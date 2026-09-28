"""Step 6 offline proving tests: CI workflow histories + dependency-bump PRs.

Every test here runs with `DATABASE_URL` unset and touches no network. The
GitHub client takes an injected `http_json_get`, and the Zenodo reader takes
already-fetched bytes, so both sources are fully exercisable offline.

What these tests are actually for
--------------------------------
Not line coverage. Each one pins a decision that would otherwise be silently
reversible by a later edit:

- that a **CC-BY-4.0 artifact is quarantined** rather than ingested (the
  allowlist working, and the single most load-bearing behaviour in step 6);
- that **nothing from step 6 is ever emitted as verified**;
- that the **Authorization header is not attached to a non-GitHub host**, and
  specifically not after a cross-host redirect;
- that a **403 secondary rate limit is retryable** while a plain 403 is a
  permission error -- the distinction that cost a live probe;
- that the **Zenodo schema is pinned** and a drifted record or a changed column
  set raises rather than ingesting shifted fields;
- that **held-out exclusion fails closed** when a design file is missing.

The fakes are hand-rolled per file, per CLAUDE.md's explicit convention that
these are deliberately not shared.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services.ingestion_sources import bot_dependency_prs as bdp
from app.services.ingestion_sources import ci_workflow_history as cwh
from app.services.ingestion_sources import workflow_knowledge as wk


# ==========================================================================
# GitHub client: auth scoping, redirect safety, rate-limit classification
# ==========================================================================

class _FakeHttp:
    """Records every (url, authed) pair and replays scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, url, authed):
        self.calls.append((url, authed))
        if not self._responses:
            return 200, {}, {"ok": True}
        return self._responses.pop(0)


def test_auth_header_never_attaches_to_a_non_github_host():
    assert bdp._auth_host_allowed("https://api.github.com/repos/a/b") is True
    assert bdp._auth_host_allowed("https://raw.githubusercontent.com/a/b") is True
    # The exact leak shape from github_corpus.py: a public non-GitHub host.
    assert bdp._auth_host_allowed("https://evil.example.com/steal") is False
    # And a lookalike that merely contains a GitHub string.
    assert bdp._auth_host_allowed("https://api.github.com.evil.test/x") is False
    assert bdp._auth_host_allowed("https://user@api.github.com.evil.test/x") is False


def test_client_refuses_a_non_github_api_url_outright():
    client = bdp._GitHubApiClient(token="t", http_json_get=_FakeHttp([]))
    with pytest.raises(bdp.BotPrError, match="refusing non-GitHub-API url"):
        client.get("https://evil.example.com/repos/a/b")


def test_client_classifies_secondary_rate_limit_as_retryable(monkeypatch):
    """A 403 whose body mentions a rate limit must NOT be a permission error.
    Live probe: four back-to-back search calls returned 403 'exceeded a
    secondary rate limit' while the documented budget still read 29/30."""
    slept: list[float] = []
    monkeypatch.setattr(bdp, "_sleep", lambda s: slept.append(s))
    http = _FakeHttp([
        (403, {"retry-after": "2"}, {"message": "You have exceeded a secondary rate limit."}),
        (200, {}, {"ok": True}),
    ])
    client = bdp._GitHubApiClient(token="t", http_json_get=http)
    body = client.get("https://api.github.com/rate_limit", is_search=True)
    assert body == {"ok": True}
    assert http.calls and len(http.calls) == 2
    assert 2.0 in slept, "must honour Retry-After before retrying"
    assert client.stats["rate_limited"] == 1


def test_client_treats_a_plain_403_as_permission_denied(monkeypatch):
    monkeypatch.setattr(bdp, "_sleep", lambda s: None)
    http = _FakeHttp([(403, {}, {"message": "Resource not accessible by personal access token"})])
    client = bdp._GitHubApiClient(token="t", http_json_get=http)
    with pytest.raises(bdp.BotPrPermissionDenied, match="not a rate limit"):
        client.get("https://api.github.com/repos/a/b")


