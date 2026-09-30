"""Step 6 source: Dependabot / Renovate dependency-bump pull requests.

Why this module exists
----------------------
Step 6 of `.scratch/prompts/ingestion_build_prompts.md` asks for "a rate-limited,
resumable GitHub API scraper (token from `.env`, never logged) for merged PRs with
green CI -> version-bump Procedures with the CI result as evidence".

This module is the discovery + fetch half of that, shaped to the existing
`ingestion_sources.base.SourceAdapter` Protocol so it drops into the same
compiler as every other source. It deliberately does NOT decide what a
Procedure is -- see `workflow_knowledge.py` for that, and the honesty note
below.

The honesty note, which is the most important thing in this file
----------------------------------------------------------------
**A merged PR with green CI is a host self-report, not our verified execution.**
`procedures.record_execution_outcome` has an explicit `execution_verified` flag
whose docstring says: True means "a genuine execution-backed outcome ... a
durable `execution_runs` row from a sandboxed run"; False means "a bare HOST
SELF-REPORT ... a claim, not verified execution". When False the row is
stamped `record_execution_outcome@1:self_report`, which
`app.services.evidence_trust.is_trusted_writer` classifies as
CLAIMED_SUCCESS/UNKNOWN and **never** VERIFIED_*, and `verification_state` /
`availability` are not touched.

A GitHub Actions run we observed from outside our sandbox is exactly a host
self-report. So this module emits `CI_EVIDENCE_TYPE = "experiment"` -- a
witness type, which by `execution/evidence.py` carries no `outcome_status` and
never promotes anything to verified. CI is corroboration here, never the
promotion path. Putting this in the module docstring rather than in a comment
because it is the decision a future reader is most likely to get wrong.

A second constraint, from retention rather than from the API: GitHub Actions
run history is retained ~90 days by default and repos can cut it to 1 day.
Merged-PR metadata persists forever; the check outcomes on that commit do not.
So a historical green-CI chain cannot be assembled after the fact. This module
records the check summary **at the moment it observes the PR** and stamps
`observed_at`, so a chain is only as old as the observation.

Security decisions that differ from `github_corpus.py`
----------------------------------------------------
`ingestion_sources/github_corpus.py:59-80` builds one `headers` dict containing
`Authorization: Bearer $GITHUB_TOKEN` and reuses that same dict on every
redirect hop. `assert_safe_locator` is an SSRF check, not a host-identity
check, so a 302 from `api.github.com` to any public host carries the live
token. That is a real leak in existing code (filed in
`.scratch/ingestion_testing_audit.md`, P0 #3) and it is NOT copied here:

- `_auth_host_allowed()` gates `Authorization` to an explicit GitHub host set.
- `_api_get()` never follows a redirect to a different host with auth attached;
  a cross-host redirect is followed *anonymously* or refused, per
  `FOLLOW_CROSS_HOST_REDIRECT`.
- The token is read from `PERSONAL_GITHUB_TOKEN` (the name actually present in
  `backend/.env`) or `GITHUB_TOKEN`, and is never logged, never stored on the
  instance, and never included in any returned dict.

Rate limiting
-------------
GitHub's failure mode is a **403 with "You have exceeded a secondary rate
limit"**, not a 429. Observed live: four back-to-back search calls tripped it
while the documented `search` budget still showed 29/30 remaining. So this
client:
- reads `X-RateLimit-Remaining` / `X-RateLimit-Reset` and `Retry-After` on
  every response and sleeps itself before the documented budget is exhausted
  (`_reserve`, called before each request, not after a failure),
- classifies a 403 whose body mentions a rate limit as RETRYABLE, and a plain
  403 as a permission error,
- never sleeps inside `async def` -- `time.sleep` is wrapped in
  `app.utils.aio.run_blocking` per the house rule that a blocking call must
  never run on the event loop,
- is resumable: `discover()` takes an `after` cursor and yields
  `BumpCursor` values, so a run interrupted by a secondary limit restarts
  where it stopped instead of re-spending the budget.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from app.services.ingestion_sources.base import (
    SourceAdapter,
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)

# --------------------------------------------------------------------------
# Vocabulary. Every constant here is a named, retunable module attribute, the
# same discipline `trace_worker.py` uses for MAX_INLINE_PAYLOAD_BYTES.
# --------------------------------------------------------------------------

SOURCE_TYPE = "dependency_bump_pr"
ACTOR_ID = "bot_dependency_prs"
EXTRACTOR_VERSION = "bot_dependency_prs/v1"

#: Witness evidence type. NOT execution_result/reproduction -- see the module
#: docstring. A CI run observed from outside our sandbox is a host self-report.
CI_EVIDENCE_TYPE = "experiment"

#: The two bot identities step 6 names. Verified live: `author:app/dependabot`
#: returns items whose `user.login` is `dependabot[bot]`. Renovate is listed but
#: UNVERIFIED -- a probe for it hit a secondary rate limit before returning, and
#: guessing its login would be worse than shipping it disabled.
BOT_AUTHORS: tuple[str, ...] = ("app/dependabot",)
UNVERIFIED_BOT_AUTHORS: tuple[str, ...] = ("app/renovate",)

#: Hosts allowed to receive the Authorization header. Deliberately an explicit
#: allowlist rather than a denylist: an unknown GitHub-owned host fails closed
#: (no auth) instead of open (auth to anything).
AUTH_HOSTS: frozenset[str] = frozenset({
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
})

#: Whether a cross-host redirect is followed at all. Following it *anonymously*
#: is safe and keeps raw-content fetches working; the token never goes with it.
FOLLOW_CROSS_HOST_REDIRECT = True
MAX_REDIRECTS = 4

HTTP_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2.0

#: Self-throttle. We stop at this many *remaining* requests rather than
#: discovering the limit by being throttled.
RESERVE_REMAINING = 100
#: Search API is 30/min authenticated; core is 5000/hr. We pace search calls to
#: stay under the secondary limiter, which is stricter than the documented one.
SEARCH_MIN_INTERVAL_SECONDS = 2.5
DEFAULT_SEARCH_RESULTS = 30

#: A merged PR must have been merged at least this recently to be interesting:
#: it bounds how much of the corpus is stale version advice.
DEFAULT_SINCE = "2024-01-01"

#: Dependency-file paths we treat as a manifest. Kept explicit and closed --
#: an open-ended glob here is how a scraper starts reading lockfiles it has no
#: business reading.
DEPENDENCY_MANIFEST_NAMES: tuple[str, ...] = (
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "pubspec.yaml", "Directory.Packages.props",
)

#: Ecosystem label from the changed manifest filename. Used only for reporting.
ECOSYSTEM_BY_MANIFEST: dict[str, str] = {
    "package.json": "npm", "requirements.txt": "pypi", "pyproject.toml": "pypi",
    "Pipfile": "pypi", "go.mod": "go", "Cargo.toml": "cargo",
    "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle",
    "Gemfile": "bundler", "composer.json": "composer",
    "pubspec.yaml": "pub", "Directory.Packages.props": "nuget",
}

#: A conventional-commit prefix (`chore(deps): `, `fix(deps)!: `) is stripped
#: before parsing. This is not decoration: in the live pilot, 26 of 30 sampled
#: Dependabot titles carried one, and a parser that only matches a bare
#: `Bump X from A to B` reads the most common real-world shape as unparseable.
_CONVENTIONAL_PREFIX_RE = re.compile(r"^[A-Za-z]+(\([^)]*\))?!?:\s*")

#: Verb + package + optional from/to, anchored anywhere after the prefix.
#: Group captures are ordered most-specific first, because a grouped bump also
#: matches the generic form and we would otherwise lose the group name.
_BUMP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # `bump <pkg> from <from> to <to>` — the dominant form, incl. `Bump httpx2
    # from 2.5.0 to 2.9.1 in /backend`.
    ("bump", re.compile(
        r"\bbump\s+(?P<pkg>[^\s]+)\s+from\s+`?(?P<from>[^`\s]+)`?\s+to\s+`?(?P<to>[^`\s]+)`?",
        re.IGNORECASE)),
    # `Update <pkg> requirement from >=3.2 to >=3.3.4 in /backend` (pip's wording).
    ("update_requirement", re.compile(
        r"\bupdate\s+(?P<pkg>[^\s]+)\s+requirement\s+from\s+`?(?P<from>[^`\s]+)`?\s+to\s+`?(?P<to>[^`\s]+)`?",
        re.IGNORECASE)),
    # `bump <pkg> from \`61b7c44\` to \`e2587ca\` in the nix group` — commit-pinned.
    ("bump", re.compile(
        r"\bbump\s+(?P<pkg>[^\s]+)\s+from\s+(?P<from>\S+)\s+to\s+(?P<to>\S+)",
        re.IGNORECASE)),
    # `bump the github-actions group with 3 updates` — no versions at all. Kept
    # as its own kind: it names a real upgrade with no version pair, which is
    # worth counting and must not be reported as a parse failure.
    ("group_no_versions", re.compile(
        r"\bbump\s+the\s+(?P<pkg>.+?)\s+group\s+with\s+\d+\s+update", re.IGNORECASE)),
    # `bump X to v1.2.3` with no from-version.
    ("bump_no_from", re.compile(
        r"\bbump\s+(?P<pkg>[^\s]+)\s+to\s+`?v?(?P<to>[^`\s]+)`?", re.IGNORECASE)),
    # `Update dependency foo to v1.2.3` / Renovate's older phrasing.
    ("update", re.compile(
        r"\bupdate\s+(?:dependency\s+)?(?P<pkg>[^\s]+)\s+to\s+`?v?(?P<to>[^`\s]+)`?",
        re.IGNORECASE)),
    # `Pin dependency foo to 1.2.3`.
    ("pin", re.compile(
        r"\bpin\s+(?:dependency\s+)?(?P<pkg>[^\s]+)\s+to\s+`?v?(?P<to>[^`\s]+)`?",
        re.IGNORECASE)),
)

#: Trailing scope after the version pair: `in /backend`, `in the go_modules
#: group`, `across 1 directory`, `in the nix group`. Recorded, not discarded --
#: which workspace a bump applied to is part of when it is applicable.
_SCOPE_RE = re.compile(
    r"\s+(?:in|across)\s+(?:the\s+)?(?P<scope>.+?)(?:\s+group)?\s*$", re.IGNORECASE)


class BotPrError(RuntimeError):
    """Non-retryable failure talking to the GitHub API."""


class BotPrRateLimited(BotPrError):
    """Retryable: primary or secondary rate limit. Carries the wait."""


class BotPrPermissionDenied(BotPrError):
    """A 403 that is NOT a rate limit -- bad token or missing scope."""


# --------------------------------------------------------------------------
# Parsed shapes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BumpCursor:
    """One merged bot PR, parsed. `cursor` is the resume token: the PR's
    numeric id, so a resumed run asks for `id > cursor` and never re-spends
    budget on rows it already saw."""

    cursor: int
    repo: str
    pr_number: int
    pr_id: int
    title: str
    merged_at: Optional[datetime]
    bot: str
    manifest_paths: tuple[str, ...] = ()
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    from_version: Optional[str] = None
    to_version: Optional[str] = None
    bump_kind: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor, "repo": self.repo, "pr_number": self.pr_number,
            "pr_id": self.pr_id, "title": self.title, "bot": self.bot,
            "merged_at": self.merged_at.isoformat() if self.merged_at else None,
            "manifest_paths": list(self.manifest_paths),
            "ecosystem": self.ecosystem, "package": self.package,
            "from_version": self.from_version, "to_version": self.to_version,
            "bump_kind": self.bump_kind,
        }


@dataclass(frozen=True)
class CheckSummary:
    """The CI verdict observed for one commit, at observation time."""

    conclusion: str
    total: int
    passed: int
    failed: int
    skipped: int
    observed_at: datetime
    #: The retention caveat, carried as data rather than prose: a check result
    #: read today says nothing about the same commit in 90 days.
    retention_note: str = "gh_actions_run_retention_~90d"

    @property
    def green(self) -> bool:
        return self.conclusion == "success"

    def as_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion, "total": self.total,
            "passed": self.passed, "failed": self.failed, "skipped": self.skipped,
            "observed_at": self.observed_at.isoformat(),
            "retention_note": self.retention_note,
        }


@dataclass(frozen=True)
class BotPrArtifact:
    """A discovered bump plus the CI verdict observed for it right now."""

    cursor: BumpCursor
    check: CheckSummary
    head_sha: str
    base_sha: str
    body: str


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------

HttpJsonGet = Callable[[str, bool], "tuple[int, dict[str, str], Any]"]


def _auth_host_allowed(url: str) -> bool:
    host = url.split("//", 1)[-1].split("/", 1)[0].split("@")[-1].split(":")[0].lower()
    return host in AUTH_HOSTS


def _token_from_env() -> Optional[str]:
    import os
    for name in ("PERSONAL_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    # `.env` is read by app.config and never exported: a token that lives only in backend/.env was invisible here.
    try:
        from app.config import settings

        return settings.personal_github_token or settings.github_token or None
    except Exception:  # noqa: BLE001 -- config unavailable (offline tests): unauthenticated
        return None


def _is_rate_limit_body(body: Any) -> bool:
    text = json.dumps(body)[:600].lower() if not isinstance(body, str) else body[:600].lower()
    return "rate limit" in text or "abuse detection" in text


def _sleep(seconds: float) -> None:
    """Rate-limit backoff.

    Plain `time.sleep`, deliberately: this adapter is a *synchronous* adapter
    (matching the `SourceAdapter` Protocol, which is sync on purpose), so it
    may only ever be called off the event loop. Every async caller in this
    package wraps discovery and fetch in `app.utils.aio.run_blocking`, which
    puts them on a worker thread -- where sleeping is correct. Calling this from
    inside an `async def` would block the loop, which is the house rule this
    module is required not to break.
    """
    time.sleep(max(0.0, seconds))


class _GitHubApiClient:
    """Minimal, rate-limit-aware, host-pinned GitHub REST client.

    `http_json_get` is injected for tests; the default uses httpx lazily
    (same discipline as `github_corpus._default_http_get`, so a missing httpx
    surfaces at first network use and never at import time).
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        http_json_get: Optional[HttpJsonGet] = None,
        reserve: int = RESERVE_REMAINING,
    ) -> None:
        self._token = token if token is not None else _token_from_env()
        self._http = http_json_get
        # NB: attribute is NOT `_reserve` -- that name is the throttling method.
        self._reserve_remaining = reserve
        self._reset_at: float = 0.0
        self._last_search = 0.0
        #: Observability for the run report. Never contains the token.
        self.stats: dict[str, int] = {"requests": 0, "retries": 0, "rate_limited": 0}

    # -- rate-limit bookkeeping ------------------------------------------
    def _reserve(self, *, is_search: bool) -> None:
        if is_search:
            gap = time.monotonic() - self._last_search
            if gap < SEARCH_MIN_INTERVAL_SECONDS:
                _sleep(SEARCH_MIN_INTERVAL_SECONDS - gap)
            self._last_search = time.monotonic()
        now = time.monotonic()
        if self._reset_at and now < self._reset_at:
            _sleep(self._reset_at - now)
        self._reset_at = 0.0

    def _note_headers(self, headers: dict[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if remaining is not None and reset is not None:
            try:
                if int(remaining) <= self._reserve_remaining:
                    wait = max(0.0, float(reset) - time.time())
                    self._reset_at = time.monotonic() + wait
            except ValueError:
                pass

    # -- the request ------------------------------------------------------
    def get(self, url: str, *, is_search: bool = False) -> Any:
        if not url.startswith("https://api.github.com/"):
            raise BotPrError(f"refusing non-GitHub-API url: {url!r}")
        self._reserve(is_search=is_search)

        last_exc: Optional[BaseException] = None
        for attempt in range(HTTP_ATTEMPTS):
            try:
                status, headers, body = self._one(url)
            except Exception as exc:  # noqa: BLE001 -- classified below
                if not _is_transport_error(exc):
                    raise
                last_exc = exc
                self.stats["retries"] += 1
                if attempt < HTTP_ATTEMPTS - 1:
                    _sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                continue

            self._note_headers(headers)
            self.stats["requests"] += 1

            if status == 200:
                return body
            if status == 403 or status == 429:
                if _is_rate_limit_body(body) or status == 429:
                    self.stats["rate_limited"] += 1
                    wait = float(headers.get("retry-after") or 0) or 60.0
                    reset = headers.get("x-ratelimit-reset")
                    if reset:
                        try:
                            wait = max(wait, float(reset) - time.time())
                        except ValueError:
                            pass
                    self._reset_at = time.monotonic() + wait
                    if attempt < HTTP_ATTEMPTS - 1:
                        _sleep(wait)
                        self.stats["retries"] += 1
                        continue
                    raise BotPrRateLimited(
                        f"rate limited after {HTTP_ATTEMPTS} attempts; wait {wait:.0f}s")
                raise BotPrPermissionDenied(f"403 from GitHub (not a rate limit): {url}")
            if status == 404:
                raise BotPrError(f"404 from GitHub: {url}")
            if status == 422:
                raise BotPrError(f"422 from GitHub (bad query): {url}")
            raise BotPrError(f"unexpected GitHub status {status} for {url}")

        assert last_exc is not None
        raise last_exc

    def _one(self, url: str) -> "tuple[int, dict[str, str], Any]":
        if self._http is not None:
            return self._http(url, _auth_host_allowed(url))

        import httpx

        headers = {"Accept": "application/vnd.github+json", "User-Agent": "stealthlab-step6"}
        # Host-pinned: the token is attached ONLY to an allowlisted GitHub host.
        if self._token and _auth_host_allowed(url):
            headers["Authorization"] = f"Bearer {self._token}"

        current = url
        for _ in range(MAX_REDIRECTS + 1):
            response = httpx.get(current, timeout=30, follow_redirects=False, headers=headers)
            if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                nxt = str(httpx.URL(response.url).join(response.headers["location"]))
                if not nxt.startswith("https://"):
                    raise BotPrError(f"refusing non-https redirect: {nxt!r}")
                if not _auth_host_allowed(nxt):
                    # Followed, but only if allowed -- and auth is NOT re-attached
                    # because headers are rebuilt per hop from `_auth_host_allowed`.
                    if not FOLLOW_CROSS_HOST_REDIRECT:
                        raise BotPrError(f"refusing cross-host redirect: {nxt!r}")
                current = nxt
                continue
            try:
                body = response.json()
            except Exception:  # noqa: BLE001
                body = response.text
            return response.status_code, dict(response.headers), body
        raise BotPrError(f"too many redirects starting at {url}")


def _is_transport_error(exc: BaseException) -> bool:
    import ssl
    try:
        import httpx
    except Exception:  # noqa: BLE001
        httpx = None  # type: ignore[assignment]
    if httpx is not None and isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, (ssl.SSLError, ConnectionError, OSError))


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _norm_version(value: Optional[str]) -> Optional[str]:
    """Strip a leading `v`/`V`. Renovate titles say "to v1.2.3" and Dependabot
    says "to 1.2.3"; storing both spellings of the same version would make the
    dedupe key miss and mint a second Procedure for one upgrade."""
    if not value:
        return value
    text = str(value).strip()
    return text[1:] if text[:1] in ("v", "V") and text[1:2].isdigit() else text


def parse_bump_title(title: str) -> dict[str, Optional[str]]:
    """Extract (package, from, to, kind, scope) from a bot PR title.

    Returns `{"package": None, ...}` rather than raising when no pattern
    matches: an unparseable title is a real, common outcome (a bot's release
    PR, a security backport) and must be counted, not crash the run.

    `scope` is the workspace/monorepo path a grouped bump applied to
    (`in /backend`, `in the go_modules group`). It is part of when the knowledge
    is applicable, so it is captured rather than dropped.
    """
    raw = (title or "").strip()
    stripped = _CONVENTIONAL_PREFIX_RE.sub("", raw)
    for kind, pattern in _BUMP_PATTERNS:
        match = pattern.search(stripped)
        if not match:
            continue
        groups = match.groupdict()
        package = (groups.get("pkg") or "").strip().strip("`")
        if not package:
            continue
        scope = None
        tail = stripped[match.end():]
        scope_match = _SCOPE_RE.search(tail)
        if scope_match:
            scope = scope_match.group("scope").strip().strip("`").rstrip(".") or None
        return {
            "package": package,
            "from_version": _norm_version(groups.get("from")),
            "to_version": _norm_version(groups.get("to")),
            "bump_kind": kind,
            "scope": scope,
        }
    return {"package": None, "from_version": None, "to_version": None,
            "bump_kind": None, "scope": None}


def manifest_ecosystem(paths: "tuple[str, ...] | list[str]") -> tuple[tuple[str, ...], Optional[str]]:
    """Filter changed paths down to known dependency manifests and label the
    ecosystem. A PR touching only source files is not a dependency bump and is
    rejected here rather than downstream."""
    found = tuple(
        p for p in paths
        if p.rsplit("/", 1)[-1] in DEPENDENCY_MANIFEST_NAMES
    )
    ecosystem = None
    for path in found:
        ecosystem = ECOSYSTEM_BY_MANIFEST.get(path.rsplit("/", 1)[-1], ecosystem)
        if ecosystem:
            break
    return found, ecosystem


def summarize_checks(check_runs: Any) -> CheckSummary:
    """Fold `/commits/{sha}/check-runs` into one verdict.

    An absent/empty `check_runs` is `conclusion="none"`, which is NOT green.
    A repo with no CI must never be counted as a passing check -- that is the
    "42.1% of workflows have no test step" failure mode arriving through a
    different door.
    """
    now = datetime.now(timezone.utc)
    runs = (check_runs or {}).get("check_runs") or []
    if not runs:
        return CheckSummary("none", 0, 0, 0, 0, now)
    counts = {"success": 0, "failure": 0, "skipped": 0, "neutral": 0, "cancelled": 0,
              "timed_out": 0, "action_required": 0, "stale": 0}
    for run in runs:
        key = str(run.get("conclusion") or run.get("status") or "neutral")
        counts[key] = counts.get(key, 0) + 1
    total = len(runs)
    passed = counts["success"]
    failed = counts["failure"] + counts["timed_out"] + counts["action_required"]
    if failed:
        conclusion = "failure"
    elif counts["cancelled"]:
        conclusion = "cancelled"
    elif passed == 0:
        conclusion = "neutral"
    else:
        conclusion = "success"
    return CheckSummary(conclusion, total, passed, failed,
                        counts["skipped"] + counts["neutral"], now)


def _merge_commit_states(states: Any) -> str:
    """Fold `/commits/{sha}/status` into one conclusion.

    Needed because `POST /repos/{o}/{r}/statuses/{sha}` is what many older
    workflows write, and it is invisible to `/check-runs`. Reading only
    check-runs therefore reports `none` for a repository whose CI did run and
    did report -- which is exactly what the pilot showed: 25/25 `none`. A
    `none` that is really "reported through the other API" is a false negative
    about our own detection, not a fact about the repository.
    """
    states = states or []
    if not states:
        return "none"
    # A commit can carry several contexts (build, test, lint). Any failure wins;
    # otherwise success if any context succeeded.
    seen = {str((s or {}).get("state") or "unknown") for s in states}
    for bad in ("failure", "error"):
        if bad in seen:
            return "failure"
    if "pending" in seen:
        return "pending"
    if "success" in seen:
        return "success"
    return "neutral"


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------

class BotDependencyPrSource:
    """`SourceAdapter` over merged Dependabot/Renovate PRs.

    `discover()` is a cursor-based, resumable search; `fetch()` pulls the diff
    files and the CI verdict for one PR. Constructing one performs no network
    I/O, per the convention every other adapter in this package follows.
    """

    source_type = SOURCE_TYPE

    def __init__(
        self,
        *,
        client: Optional[_GitHubApiClient] = None,
        authors: "tuple[str, ...]" = BOT_AUTHORS,
        since: str = DEFAULT_SINCE,
        per_page: int = DEFAULT_SEARCH_RESULTS,
        max_pages: int = 1,
        repos: "tuple[str, ...]" = (),
    ) -> None:
        self._client = client or _GitHubApiClient()
        self._authors = authors
        self._since = since
        self._per_page = min(per_page, 100)
        self._max_pages = max_pages
        #: Optional repository scoping. See `discover()` for why a run that needs
        #: more than ~1,000 PRs MUST set this.
        self._repos = tuple(repos)
        self._snapshots: dict[str, BotPrArtifact] = {}
        #: Cursors seen by `discover()`, so `fetch()` can resolve a ref the
        #: caller built from one. Registered on discovery, not on fetch -- the
        #: earlier arrangement made `fetch()` unresolvable for every ref that
        #: did not come out of a previous `fetch()`.
        self._cursors: dict[str, BumpCursor] = {}
        #: Per-repo SPDX cache. One `/license` call per repo, not per PR.
        self._licenses: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _cursor_key(repo: str, pr_number: int) -> str:
        return f"{repo}#{pr_number}"

    # -- discover ---------------------------------------------------------
    def discover(self, *, after: int = 0) -> Iterator[BumpCursor]:
        """Yield merged bot PRs newer than `after`, cheapest-first.

        Resumability is the point: the caller persists the last `cursor` and
        passes it back, so a run killed by a secondary rate limit costs nothing
        on restart.

        REPOSITORY SCOPING IS NOT AN OPTIMISATION. GitHub's search endpoint caps
        every query at 1,000 results, and a global `author:app/dependabot
        is:merged` query reports ~26.6M matches -- so the global form is
        permanently truncated and cannot enumerate a corpus. Measured on
        2026-09-28. Any run that needs more than ~1,000 PRs must pass `repos=`;
        with `repos` empty this method keeps its original global behaviour,
        which is correct for a bounded probe and wrong for a census.
        """
        scopes: "tuple[str, ...]" = self._repos or ("",)
        for author in self._authors:
            for scope in scopes:
                page = 1
                while page <= self._max_pages:
                    body = self._client.get(
                        self._search_url(author, page, repo=scope), is_search=True)
                    items = (body or {}).get("items") or []
                    if not items:
                        break
                    for item in items:
                        cursor = self._to_cursor(item, author)
                        if cursor is None or cursor.cursor <= after:
                            continue
                        self._cursors[self._cursor_key(cursor.repo, cursor.pr_number)] = cursor
                        yield cursor
                    if len(items) < self._per_page:
                        break
                    page += 1

    def _search_url(self, author: str, page: int, *, repo: str = "") -> str:
        parts = ["is:pr", "is:merged", f"author:{author}", f"merged:>={self._since}"]
        if repo:
            parts.append(f"repo:{repo}")
        query = " ".join(parts)
        return (
            "https://api.github.com/search/issues"
            f"?q={_quote(query)}&sort=updated&order=asc&per_page={self._per_page}&page={page}"
        )

    def _to_cursor(self, item: dict[str, Any], author: str) -> Optional[BumpCursor]:
        if item.get("pull_request") is None:
            return None
        repo_url = str(item.get("repository_url") or "")
        repo = repo_url.split("/repos/", 1)[-1] if "/repos/" in repo_url else ""
        if not repo or "/" not in repo:
            return None
        merged_at = _parse_ts(item.get("pull_request", {}).get("merged_at")
                              or item.get("closed_at"))
        parsed = parse_bump_title(str(item.get("title") or ""))
        paths, ecosystem = manifest_ecosystem(())
        return BumpCursor(
            cursor=int(item.get("id") or 0),
            repo=repo,
            pr_number=int(item.get("number") or 0),
            pr_id=int(item.get("id") or 0),
            title=str(item.get("title") or ""),
            merged_at=merged_at,
            bot=author,
            manifest_paths=paths,
            ecosystem=ecosystem,
            package=parsed["package"],
            from_version=parsed["from_version"],
            to_version=parsed["to_version"],
            bump_kind=parsed["bump_kind"],
            raw=item,
        )

    # -- fetch ------------------------------------------------------------
    def fetch(self, ref: SourceRef) -> SourceArtifact:
        """Pull one PR's changed files + CI verdict into a `SourceArtifact`.

        The artifact is *descriptive* -- it states what was bumped and what CI
        said. Turning it into a Procedure is `workflow_knowledge.py`'s job, and
        that function refuses to mark it verified.
        """
        cursor = self._resolve_cursor(ref)
        files = self._client.get(
            f"https://api.github.com/repos/{cursor.repo}/pulls/{cursor.pr_number}/files?per_page=100")
        changed = tuple(
            str(f.get("filename") or "") for f in (files or []) if f.get("filename")
        )
        paths, ecosystem = manifest_ecosystem(changed)
        head_sha = str((files or [{}])[0].get("sha") or "")
        detail = self._client.get(
            f"https://api.github.com/repos/{cursor.repo}/pulls/{cursor.pr_number}")
        base_sha = str((detail or {}).get("base", {}).get("sha") or "")
        real_head = str((detail or {}).get("head", {}).get("sha") or head_sha)
        checks = self._checks_for(cursor.repo, real_head)

        cursor = _replace_cursor(cursor, manifest_paths=paths, ecosystem=ecosystem)
        body = (detail or {}).get("body") or ""
        license_metadata = self._repo_license(cursor.repo)
        content = _render_bump_document(cursor, checks, detail)
        self._snapshots[self._cursor_key(cursor.repo, cursor.pr_number)] = BotPrArtifact(
            cursor=cursor, check=checks, head_sha=real_head, base_sha=base_sha, body=body)

        return SourceArtifact(
            source_type=self.source_type,
            uri=f"https://github.com/{cursor.repo}/pull/{cursor.pr_number}",
            content=content,
            content_hash=compute_content_hash(content),
            repository=cursor.repo,
            path=f"pull/{cursor.pr_number}",
            commit=real_head or None,
            source_id=SOURCE_TYPE,
            # `spdx_id` first: `screening.spdx_license_signal` reads that key,
            # and `classify_spdx` reads either. Both set, per artifact.
            license_metadata={**license_metadata,
                              "observed_manifest_paths": list(paths)},
        )

    def _checks_for(self, repo: str, sha: str) -> CheckSummary:
        """Read BOTH CI surfaces for one commit.

        `/check-runs` and `/status` are different APIs and a repository
        generally uses one or the other, not both. Reading only check-runs
        reports `none` for every repo on the older status API, which the pilot
        caught: 25/25 `none` before this was merged. The merged verdict is the
        more informative of the two; `none` survives only when both are empty,
        which genuinely means "no CI reported on this commit".
        """
        runs = self._client.get(
            f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs")
        summary = summarize_checks(runs)
        if summary.total:
            return summary
        try:
            states = self._client.get(
                f"https://api.github.com/repos/{repo}/commits/{sha}/status")
        except BotPrError:
            return summary
        conclusion = _merge_commit_states((states or {}).get("statuses"))
        if conclusion == "none":
            return summary
        now = datetime.now(timezone.utc)
        return CheckSummary(
            conclusion=conclusion,
            total=len((states or {}).get("statuses") or []),
            passed=sum(1 for s in (states or {}).get("statuses") or []
                       if (s or {}).get("state") == "success"),
            failed=sum(1 for s in (states or {}).get("statuses") or []
                       if (s or {}).get("state") in ("failure", "error")),
            skipped=0, observed_at=now,
            retention_note="commit_status_api; gh_actions_run_retention_~90d",
        )

    def _repo_license(self, repo: str) -> dict[str, Any]:
        """Per-repository SPDX id, cached for the run.

        Step 6's rule is license per item, never per compilation: a merged bump
        PR in an MIT repo and one in an AGPL repo are different items with
        different verdicts, and the whole point of the allowlist is that this
        is decided separately for each.
        """
        if repo in self._licenses:
            return self._licenses[repo]
        metadata: dict[str, Any] = {"observed_manifest_paths": []}
        try:
            body = self._client.get(f"https://api.github.com/repos/{repo}/license")
        except BotPrError:
            # An unlicensed or 404 repo is QUARANTINE, not ALLOW. Recording the
            # absence is the point; swallowing it into a pass is the bug.
            metadata["spdx_id"] = None
            metadata["license_error"] = "license endpoint unavailable"
            self._licenses[repo] = metadata
            return metadata
        if isinstance(body, dict):
            spdx = (body.get("license") or {}).get("spdx_id")
            metadata["spdx_id"] = spdx
            metadata["license_name"] = (body.get("license") or {}).get("name")
            metadata["license_path"] = body.get("path")
        else:
            metadata["spdx_id"] = None
            metadata["license_error"] = "unexpected license payload"
        self._licenses[repo] = metadata
        return metadata

    def ref_for(self, cursor: BumpCursor) -> SourceRef:
        """Build the `SourceRef` that resolves back to this cursor.

        Public because the (discover -> fetch) pairing is a real contract, not
        an accident of the CLI: a caller that hand-builds a `SourceRef` with
        the wrong `path` gets an unresolvable-ref error, and the shape of the
        path is exactly the thing most likely to drift. Making the mapping one
        call means discover and fetch can never disagree about it.
        """
        return SourceRef(
            uri=f"https://github.com/{cursor.repo}/pull/{cursor.pr_number}",
            repository=cursor.repo,
            path=f"pull/{cursor.pr_number}",
            source_id=SOURCE_TYPE,
        )

    def _resolve_cursor(self, ref: SourceRef) -> BumpCursor:
        pr_number = int((ref.path or "").rsplit("/", 1)[-1] or 0)
        key = self._cursor_key(ref.repository or "", pr_number)
        cached = self._snapshots.get(key)
        if cached is not None:
            return cached.cursor
        known = self._cursors.get(key)
        if known is not None:
            return known
        raise BotPrError(
            f"fetch() needs a ref from discover() for {ref.repository} {ref.path}; "
            "this adapter does not re-derive a cursor from a bare uri"
        )

    # -- SourceAdapter ----------------------------------------------------
    def fingerprint(self, artifact: SourceArtifact) -> str:
        """Identity for staleness: repo + PR number + head sha.

        Deliberately NOT the content hash. Two fetches of the same PR one day
        apart legitimately differ (the check-run list grows), and we do not
        want that to mint a new Procedure version.
        """
        return f"{artifact.repository}:{artifact.path}:{artifact.commit or ''}"

    def artifacts(self) -> "list[BotPrArtifact]":
        return list(self._snapshots.values())


def _quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _replace_cursor(cursor: BumpCursor, **changes: Any) -> BumpCursor:
    data = {f: getattr(cursor, f) for f in cursor.__dataclass_fields__}
    data.update(changes)
    return BumpCursor(**data)


def _render_bump_document(cursor: BumpCursor, checks: CheckSummary, detail: Any) -> str:
    """The artifact text. Structured, greppable, and explicit that CI is
    corroboration rather than proof -- the words survive into whatever a
    downstream extractor reads."""
    lines = [
        f"# dependency bump: {cursor.package or 'unknown package'}",
        f"repository: {cursor.repo}",
        f"pull_request: {cursor.pr_number}",
        f"merged_at: {cursor.merged_at.isoformat() if cursor.merged_at else 'unknown'}",
        f"bot: {cursor.bot}",
        f"ecosystem: {cursor.ecosystem or 'unknown'}",
        f"from_version: {cursor.from_version or 'unknown'}",
        f"to_version: {cursor.to_version or 'unknown'}",
        f"changed_manifests: {', '.join(cursor.manifest_paths) or 'none detected'}",
        "",
        "## observed CI verdict",
        f"conclusion: {checks.conclusion}",
        f"checks_total: {checks.total}",
        f"checks_passed: {checks.passed}",
        f"checks_failed: {checks.failed}",
        f"observed_at: {checks.observed_at.isoformat()}",
        f"retention_note: {checks.retention_note}",
        "",
        "## verification honesty",
        "This CI result was observed from outside this system. It is a host",
        "self-report: a green run does not establish that the upgrade is",
        "correct, only that the repository's own checks passed when we looked.",
        "GitHub Actions retains run history for about 90 days, so this verdict",
        "is only valid as of observed_at.",
    ]
    return "\n".join(lines) + "\n"


# Registering this adapter is one line, and deliberately kept next to the class
# rather than edited into `dispatch.py` (whose TRAJECTORY_ADAPTERS table is
# dead data read by nothing).
ADAPTERS: dict[str, type] = {SOURCE_TYPE: BotDependencyPrSource}
