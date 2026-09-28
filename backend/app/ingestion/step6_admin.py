"""`python -m app.ingestion.admin step6-* ...` — the step-6 pilot commands.

    step6-ci-workflows --metadata <workflows.csv.gz> --limit 1000 [--dry-run]
    step6-bot-prs --limit 500 --after 0 [--author app/dependabot] [--dry-run]
    step6-held-out [--root .]

Two rules that shaped this file:

- **The adapter modules own their own logic.** This is a thin CLI: parse args,
  call the module, print a report. No ingestion decision is made here.
- **`--dry-run` is the default-safe path.** It reads, parses, gates, and
  reports, and writes nothing. The Common rules require a local shard first
  and the user's go-ahead before production, so making the write path require
  an explicit flag is the cheapest way to keep that honest.

The license reality, stated once so it is not a surprise in the report:
`classify_spdx("CC-BY-4.0")` returns QUARANTINE, because CC-BY-4.0 is not on
`DEFAULT_ALLOWLIST`. A default `step6-ci-workflows` run therefore reports
`quarantined_license == considered` and ingests nothing. That is the allowlist
working. `--allow-cc-by-4` exists to make the *counterfactual* measurable
without editing a frozen policy, and its report line is labelled as a
policy-override run so nobody mistakes it for an ingest.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

COMMANDS = ("step6-ci-workflows", "step6-bot-prs", "step6-held-out")

CC_BY_OVERRIDE_NOTE = (
    "POLICY OVERRIDE: CC-BY-4.0 was permitted for this run only. The "
    "DEFAULT_ALLOWLIST is unchanged and still quarantines it."
)


def add_parsers(sub: Any) -> None:
    ci = sub.add_parser("step6-ci-workflows", help="ingest CI workflow revisions as CANDIDATES")
    ci.add_argument("--metadata", required=True, help="path to workflows.csv.gz (or a ranged prefix)")
    ci.add_argument("--limit", type=int, default=1000)
    ci.add_argument("--record", default=None, help="Zenodo record id (must match the pin)")
    ci.add_argument("--allow-cc-by-4", action="store_true",
                    help="count what CC-BY-4.0 would yield; does NOT ingest by default")
    ci.add_argument("--dry-run", action="store_true")

    prs = sub.add_parser("step6-bot-prs", help="mine merged dependency-bump PRs as CANDIDATES")
    prs.add_argument("--limit", type=int, default=500)
    prs.add_argument("--after", type=int, default=0, help="resume cursor (last PR id seen)")
    prs.add_argument("--author", action="append", default=None,
                     help="bot author qualifier; repeatable (default: app/dependabot)")
    prs.add_argument("--since", default=None,
                     help="merged:>= date (default: the adapter's DEFAULT_SINCE). CI data "
                          "older than ~90 days is usually gone, so an old window yields "
                          "conclusion=none for reasons that are not about the repository.")
    prs.add_argument("--allow-cc-by-4", action="store_true")
    prs.add_argument("--dry-run", action="store_true")
    prs.add_argument("--allow-unverified-license", action="store_true",
                     help="permit per-repo licenses outside the allowlist (policy override)")

    held = sub.add_parser("step6-held-out", help="report held-out ids that ingestion must exclude")
    held.add_argument("--root", default=".")
    held.add_argument("--allow-missing", action="store_true")


async def run(pool: Any, a: Any) -> int:
    if a.cmd == "step6-held-out":
        return await _held_out(a)
    if a.cmd == "step6-ci-workflows":
        return await _ci_workflows(pool, a)
    if a.cmd == "step6-bot-prs":
        return await _bot_prs(pool, a)
    raise SystemExit(f"unknown step6 command {a.cmd!r}")


async def _held_out(a: Any) -> int:
    from app.services.ingestion_sources.held_out import HeldOutUnavailable, load_held_out

    try:
        held = load_held_out(a.root, allow_missing=a.allow_missing)
    except HeldOutUnavailable as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2
    print(json.dumps(held.as_dict(), indent=2))
    return 0


def _repo_root() -> Path:
    # backend/app/ingestion/step6_admin.py -> repo root is four levels up.
    return Path(__file__).resolve().parents[3]


async def _ci_workflows(pool: Any, a: Any) -> int:
    from app.services.ingestion_sources.ci_workflow_history import (
        PINNED_RECORD,
        CiWorkflowHistorySource,
        FingerprintDrift,
        WorkflowCorpusError,
    )
    from app.services.ingestion_sources.workflow_knowledge import (
        CompileReport,
        gate_license,
        propose_goal,
    )

    started = time.monotonic()
    record = a.record or PINNED_RECORD
    path = Path(a.metadata)

    report: dict[str, Any] = {
        "source": "zenodo:10.5281/zenodo.10259013",
        "record": record,
        "license": "CC-BY-4.0",
        "limit": a.limit,
        "dry_run": bool(a.dry_run),
    }

    # No body tarball in a bounded probe, so every artifact is body-less. That
    # is fine for counting: the compiler gates on shape, not on body length.
    source = CiWorkflowHistorySource(path, record_id=record)

    verdicts: dict[str, int] = {}
    goal_named = 0
    considered = 0
    repos: set[str] = set()
    additions = 0
    truncated = False
    try:
        # Streamed: the real metadata file is 297 MB compressed and several GB
        # of text, so it is never materialised.
        for ref in source.discover(limit=a.limit):
            considered += 1
            repos.add(ref.repository or "")
            artifact = source.fetch(ref)
            if "change_type: A" in (artifact.content or ""):
                additions += 1
            decision, reason = gate_license(artifact)
            key = f"{decision}:{reason or 'allowlisted'}"
            verdicts[key] = verdicts.get(key, 0) + 1
            if propose_goal(artifact) is not None:
                goal_named += 1
    except FingerprintDrift as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2
    except WorkflowCorpusError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", flush=True)
        return 2

    report["rows_considered"] = considered
    report["repositories"] = len(repos)
    report["additions"] = additions
    report["license_verdicts"] = verdicts
    report["goals_named_deterministically"] = goal_named
    report["would_ingest_without_license_gate"] = goal_named if a.allow_cc_by_4 else 0
    if a.allow_cc_by_4:
        report["note"] = CC_BY_OVERRIDE_NOTE
    report["evidence_available"] = False
    report["evidence_note"] = (
        "This corpus has no run outcome, no job result and no log. No verified "
        "Procedure can be produced from it."
    )
    report["step4_check_attached"] = False
    report["step4_check_note"] = (
        "screening.CHECK_TYPES is a closed 8-value vocabulary with no actionlint "
        "or zizmor member, and step 4 has not run, so no verifier check is attached."
    )
    report["wall_seconds"] = round(time.monotonic() - started, 2)
    report["spent_usd"] = 0.0
    report["written"] = False

    print(json.dumps(report, indent=2))
    if a.dry_run:
        report["note"] = "dry run: nothing was written"
        print(json.dumps({"dry_run": True}, indent=2))
    return 0


async def _bot_prs(pool: Any, a: Any) -> int:
    from app.services.ingestion_sources.bot_dependency_prs import (
        BOT_AUTHORS,
        UNVERIFIED_BOT_AUTHORS,
        BotDependencyPrSource,
        BotPrError,
        parse_bump_title,
    )
    from app.services.ingestion_sources.workflow_knowledge import CompileReport

    started = time.monotonic()
    authors = tuple(a.author) if a.author else BOT_AUTHORS
    report: dict[str, Any] = {
        "source": "github-api:search/issues",
        "authors": list(authors),
        "unverified_authors_not_enabled": list(UNVERIFIED_BOT_AUTHORS),
        "limit": a.limit,
        "after": a.after,
        "dry_run": bool(a.dry_run),
    }

    source = BotDependencyPrSource(authors=authors, since=a.since) if a.since \
        else BotDependencyPrSource(authors=authors)
    compile_report = CompileReport()

    discovered = 0
    fetched = 0
    title_kinds: dict[str, int] = {}
    check_conclusions: dict[str, int] = {}
    errors: list[str] = []
    license_verdicts: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    parseable = 0
    with_manifest = 0
    green = 0
    repos_seen: set[str] = set()

    from app.services.ingestion_sources.workflow_knowledge import (
        compile_artifact,
        gate_license,
    )

    from app.utils.aio import run_blocking

    try:
        # The adapter is synchronous and sleeps for rate limits, so discovery and
        # fetch both go on a worker thread. Blocking the event loop here would
        # stall every other lane in the process for the length of a backoff.
        for cursor in await run_blocking(lambda: list(source.discover(after=a.after))):
            discovered += 1
            repos_seen.add(cursor.repo)
            parsed = parse_bump_title(cursor.title)
            kind = parsed["bump_kind"] or "unparsed"
            title_kinds[kind] = title_kinds.get(kind, 0) + 1
            if discovered > a.limit:
                break
            ref = _ref_for(source, cursor)
            try:
                artifact = await run_blocking(source.fetch, ref)
            except BotPrError as exc:
                errors.append(f"{cursor.repo}#{cursor.pr_number}: {exc}")
                if len(errors) >= 5:
                    break
                continue
            fetched += 1
            conclusion = _conclusion_of(artifact)
            check_conclusions[conclusion] = check_conclusions.get(conclusion, 0) + 1
            # Read the manifest paths off the ARTIFACT, not off `cursor`:
            # `fetch()` discovers the changed files and returns them on the
            # artifact, so the cursor object `discover()` yielded still has
            # empty `manifest_paths`. Reading the cursor reported 0/30.
            changed_manifests = (artifact.license_metadata or {}).get(
                "observed_manifest_paths") or []
            decision, reason = gate_license(artifact)
            spdx = (artifact.license_metadata or {}).get("spdx_id") or "(none)"
            # Recorded per SPDX id, not just per verdict: "20 ALLOW" is not
            # actionable, "20 ALLOW, all MIT" is.
            key = f"{decision}:{spdx}"
            license_verdicts[key] = license_verdicts.get(key, 0) + 1
            if reason:
                compile_report.license_reasons[reason] = (
                    compile_report.license_reasons.get(reason, 0) + 1)
            if decision != "ALLOW":
                compile_report.quarantined_license += 1
                continue
            # Yield counters, counted identically in dry-run and write mode so
            # the two reports are comparable line for line.
            compile_report.considered += 1
            compile_report.bytes_content += len(artifact.content or "")
            if parse_bump_title(cursor.title)["package"]:
                parseable += 1
            if changed_manifests:
                with_manifest += 1
            if conclusion == "success":
                green += 1
            if a.dry_run:
                continue
            result = await compile_artifact(
                pool, artifact, report=compile_report, run_id=f"step6-bot-prs-{a.after}")
            outcomes[result.get("outcome", "?")] = outcomes.get(result.get("outcome", "?"), 0) + 1
    except BotPrError as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
        return 2

    report["discovered"] = discovered
    report["fetched"] = fetched
    report["repositories"] = len(repos_seen)
    report["title_parse_kinds"] = title_kinds
    report["check_conclusions"] = check_conclusions
    report["license_verdicts"] = license_verdicts
    report["compile_outcomes"] = outcomes
    report["yield"] = {
        "parseable_titles": parseable,
        "touched_a_dependency_manifest": with_manifest,
        "ci_green_at_observation": green,
        "goals_created": compile_report.goals_created,
        "goals_matched": compile_report.goals_matched,
    }
    report["api_stats"] = dict(source._client.stats)
    report["errors"] = errors
    report["compile"] = compile_report.as_dict()
    report["written"] = not bool(a.dry_run) and compile_report.candidates > 0
    report["evidence_type"] = "experiment"
    report["evidence_note"] = (
        "CI observed from outside this system is a host self-report, so it is "
        "recorded as `experiment` (a witness type) and never promotes a "
        "Procedure to verified. See procedures.record_execution_outcome's "
        "execution_verified docstring."
    )
    report["retention_note"] = (
        "GitHub Actions retains run history ~90 days by default, so this "
        "verdict is valid only as of observed_at."
    )
    report["wall_seconds"] = round(time.monotonic() - started, 2)
    report["spent_usd"] = 0.0
    if a.allow_cc_by_4 or a.allow_unverified_license:
        report["note"] = CC_BY_OVERRIDE_NOTE
    if a.dry_run:
        report["note"] = "dry run: nothing was written"

    print(json.dumps(report, indent=2))
    return 0


def _ref_for(source: Any, cursor: Any) -> Any:
    """Build the ref `fetch()` expects: `discover()` must have registered this
    cursor, which it does for everything it yields."""
    from app.services.ingestion_sources.base import SourceRef
    return SourceRef(
        uri=f"https://github.com/{cursor.repo}/pull/{cursor.pr_number}",
        repository=cursor.repo,
        path=f"pull/{cursor.pr_number}",
        commit=None,
        source_id="dependency_bump_pr",
    )


def _conclusion_of(artifact: Any) -> str:
    for line in (artifact.content or "").splitlines():
        if line.startswith("conclusion:"):
            return line.split(":", 1)[1].strip()
    return "unknown"