def test_client_exhausts_retries_then_raises_rate_limited(monkeypatch):
    monkeypatch.setattr(bdp, "_sleep", lambda s: None)
    body = {"message": "You have exceeded a secondary rate limit."}
    http = _FakeHttp([(429, {}, body)] * bdp.HTTP_ATTEMPTS)
    client = bdp._GitHubApiClient(token="t", http_json_get=http)
    with pytest.raises(bdp.BotPrRateLimited, match="rate limited after"):
        client.get("https://api.github.com/search/issues", is_search=True)
    assert len(http.calls) == bdp.HTTP_ATTEMPTS


def test_client_self_throttles_before_exhausting_the_budget(monkeypatch):
    """Reserve-based: we stop at RESERVE_REMAINING rather than discovering the
    limit by being throttled."""
    slept: list[float] = []
    monkeypatch.setattr(bdp, "_sleep", lambda s: slept.append(s))
    http = _FakeHttp([(200, {"x-ratelimit-remaining": "5", "x-ratelimit-reset": "99999999999"}, {})])
    client = bdp._GitHubApiClient(token="t", http_json_get=http, reserve=100)
    client.get("https://api.github.com/rate_limit")
    assert client._reset_at > 0, "a low remaining count must arm the client-side wait"


# ==========================================================================
# Title parsing + check summarising
# ==========================================================================

@pytest.mark.parametrize("title,package,frm,to,kind", [
    ("Bump lodash from 4.17.20 to 4.17.21", "lodash", "4.17.20", "4.17.21", "bump"),
    ("Bump express from 4.18.2 to 5.0.0", "express", "4.18.2", "5.0.0", "bump"),
    ("Update dependency foo to v1.2.3", "foo", None, "1.2.3", "update"),
    # The shapes the live pilot actually returned. 26 of 30 sampled titles
    # carried a conventional-commit prefix, which the first parser missed
    # entirely and read as unparseable.
    ("chore(deps): bump pre-commit from 4.6.0 to 4.6.1 in the python-minor-and-patch group",
     "pre-commit", "4.6.0", "4.6.1", "bump"),
    ("chore(deps-dev): bump vue from 3.5.27 to 3.5.40 in /vue",
     "vue", "3.5.27", "3.5.40", "bump"),
    ("fix(deps): bump codecov/codecov-action from 5 to 7", "codecov/codecov-action", "5", "7", "bump"),
    ("build(deps): bump nixpkgs from `61b7c44` to `e2587ca` in the nix group",
     "nixpkgs", "61b7c44", "e2587ca", "bump"),
    ("chore(deps): bump esacteksab/go from 1.25.12-2026-07-17 to 1.25.12-2026-07-24",
     "esacteksab/go", "1.25.12-2026-07-17", "1.25.12-2026-07-24", "bump"),
    ("Update psycopg[binary] requirement from >=3.2 to >=3.3.4 in /backend",
     "psycopg[binary]", ">=3.2", ">=3.3.4", "update_requirement"),
    ("Bump httpx2 from 2.5.0 to 2.9.1 in /backend", "httpx2", "2.5.0", "2.9.1", "bump"),
    # A group bump with no version pair is a real upgrade, not a parse failure.
    ("chore(deps): bump the github-actions group with 3 updates",
     "github-actions", None, None, "group_no_versions"),
])
def test_bump_titles_parse(title, package, frm, to, kind):
    parsed = bdp.parse_bump_title(title)
    assert parsed["package"] == package, title
    assert parsed["from_version"] == frm, title
    assert parsed["to_version"] == to, title
    assert parsed["bump_kind"] == kind, title


def test_bump_scope_is_captured_not_dropped():
    """Which workspace a grouped bump applied to is part of when the knowledge
    is applicable, so it is recorded rather than discarded."""
    assert bdp.parse_bump_title(
        "chore(deps): bump vue from 3.5.27 to 3.5.40 in /vue")["scope"] == "/vue"
    assert bdp.parse_bump_title(
        "chore(deps): bump modernc.org/sqlite from 1.54.0 to 1.55.0 in the gomod group"
    )["scope"] == "gomod"


@pytest.mark.parametrize("title", [
    "Merge pull request #12 from user/feature",
    "chore: release 1.2.3",
    "Bump nothing at all",
])
def test_unparseable_titles_return_none_rather_than_raising(title):
    """A bot's release PR is a real, common outcome. It gets counted, not fatal."""
    parsed = bdp.parse_bump_title(title)
    assert parsed["bump_kind"] is None


def test_absent_check_runs_is_never_green():
    """A repo with no CI must not read as a passing check. This is the '42.1% of
    workflows have no test step' failure mode arriving through a second door."""
    summary = bdp.summarize_checks(None)
    assert summary.conclusion == "none"
    assert summary.green is False
    assert bdp.summarize_checks({"check_runs": []}).green is False


def test_mixed_check_runs_fold_to_failure():
    runs = {"check_runs": [
        {"conclusion": "success"}, {"conclusion": "success"}, {"conclusion": "failure"}]}
    summary = bdp.summarize_checks(runs)
    assert summary.conclusion == "failure"
    assert summary.passed == 2 and summary.failed == 1 and summary.green is False


def test_timed_out_counts_as_failure_not_success():
    runs = {"check_runs": [{"conclusion": "success"}, {"conclusion": "timed_out"}]}
    assert bdp.summarize_checks(runs).conclusion == "failure"


def test_manifest_filtering_keeps_only_dependency_files():
    paths, eco = bdp.manifest_ecosystem(
        ("package.json", "src/index.js", "README.md", "package-lock.json"))
    assert paths == ("package.json",)
    assert eco == "npm"


# ==========================================================================
# Resume semantics
# ==========================================================================

def _search_item(pr_id: int, repo: str = "acme/widget", title: str = "Bump lodash from 4.17.20 to 4.17.21"):
    return {
        "id": pr_id, "number": pr_id, "title": title,
        "repository_url": f"https://api.github.com/repos/{repo}",
        "pull_request": {"merged_at": "2026-05-01T00:00:00Z"},
    }


def test_discover_is_resumable_by_cursor(monkeypatch):
    """A run killed by a secondary rate limit restarts without re-spending."""
    monkeypatch.setattr(bdp, "_sleep", lambda s: None)
    http = _FakeHttp([(200, {}, {"items": [_search_item(10), _search_item(20), _search_item(30)]})])
    client = bdp._GitHubApiClient(token="t", http_json_get=http)
    source = bdp.BotDependencyPrSource(client=client)

    first = [c.cursor for c in source.discover(after=0)]
    assert first == [10, 20, 30]

    # Resume from 20: only newer rows come back.
    http2 = _FakeHttp([(200, {}, {"items": [_search_item(10), _search_item(20), _search_item(30)]})])
    resumed = bdp.BotDependencyPrSource(client=bdp._GitHubApiClient(token="t", http_json_get=http2))
    assert [c.cursor for c in resumed.discover(after=20)] == [30]


def test_discover_skips_non_pull_request_items(monkeypatch):
    monkeypatch.setattr(bdp, "_sleep", lambda s: None)
    issue = _search_item(5)
    issue.pop("pull_request")
    http = _FakeHttp([(200, {}, {"items": [issue, _search_item(6)]})])
    source = bdp.BotDependencyPrSource(client=bdp._GitHubApiClient(token="t", http_json_get=http))
    assert [c.cursor for c in source.discover(after=0)] == [6]


def test_constructor_performs_no_network():
    source = bdp.BotDependencyPrSource()
    assert source.source_type == bdp.SOURCE_TYPE


def test_fingerprint_ignores_check_run_drift():
    """Same PR, fetched a day apart, legitimately differs (the check list grows).
    That must not mint a new Procedure version."""
    source = bdp.BotDependencyPrSource()
    from app.services.ingestion_sources.base import SourceArtifact
    a = SourceArtifact(source_type="dependency_bump_pr", uri="u", content="x",
                       content_hash="h1", repository="a/b", path="pull/1", commit="sha")
    b = SourceArtifact(source_type="dependency_bump_pr", uri="u", content="y",
                       content_hash="h2", repository="a/b", path="pull/1", commit="sha")
    assert source.fingerprint(a) == source.fingerprint(b)


def test_fetch_requires_a_discovered_ref():
    from app.services.ingestion_sources.base import SourceRef
    source = bdp.BotDependencyPrSource()
    with pytest.raises(bdp.BotPrError, match="needs a ref from discover"):
        source.fetch(SourceRef(uri="https://github.com/a/b/pull/1", repository="a/b", path="pull/1"))


def test_status_api_is_merged_in_when_check_runs_are_absent():
    """The pilot caught this: reading only /check-runs reported `none` for 25/25
    real merged PRs, because those repos report through /commits/{sha}/status.
    A `none` that means "we looked in the wrong place" is a false negative about
    our detection, not a fact about the repository."""
    monkey_calls: list[str] = []

    class _TwoSurfaceHttp:
        def __init__(self):
            self.step = 0

        def __call__(self, url, authed):
            monkey_calls.append(url)
            if "search/issues" in url:
                return 200, {}, {"items": [_search_item(1, repo="acme/widget")]}
            if "check-runs" in url:
                return 200, {}, {"check_runs": []}
            if "/status" in url:
                return 200, {}, {"statuses": [
                    {"state": "success"}, {"state": "success"}, {"state": "failure"}]}
            if url.endswith("/files?per_page=100"):
                return 200, {}, [{"filename": "package.json"}]
            if "/pulls/" in url:
                return 200, {}, {"head": {"sha": "s" * 40}, "base": {"sha": "b" * 40},
                                 "body": "bump"}
            if url.endswith("/license"):
                return 200, {}, {"license": {"spdx_id": "MIT", "name": "MIT License"}}
            return 200, {}, {}

    client = bdp._GitHubApiClient(token="t", http_json_get=_TwoSurfaceHttp())
    source = bdp.BotDependencyPrSource(client=client)
    cursor = next(iter(source.discover(after=0)))
    artifact = source.fetch(source.ref_for(cursor))
    assert "conclusion: failure" in artifact.content, \
        "a failure on the status API must not be reported as `none`"
    assert any("/status" in c for c in monkey_calls), "the status API must be consulted"


def test_both_surfaces_empty_reports_none():
    assert bdp._merge_commit_states([]) == "none"
    assert bdp._merge_commit_states(None) == "none"
    assert bdp._merge_commit_states([{"state": "success"}]) == "success"
    assert bdp._merge_commit_states([{"state": "success"}, {"state": "error"}]) == "failure"


def test_per_repository_license_is_resolved_and_cached(monkeypatch):
    """Step 6 requires license per repository via the allowlist, and one
    `/license` call per repo rather than per PR."""
    monkeypatch.setattr(bdp, "_sleep", lambda s: None)
    calls: list[str] = []

    class _Http:
        def __call__(self, url, authed):
            calls.append(url)
            if "search/issues" in url:
                return 200, {}, {"items": [
                    _search_item(1, repo="acme/widget"),
                    _search_item(2, repo="acme/widget"),
                    _search_item(3, repo="acme/widget"),
                ]}
            if url.endswith("/license"):
                return 200, {}, {"license": {"spdx_id": "Apache-2.0", "name": "Apache"}}
            if url.endswith("/files?per_page=100"):
                return 200, {}, [{"filename": "go.mod"}]
            if "/pulls/" in url:
                return 200, {}, {"head": {"sha": "s" * 40}, "base": {"sha": "b" * 40},
                                 "body": ""}
            if "check-runs" in url:
                return 200, {}, {"check_runs": [{"conclusion": "success"}]}
            if "/status" in url:
                return 200, {}, {"statuses": []}
            return 200, {}, {}

    source = bdp.BotDependencyPrSource(
        client=bdp._GitHubApiClient(token="t", http_json_get=_Http()))
    cursors = list(source.discover(after=0))
    assert len(cursors) == 3, "discover must yield every item before fetch resolves any"
    for cursor in cursors:
        artifact = source.fetch(source.ref_for(cursor))
        assert artifact.license_metadata["spdx_id"] == "Apache-2.0"
    assert len([c for c in calls if c.endswith("/license")]) == 1, \
        "license must be cached per repo, not fetched per PR"


def test_unlicensed_repo_is_quarantined_not_allowed(monkeypatch):
    """A repo whose license endpoint 404s must not read as permissive."""
    monkeypatch.setattr(bdp, "_sleep", lambda s: None)

    class _Http:
        def __call__(self, url, authed):
            if "search/issues" in url:
                return 200, {}, {"items": [_search_item(1, repo="acme/widget")]}
            if url.endswith("/license"):
                return 404, {}, {"message": "Not Found"}
            if url.endswith("/files?per_page=100"):
                return 200, {}, [{"filename": "package.json"}]
            if "/pulls/" in url:
                return 200, {}, {"head": {"sha": "s" * 40}, "base": {"sha": "b" * 40}}
            if "check-runs" in url:
                return 200, {}, {"check_runs": []}
            if "/status" in url:
                return 200, {}, {"statuses": []}
            return 200, {}, {}

    source = bdp.BotDependencyPrSource(
        client=bdp._GitHubApiClient(token="t", http_json_get=_Http()))
    cursor = next(iter(source.discover(after=0)))
    artifact = source.fetch(source.ref_for(cursor))
    assert artifact.license_metadata["spdx_id"] is None
    decision, _reason = wk.gate_license(artifact)
    assert decision == "QUARANTINE"


def test_ci_evidence_type_is_a_witness_not_a_promotion_path():
    """The single most important assertion in this file.

    execution/evidence.py's REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED is
    ('execution_result', 'reproduction'). An externally observed CI run is a
    host self-report, so it must not use either -- otherwise a green GitHub
    Actions run would promote a Procedure to verified.
    """
    from app.execution import evidence as ev

    assert bdp.CI_EVIDENCE_TYPE == "experiment"
    assert bdp.CI_EVIDENCE_TYPE not in ev.REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED


# ==========================================================================
# Zenodo workflow corpus: schema pinning
# ==========================================================================

_HEADER = ",".join(cwh.EXPECTED_COLUMNS)


def _csv_row(**over):
    row = {
        "repository": "acme/widget", "commit_hash": "a" * 40,
        "author_name": "Dev", "author_email": "dev@example.com",
        "committer_name": "Dev", "committer_email": "dev@example.com",
        "committed_date": "1700000000", "authored_date": "1700000000",
        "file_path": ".github/workflows/ci.yml", "previous_file_path": "",
        "file_hash": "b" * 64, "previous_file_hash": "",
        "git_change_type": "M", "valid_yaml": "True",
        "probably_workflow": "True", "valid_workflow": "True",
        "uid": "acme/widget/.github/workflows/ci.yml/" + "c" * 40,
    }
    row.update(over)
    return ",".join(str(row[c]) for c in cwh.EXPECTED_COLUMNS)


def test_metadata_parses_the_verified_seventeen_columns():
    text = _HEADER + "\n" + _csv_row() + "\n"
    revisions = list(cwh.iter_metadata_csv(text.encode()))
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev.repository == "acme/widget"
    assert rev.file_path == ".github/workflows/ci.yml"
    assert rev.git_change_type == "M"
    assert rev.is_addition is False
    assert rev.committed_date is not None


def test_schema_drift_raises_rather_than_ingesting_shifted_fields():
    """A changed column set means every commit attribution and license decision
    downstream is wrong. Refuse."""
    bad = "repository,commit_hash,file_path,NEW_COLUMN\n" + "a,b,c,d\n"
    with pytest.raises(cwh.SchemaDrift, match="expected 17 columns"):
        list(cwh.iter_metadata_csv(bad.encode()))


def test_record_fingerprint_drift_raises():
    with pytest.raises(cwh.FingerprintDrift, match="differs from the pinned"):
        list(cwh.iter_metadata_csv((_HEADER + "\n").encode(), record_id="99999999"))


def test_non_workflow_paths_are_dropped():
    text = _HEADER + "\n" + _csv_row(file_path="src/main.py") + "\n"
    assert list(cwh.iter_metadata_csv(text.encode())) == []


def test_addition_is_distinguished_from_modification():
    text = _HEADER + "\n" + _csv_row(git_change_type="A") + "\n"
    assert list(cwh.iter_metadata_csv(text.encode()))[0].is_addition is True


def test_gzipped_metadata_is_read(tmp_path):
    import gzip
    payload = gzip.compress((_HEADER + "\n" + _csv_row() + "\n").encode())
    assert len(list(cwh.iter_metadata_csv(payload))) == 1


def test_truncated_gzip_prefix_still_yields_clean_rows():
    """A ranged probe of a 311 MB file yields a partial member. Whatever decoded
    cleanly must be usable; the torn tail is dropped, not guessed at."""
    import gzip
    rows = "\n".join(_csv_row(uid=f"uid-{i}") for i in range(6))
    full = gzip.compress((_HEADER + "\n" + rows + "\n").encode())
    truncated = full[: int(len(full) * 0.92)]
    revisions = list(cwh.iter_metadata_csv(truncated))
    assert 1 <= len(revisions) < 6, "some rows survive, and the torn tail is dropped"
    assert all(r.repository == "acme/widget" for r in revisions)


def test_workflow_summary_reads_structure_without_a_yaml_parse():
    body = "name: CI\non:\n  push:\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n      - run: pytest\n"
    summary = cwh.summarize_workflow(body, filename="ci.yml")
    assert summary.name == "CI"
    assert "build" in summary.jobs
    assert summary.has_test_step is True
    assert any("checkout" in u for u in summary.uses)


def test_unparseable_yaml_still_summarises():
    """~23% of corpus snapshots fail YAML parse. The content is still useful
    candidate material, so it must not be dropped."""
    broken = "name: CI\njobs:\n  build:\n   steps:\n  - run: [unclosed\n"
    summary = cwh.summarize_workflow(broken)
    assert summary.name == "CI"


def test_artifact_states_it_is_unverified():
    data = (_HEADER + "\n" + _csv_row() + "\n").encode()
    source = cwh.CiWorkflowHistorySource(data)
    ref = next(source.discover())
    artifact = source.fetch(ref)
    assert "UNVERIFIED" in artifact.content
    assert "no run outcome" in artifact.content
    assert artifact.license_metadata["spdx_id"] == "CC-BY-4.0"


# ==========================================================================
# License gate: the load-bearing refusal
# ==========================================================================

def test_cc_by_4_compilation_license_is_provenance_and_the_item_license_still_gates():
    """Founder ruling 2026-09-29: CC-BY-4.0 is on DEFAULT_ALLOWLIST (with attribution, and removable as a class).
    That does NOT admit every step-6 workflow artifact: the CC-BY-4.0 on the Zenodo record covers the compilation,
    not the third-party files inside it, so each item is still gated on ITS repository's license, and an
    unresolved one is still QUARANTINE."""
    from app.services.repo_license_policy import DEFAULT_ALLOWLIST, attribution_required, classify_spdx

    assert "CC-BY-4.0" in DEFAULT_ALLOWLIST and attribution_required("CC-BY-4.0")
    assert classify_spdx("CC-BY-4.0").decision == "ALLOW"
    assert classify_spdx("CC-BY-SA-4.0").decision == "QUARANTINE"      # share-alike stays out

    data = (_HEADER + "\n" + _csv_row() + "\n").encode()
    source = cwh.CiWorkflowHistorySource(data)
    artifact = source.fetch(next(source.discover()))
    decision, reason = wk.gate_license(artifact)
    assert decision == "QUARANTINE"
    assert "not resolvable" in (reason or "")


def test_gate_records_a_reason_so_counts_are_auditable():
    from app.services.ingestion_sources.base import SourceArtifact
    artifact = SourceArtifact(
        source_type="ci_workflow_history", uri="u", content="x", content_hash="h",
        license_metadata={"spdx_id": "CC-BY-4.0"})
    _decision, reason = wk.gate_license(artifact)
    assert reason, "a quarantine must carry a reason string for the report"


# ==========================================================================
# Compilation: candidate-only, never verified
# ==========================================================================

def _bump_artifact(**kw):
    from app.services.ingestion_sources.base import SourceArtifact
    content = "\n".join([
        "# dependency bump: lodash",
        "repository: acme/widget",
        "ecosystem: npm",
        "from_version: 4.17.20",
        "to_version: 4.17.21",
        "changed_manifests: package.json",
        "",
        "## observed CI verdict",
        "conclusion: success",
        "checks_total: 3",
        "checks_passed: 3",
        "checks_failed: 0",
        "",
        "## verification honesty",
        "This CI result was observed from outside this system.",
    ]) + "\n"
    return SourceArtifact(
        source_type="dependency_bump_pr", uri="https://github.com/acme/widget/pull/1",
        content=content, content_hash="h", repository="acme/widget",
        path="pull/1", commit="c" * 40, **kw)


def test_goal_proposal_is_deterministic_and_costs_nothing():
    name, description = wk.propose_goal(_bump_artifact())
    assert name == "upgrade lodash in npm projects"
    assert "externally observed CI" in description
    # Same input, same output -- no LLM, no spend, reproducible.
    assert wk.propose_goal(_bump_artifact()) == (name, description)


def test_bump_preconditions_name_the_version_the_bump_targets():
    parsed = wk.parse_artifact_document(_bump_artifact().content)
    pre = wk.propose_preconditions(_bump_artifact(), parsed)
    assert any("4.17.20" in p for p in pre), "must pin the from-version or it is not applicable knowledge"
    assert any("npm" in p for p in pre)


def test_bump_failure_conditions_name_the_real_post_merge_breakage():
    parsed = wk.parse_artifact_document(_bump_artifact().content)
    failures = wk.propose_failure_conditions(_bump_artifact(), parsed)
    assert any("migration" in f for f in failures)
    assert any("transitive" in f for f in failures)


def test_artifact_round_trips_through_its_rendered_document():
    parsed = wk.parse_artifact_document(_bump_artifact().content)
    assert parsed["package"] == "lodash"
    assert parsed["from_version"] == "4.17.20"
    assert parsed["to_version"] == "4.17.21"
    assert parsed["ecosystem"] == "npm"


def test_stub_artifact_is_rejected_by_shape():
    assert wk.artifact_is_substantive("x") is False
    assert wk.artifact_is_substantive(_bump_artifact().content) is True


def test_compile_provenance_is_candidate_not_verified():
    """Nothing from step 6 may claim a verified provenance value.

    Checked behaviourally against the constants the write path actually uses,
    rather than by grepping the source for a string -- a grep matches the
    docstring that explains why the string is absent, which is a test that
    passes or fails for the wrong reason.
    """
    from app.services.ingestion_sources.workflow_knowledge import (  # noqa: F401
        CANDIDATE_PROVENANCE,
    )

    assert wk.CANDIDATE_PROVENANCE == "system_pending_review"
    assert "verified" not in wk.CANDIDATE_PROVENANCE

    # The Procedure write path's own return value declares the state, and the
    # outcome vocabulary has no "verified" member to accidentally reach.
    assert wk.OUTCOME_CANDIDATE == "candidate"
    for name in dir(wk):
        if name.startswith("OUTCOME_"):
            assert "verified" not in getattr(wk, name)
    # And no outcome verb lets a caller mint a promotion.
    assert not hasattr(wk, "mark_verified")
    assert not hasattr(wk, "promote")


def test_verified_procedures_require_evidence_we_do_not_claim():
    """A step-6 CI verdict is not an evidence type that can promote anything."""
    from app.execution import evidence as ev
    assert wk.CANDIDATE_PROVENANCE != "verified"
    assert bdp.CI_EVIDENCE_TYPE not in ev.REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED


def test_locator_carries_scope_and_provenance():
    locator = wk.build_locator(_bump_artifact())
    assert locator["source_id"] == "dependency_bump_pr"
    assert locator["commit"] == "c" * 40
    assert locator["content_hash"] == "h"
    assert locator["uri"].endswith("/pull/1")


# ==========================================================================
# Held-out exclusion
# ==========================================================================

def _write_design(root, rel, *, test_ids, calibration_ids=(), repos=()):
    """Write ONE design file and an empty sibling, so a test that cares about a
    single design still satisfies load_held_out's fail-closed contract."""
    for target in (rel, "experiments/swebench_rebench/runs/design.json"):
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        if target == rel:
            payload = {
                "dataset": "princeton-nlp/SWE-bench_Verified", "dataset_revision": "abc",
                "scored_repos": list(repos), "train": ["t1"], "test": list(test_ids),
                "calibration": list(calibration_ids),
            }
        else:
            payload = {"dataset": "nebius/SWE-rebench", "dataset_revision": "def",
                       "scored_repos": [], "train": [], "test": [], "calibration": []}
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_held_out_unions_test_and_calibration(tmp_path):
    from app.services.ingestion_sources.held_out import load_held_out
    _write_design(tmp_path, "experiments/swebench/runs/design.json",
                  test_ids=["a__a-1"], calibration_ids=["b__b-2"])
    held = load_held_out(tmp_path)
    assert held.is_held_out("a__a-1") and held.is_held_out("b__b-2")
    assert len(held) == 2


def test_held_out_fails_closed_when_a_design_is_missing(tmp_path):
    """A missing design must raise, not return an empty set. An empty set reads
    as 'nothing is held out' -- the exact silent failure this guards."""
    from app.services.ingestion_sources.held_out import HeldOutUnavailable, load_held_out
    with pytest.raises(HeldOutUnavailable, match="fails closed|fail closed|missing"):
        load_held_out(tmp_path)


def test_held_out_allow_missing_is_dry_run_only(tmp_path):
    from app.services.ingestion_sources.held_out import load_held_out
    held = load_held_out(tmp_path, allow_missing=True)
    assert len(held.missing) == 2
    assert len(held) == 0


def test_held_out_covers_scored_repos(tmp_path):
    from app.services.ingestion_sources.held_out import load_held_out
    _write_design(tmp_path, "experiments/swebench/runs/design.json",
                  test_ids=[], repos=["django/django"])
    held = load_held_out(tmp_path)
    assert held.is_held_out("django/django")
    assert held.is_held_out("django/django@abc123")
    assert held.is_held_out("django/django/pull/1")


def test_held_out_splits_kept_and_excluded_and_counts(tmp_path):
    from app.services.ingestion_sources.held_out import load_held_out
    _write_design(tmp_path, "experiments/swebench/runs/design.json", test_ids=["a__a-1"])
    held = load_held_out(tmp_path)
    kept, excluded = held.filter(["a__a-1", "z__z-9"])
    assert kept == ["z__z-9"] and excluded == ["a__a-1"]


def test_held_out_report_hashes_the_design(tmp_path):
    from app.services.ingestion_sources.held_out import load_held_out
    _write_design(tmp_path, "experiments/swebench/runs/design.json", test_ids=["a__a-1"])
    report = load_held_out(tmp_path).as_dict()
    assert report["excluded_ids"] == 1
    assert len(report["designs"][0]["sha256"]) == 64
    assert report["designs"][0]["dataset_revision"] == "abc"


def test_held_out_rejects_malformed_design(tmp_path):
    from app.services.ingestion_sources.held_out import HeldOutUnavailable, load_design
    path = tmp_path / "design.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HeldOutUnavailable, match="not valid JSON"):
        load_design(path)


# ==========================================================================
# CLI surface
# ==========================================================================

def test_step6_commands_are_registered():
    import argparse

    from app.ingestion import step6_admin
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    step6_admin.add_parsers(sub)
    for cmd in step6_admin.COMMANDS:
        assert sub.choices[cmd] is not None
    assert step6_admin.COMMANDS == ("step6-ci-workflows", "step6-bot-prs", "step6-held-out")


def test_override_note_labels_the_run_as_a_policy_override():
    from app.ingestion.step6_admin import CC_BY_OVERRIDE_NOTE
    assert "POLICY OVERRIDE" in CC_BY_OVERRIDE_NOTE
    assert "DEFAULT_ALLOWLIST is unchanged" in CC_BY_OVERRIDE_NOTE
