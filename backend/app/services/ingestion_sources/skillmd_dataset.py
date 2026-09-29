"""`FayeZC/SkillMD-138K` reader and source adapter.

MEASURED FACTS THIS MODULE IS BUILT ON (2026-09-28, read from the parquet
footer and column chunks over HTTP range requests, not from the card)
    138,133 rows / 20,556 repos / one `train` split / 560,428,690 bytes.
    Nine columns, zero nulls: content_hash, repo, path, stars, source,
    html_url, content, lines, words.
    The dataset's own dedup was EXACT SHA-256 only -- all 138,133 hashes are
    distinct -- so identical skills that were forked and re-worded survive as
    separate rows. Measured: 123,315 distinct paths for 138,133 rows, with
    5,573 paths appearing in more than one row across 20,391 rows.

WHY THE CONTENT COLUMN IS NOT READ FROM THE PARQUET
    `content` is a single 540,908,068-byte compressed column chunk inside one
    row group, and the dataset's own viewer is broken for exactly this reason
    (HF returns `TooBigContentError`: a 560,420,299-byte scan against a
    300,000,000-byte limit). Any parquet read that touches `content` for even
    one row pulls the whole chunk. We therefore read the eight metadata
    columns (cheap, ~20 MB) and fetch each skill's text from GitHub raw via
    the row's own `html_url`.

    That is not only cheaper, it is *more* provenance: a raw fetch returns the
    bytes GitHub serves today, so a row whose file has since been deleted or
    moved is detected as DEAD rather than ingested from a six-month-old
    snapshot. The corpus was last modified 2026-04-08 22:32:31 UTC and has
    never been updated since, so roughly 5.8 months of upstream churn is
    sitting behind every row.

THE MIRROR PROBLEM, AND WHY IT IS THE FIRST GATE
    12.7% of rows (17,492) live in aggregator repositories that re-host other
    people's skills. `NeverSight/skills_feed` alone holds 17,284 rows (12.5%)
    and encodes the origin in its path: `data/skills-md/<owner>/<repo>/...`.
    Resolving "this repository's license" from `repo` for those rows returns
    the *aggregator's* license -- a licence that conveys no right to 3,249
    other people's skills, and one that would launder exactly the provenance
    the plan's hard rule 2 exists to preserve. So origin recovery runs BEFORE
    any license resolution, and an aggregator row whose origin cannot be
    recovered is QUARANTINED rather than admitted against the mirror's terms.

    The dataset records no license column at all -- the card states that
    "users should consult the original repository's license" -- and in a live
    125-row sample only 17.6% of skills declared their own `license`
    frontmatter. Per-repo resolution is therefore unavoidable, not a
    refinement.

SYNC ON PURPOSE, and network inside discover()
    `base.SourceAdapter` is deliberately sync. But the content-level gates
    (frontmatter, description, body size, coercion, screening) need the text,
    and the only seam the contract offers a caller that must gate on content
    is `discover()` -- `fetch()` returns an artifact and has no rejection
    channel. So this adapter fetches inside `discover()`, gates there, and
    caches the text so `fetch()` is free. `GitHubSkillSource` sets the
    precedent: its `discover()` already makes an API call.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional
from urllib.parse import quote, urlparse

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)
from app.services.ingestion_sources.skillmd_gate import (
    GATE_VERSION,
    GateVerdict,
    NearDuplicateIndex,
    exact_duplicate_key,
    gate_text,
)

log = logging.getLogger(__name__)

DATASET_REPO = "FayeZC/SkillMD-138K"

# Pinned to the commit the HF API reported on 2026-09-28. The dataset has
# never been updated since creation (7 minutes apart, 2026-04-08), so this
# pin is stable -- but it is a pin, not `main`, because a corpus that changes
# under a run makes every yield number in the summary unreproducible.
DATASET_REVISION = "0d73048a"

METADATA_COLUMNS: tuple[str, ...] = (
    "content_hash", "repo", "path", "stars", "source", "html_url", "lines", "words",
)

# The eight metadata columns cost ~20 MB; `content` is 540 MB in one chunk.
# Kept as a tuple so the column list travels with the revision it describes.
_PARQUET_URL = (
    "https://huggingface.co/datasets/{repo}/resolve/{rev}/train.parquet"
)

# Aggregator layout, measured from the corpus: the origin owner/repo is
# recoverable from the path for every `data/skills-md/<owner>/<repo>/...` row.
_MIRROR_PATH_RE = re.compile(r"(?i)\A(?:data/)?skills-md/(?P<owner>[^/]+)/(?P<repo>[^/]+)/")

# Repos measured as aggregators. `NeverSight/skills_feed` (17,284 rows) is the
# one that carries the recoverable path layout; the rest are listed so a row
# from them is at least *named* as a mirror in the disposition counts even
# when its origin is not recoverable.
KNOWN_AGGREGATORS: frozenset[str] = frozenset({
    "NeverSight/skills_feed",
    "majiayu000/claude-skill-registry",
    "jeremylongshore/claude-code-plugins-plus-skills",
    "aiskillstore/marketplace",
    "openclaw/skills",
    "sickn33/antigravity-awesome-skills",
    "davila7/claude-code-templates",
})

_HTML_BLOB_RE = re.compile(
    r"\Ahttps?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)\Z"
)

# Star prior, applied as a soft ordering signal only. The measured
# distribution is bimodal for a reason that has nothing to do with quality
# (mirror rows carry the mirror's stars: median 104 for the top-10 repos vs 2
# for everything else), so this cannot be a quality proxy. It is used to
# prefer popular repos when a run is truncated, and never to reject.
STAR_PRIOR_FLOOR = 5

# Corpus hygiene, measured: the crawl matched any file whose basename looked
# skill-ish, and 99.65% are literally `SKILL.md`. The ~206-row tail is made of
# meta-skills *about* authoring skills (`create-skill.md`, `install-skill.md`,
# `WHAT_IS_SKILL.md`), which are tooling, not procedural knowledge. Lowercase
# `skill.md` (482 rows) is legitimate per the spec, so it is kept.
_META_SKILL_NAME_RE = re.compile(
    r"(?i)\A(create|install|new|test|heal|what_is|what-is|validate|update)-?skill\.md\Z"
)

_HTTP_RETRY_ATTEMPTS = 3
_HTTP_BACKOFF_SECONDS = 1.5

# One pooled client per thread. `httpx.get` builds a client, a TLS session and
# a connection per call, which measured ~2.5 s per raw fetch in this corpus
# and dominated the entire pilot wall time. Pooling is the fix, not a bigger
# thread pool: the connections are to a handful of raw.githubusercontent.com
# hosts, so reusing them collapses the per-row cost to a single round trip.
# thread-local because the fetch stage is a ThreadPoolExecutor and one shared
# client across threads is not safe.
_CLIENTS = threading.local()


def _client() -> Any:
    import httpx

    client = getattr(_CLIENTS, "client", None)
    if client is None:
        client = httpx.Client(
            timeout=30,
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=16, max_connections=16),
        )
        _CLIENTS.client = client
    return client


def close_clients() -> None:
    """Close this thread's pooled client. Called by the pilot at the end of a
    run so a long-lived process does not hold idle sockets open."""
    client = getattr(_CLIENTS, "client", None)
    if client is not None:
        client.close()
        _CLIENTS.client = None


def _http_get(url: str, *, headers: Optional[dict[str, str]] = None) -> tuple[int, str]:
    """GET returning (status, text), redirect-free.

    `follow_redirects=False` is a security decision, not a style one. The
    audit recorded a real finding in `github_corpus._default_http_get`: a
    bearer token built once is replayed on every redirect hop, and GitHub
    legitimately redirects raw content to object storage, so a 302 to an
    unexpected public host would carry the token with it. Here every URL this
    module builds is a github.com or raw.githubusercontent.com host, and we
    never follow a redirect with a credential attached -- a redirect is
    reported as a status for the caller to count, not followed.
    """
    from app.services.screening import assert_safe_locator

    assert_safe_locator(url)
    last_exc: Optional[BaseException] = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
        try:
            response = _client().get(url, headers=headers or None)
            return response.status_code, response.text
        except (httpx.TransportError, OSError) as exc:
            last_exc = exc
            if attempt < _HTTP_RETRY_ATTEMPTS - 1:
                time.sleep(_HTTP_BACKOFF_SECONDS * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def _github_headers() -> dict[str, str]:
    """Read the token the same way `github_corpus` does; never log it.

    Unauthenticated GitHub allows 60 requests/hour per IP, which is below the
    per-repo license lookups a 2,000-row pilot needs once it touches more than
    ~40 repositories. The pilot reports how many lookups it made and how many
    were rate-limited rather than pretending the run was complete.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("PERSONAL_GITHUB_TOKEN")
    if not token:
        # `.env` is read by `app.config`, not exported to os.environ: a token that lives only in backend/.env
        # (as PERSONAL_GITHUB_TOKEN does) was invisible here, every license lookup ran unauthenticated, hit the
        # 60/hour wall after ~60 repos, and the rest of the corpus was quarantined as "license unknown".
        try:
            from app.config import settings

            token = settings.github_token or settings.personal_github_token
        except Exception:  # noqa: BLE001 -- config unavailable (offline tests): run unauthenticated, and say so in stats
            token = None
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


@dataclass(frozen=True)
class SkillRow:
    """One dataset row, with its origin already resolved."""

    content_hash: str
    repo: str
    path: str
    stars: int
    source: str
    html_url: str
    lines: int
    words: int
    origin_repo: str
    origin_path: str
    is_mirror: bool
    origin_recoverable: bool
    fallback_name: str

    @property
    def exact_key(self) -> str:
        return exact_duplicate_key(self.content_hash)


def resolve_origin(repo: str, path: str) -> tuple[str, str, bool, bool]:
    """(origin_repo, origin_path, is_mirror, origin_recoverable).

    For a non-mirror row this is the identity function. For a mirror row whose
    path carries the `skills-md/<owner>/<repo>/` prefix the origin is
    recovered; for any other mirror row it is not, and the caller quarantines.
    """
    if repo not in KNOWN_AGGREGATORS:
        return repo, path, False, True
    match = _MIRROR_PATH_RE.match(path or "")
    if not match:
        return repo, path, True, False
    origin_repo = f"{match.group('owner')}/{match.group('repo')}"
    remainder = path[match.end() :]
    return origin_repo, remainder or path, True, True


def row_to_skill_row(raw: dict[str, Any]) -> SkillRow:
    """Map one parquet row to a `SkillRow`, resolving the origin."""
    repo = str(raw.get("repo") or "")
    path = str(raw.get("path") or "")
    origin_repo, origin_path, is_mirror, recoverable = resolve_origin(repo, path)
    return SkillRow(
        content_hash=str(raw.get("content_hash") or ""),
        repo=repo,
        path=path,
        stars=int(raw.get("stars") or 0),
        source=str(raw.get("source") or ""),
        html_url=str(raw.get("html_url") or ""),
        lines=int(raw.get("lines") or 0),
        words=int(raw.get("words") or 0),
        origin_repo=origin_repo,
        origin_path=origin_path,
        is_mirror=is_mirror,
        origin_recoverable=recoverable,
        fallback_name=_fallback_name(path),
    )


def _fallback_name(path: str) -> str:
    """A spec-shaped name from the path, used only for rejection messages."""
    parts = [p for p in (path or "").split("/") if p]
    stem = parts[-1] if parts else "unnamed-skill"
    for suffix in (".skill.md", "SKILL.md", "skill.md", "Skill.md", ".md"):
        if stem.lower().endswith(suffix.lower()):
            stem = stem[: -len(suffix)]
            break
    cleaned = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return (cleaned or "unnamed-skill")[:64]


def _raw_url(owner_repo: str, ref: str, path: str) -> str:
    owner, _, repo = owner_repo.partition("/")
    return (
        f"https://raw.githubusercontent.com/{quote(owner, safe='')}/"
        f"{quote(repo, safe='')}/{quote(ref or 'HEAD', safe='')}/{quote(path, safe='/')}"
    )


def html_url_parts(html_url: str) -> Optional[tuple[str, str, str]]:
    """(owner/repo, ref, path) from a github.com blob URL, else None."""
    match = _HTML_BLOB_RE.match(html_url or "")
    if not match:
        return None
    return (
        f"{match.group('owner')}/{match.group('repo')}",
        match.group("ref"),
        match.group("path"),
    )


def fetch_row_text(row: SkillRow) -> tuple[str, Optional[str]]:
    """The skill's text from GitHub raw, or ("", reason) when unreachable.

    Fetches from the ORIGIN repo, not the mirror. A mirror's copy of a
    third party's skill is not evidence of anything, and pulling from it
    would attach a third party's content to the aggregator's provenance.

    Returns a reason slug rather than raising: a dead row is a normal outcome
    for a six-month-old crawl and must not abort a batch.
    """
    parts = html_url_parts(row.html_url)
    ref = parts[1] if parts else "HEAD"
    if not row.origin_recoverable and row.is_mirror:
        return "", "mirror_origin_unrecoverable"
    # No Authorization header here, deliberately. `raw.githubusercontent.com`
    # is a different host from `api.github.com`, and a credential is only ever
    # attached to the one host that needs it. This is the same defect the audit
    # found in `github_corpus._default_http_get`, where one header dict built
    # for api.github.com was replayed across every redirect hop.
    url = _raw_url(row.origin_repo, ref, row.origin_path)
    try:
        status, text = _http_get(url)
    except Exception as exc:  # noqa: BLE001 -- a transport failure is a row outcome
        log.debug("skillmd raw fetch failed for %s: %s", url, exc)
        return "", "raw_fetch_error"
    if status == 404:
        return "", "raw_404_deleted_or_moved"
    if status == 429:
        return "", "raw_429_rate_limited"
    if status in (301, 302, 303, 307, 308):
        return "", "raw_redirect_not_followed"
    if status != 200:
        return "", f"raw_status_{status}"
    return text, None


class SkillMD138KReader:
    """Revision-pinned reader over the dataset's metadata columns.

    The metadata slice is cached to a local parquet file when `cache_path` is
    given, so a second pilot run over the same revision does not re-download
    ~20 MB. `content` is never read from here -- see the module docstring.
    """

    def __init__(
        self,
        *,
        repo: str = DATASET_REPO,
        revision: str = DATASET_REVISION,
        cache_path: Optional[str] = None,
    ) -> None:
        self.repo = repo
        self.revision = revision
        self.cache_path = cache_path

    @property
    def source_url(self) -> str:
        return _PARQUET_URL.format(repo=self.repo, rev=self.revision)

    def _cached_table(self):
        import pyarrow.parquet as pq

        if self.cache_path:
            import os

            if os.path.exists(self.cache_path):
                return pq.read_table(self.cache_path)
        import fsspec

        with fsspec.filesystem("http").open(self.source_url, "rb") as handle:
            table = pq.ParquetFile(handle).read(columns=list(METADATA_COLUMNS))
        if self.cache_path:
            import os

            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            pq.write_table(table, self.cache_path)
        return table

    def iter_rows(self, limit: Optional[int] = None) -> Iterator[SkillRow]:
        table = self._cached_table()
        total = table.num_rows
        count = 0
        for raw in table.to_pylist():
            if limit is not None and count >= limit:
                break
            count += 1
            yield row_to_skill_row(raw)
        if limit is not None and count < total:
            log.info("skillmd reader stopped at %s of %s rows (limit)", count, total)

    def count(self) -> int:
        return int(self._cached_table().num_rows)


@dataclass
class SkillMD138KStats:
    """Disposition counts for one `discover()` pass."""

    rows_seen: int = 0
    fetched: int = 0
    admitted: int = 0
    reasons: dict[str, int] = None  # type: ignore[assignment]
    mirror_rows: int = 0
    mirror_origin_recovered: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    license_gate: Optional[dict[str, int]] = None
    below_star_prior: int = 0

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = {}

    def bump(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_version": GATE_VERSION,
            "dataset": DATASET_REPO,
            "dataset_revision": DATASET_REVISION,
            "rows_seen": self.rows_seen,
            "fetched": self.fetched,
            "admitted": self.admitted,
            "reasons": dict(sorted(self.reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
            "mirror_rows": self.mirror_rows,
            "mirror_origin_recovered": self.mirror_origin_recovered,
            "exact_duplicates": self.exact_duplicates,
            "near_duplicates": self.near_duplicates,
            "below_star_prior": self.below_star_prior,
            "license_gate": self.license_gate,
        }


# How many candidate rows PHASE A may hand to the fetch stage per requested
# admitted skill. Needed because the limit is enforced in PHASE C, where
# `admitted` is actually counted: an earlier version checked the limit in
# PHASE A against a counter PHASE C had not touched yet, so in the parallel
# path the limit never fired and the adapter yielded the whole corpus. The
# oversample bounds the network work instead. 12 is generous against the
# measured gate losses (a small live dry-run admitted 5 of 11 rows seen, with
# 3 of those dead upstream) and is a named constant rather than a magic number.
FETCH_OVERSAMPLE = 12

# Repos whose paths do not encode an origin are dropped before any network
# call, so the oversample is spent on rows that can actually be evaluated.


class SkillMD138KSource:
    """`SourceAdapter` over SkillMD-138K, gated before anything is yielded.

    Gate order in `discover()` is cheapest-and-certain first:

      1. filename hygiene          (free; drops the ~206 crawl false positives)
      2. mirror / origin recovery  (free; decides whether a license is knowable)
      3. exact content-hash dedup  (free; the dataset's own dedup, re-applied)
      4. content fetch             (network)
      5. frontmatter / size        (cheap; hard reject)
      6. coercion + screening      (cheap; quarantine)
      7. near-dup simhash          (cheap; reported, never merged)
      8. license allowlist         (one cached API call per ORIGIN repo)
    """

    source_type = "skill_md_138k"

    def __init__(
        self,
        reader: Optional[SkillMD138KReader] = None,
        *,
        limit: Optional[int] = None,
        license_resolver: Any = None,
        enforce_license: bool = True,
        star_prior: Optional[int] = None,
        raw_fetcher: Any = None,
        fetch_workers: int = 1,
    ) -> None:
        self._reader = reader or SkillMD138KReader()
        self._limit = limit
        self._license_resolver = license_resolver
        self._enforce_license = enforce_license
        self._star_prior = STAR_PRIOR_FLOOR if star_prior is None else star_prior
        self._near_dup = NearDuplicateIndex()
        self._raw_fetcher = raw_fetcher or fetch_row_text
        self._fetch_workers = max(1, int(fetch_workers))
        self._cache: dict[str, SourceArtifact] = {}
        self._replayed: Optional[list[SourceRef]] = None
        self.stats = SkillMD138KStats()
        self.verdicts: list[GateVerdict] = []

    def _candidates(self) -> Iterator[tuple[Any, str]]:
        """PHASE A -- sequential, in row order, no network.

        Every decision here is a pure function of the row's own metadata, so
        doing it in order and before any fetch is what makes the accounting
        reproducible: the exact-hash dedup below is order-dependent (first
        occurrence of a hash wins), and the mirror decision determines whether
        a license is knowable at all.
        """
        seen_exact: set[str] = set()
        candidate_budget = (
            None if self._limit is None else self._limit * FETCH_OVERSAMPLE + 1
        )
        candidates_emitted = 0
        for row in self._reader.iter_rows(limit=None):
            self.stats.rows_seen += 1
            if candidate_budget is not None and candidates_emitted >= candidate_budget:
                self.stats.bump("scan_budget_exhausted")
                break

            if _META_SKILL_NAME_RE.match(row.path.rsplit("/", 1)[-1]):
                self.stats.bump("filename_meta_skill_tooling")
                continue
            if row.is_mirror:
                self.stats.mirror_rows += 1
                if row.origin_recoverable:
                    self.stats.mirror_origin_recovered += 1
                else:
                    self.stats.bump("mirror_origin_unrecoverable")
                    continue
            if row.stars < self._star_prior:
                self.stats.below_star_prior += 1

            exact = row.exact_key
            if exact in seen_exact:
                self.stats.exact_duplicates += 1
                self.stats.bump("exact_duplicate")
                continue
            seen_exact.add(exact)
            candidates_emitted += 1
            yield row, exact

    def _fetch_in_waves(self, candidates: list) -> Iterator[tuple[Any, str, str, Optional[str], Optional[GateVerdict]]]:
        """PHASE B, lazily and in row order. With a limit, each wave fetches only
        2x the admissions PHASE C still needs (at least `fetch_workers`), and the
        next wave is sized after PHASE C has counted this one -- so a limit of N
        no longer fetches all N*FETCH_OVERSAMPLE candidates up front (the ~5x
        over-fetch in docs/ingestion_review.md). Outcomes are consumed in
        submission order, so every count and dedup winner is the same as one big
        batch; only fetches PHASE C would never have looked at are skipped."""
        pool = None
        if self._fetch_workers > 1 and len(candidates) > 1:
            from concurrent.futures import ThreadPoolExecutor

            pool = ThreadPoolExecutor(max_workers=self._fetch_workers)
        try:
            i = 0
            while i < len(candidates):
                if self._limit is None:
                    wave = len(candidates) - i
                else:
                    remaining = self._limit - self.stats.admitted
                    if remaining <= 0:
                        # PHASE C checks the limit before reading an outcome: a placeholder lets it
                        # record `limit_reached` exactly as before, without fetching another row.
                        yield (None, "", "", None, None)
                        return
                    wave = max(self._fetch_workers, 2 * remaining)
                chunk = candidates[i:i + wave]
                i += len(chunk)
                if pool is not None and len(chunk) > 1:
                    yield from pool.map(self._fetch_and_gate, chunk)
                else:
                    yield from (self._fetch_and_gate(c) for c in chunk)
        finally:
            if pool is not None:
                pool.shutdown(wait=True)

    def _fetch_and_gate(self, candidate: tuple[Any, str]) -> tuple[Any, str, str, Optional[str], Optional[GateVerdict]]:
        """PHASE B -- the only parallel stage. Pure and order-independent."""
        row, exact = candidate
        text, fetch_reason = self._raw_fetcher(row)
        if fetch_reason is not None:
            return row, exact, text, fetch_reason, None
        verdict = gate_text(text, fallback_name=row.fallback_name, rows=row.lines)
        return row, exact, text, None, verdict

    def discover(self) -> Iterator[SourceRef]:
        """Idempotent: the first call runs the gates, later calls replay.

        `run_skill_ingestion` drives the adapter by calling `discover()`
        itself, and this module's pilot needs the same refs beforehand to
        measure bytes. Without memoisation the SECOND call re-ran the near-dup
        index -- which is instance state and already held every admitted
        skill -- so it classified all 2,000 rows as near-duplicates of
        themselves and yielded nothing. The observed symptom was a clean
        `errors: 0` run that produced zero procedures, which is exactly the
        kind of silent zero a pilot must never report as a result.

        Replay also means the gate is genuinely once-per-run: a second
        `discover()` cannot re-hit the network or re-consume GitHub rate
        limit, and the disposition counts stay the counts of one pass.
        """
        if self._replayed is None:
            self._replayed = list(self._discover_once())
        return iter(self._replayed)

    def _discover_once(self) -> Iterator[SourceRef]:
        # PHASE B is the network stage and the only one worth parallelising.
        # raw.githubusercontent.com is CDN-backed and not under the REST API's
        # 60/hr budget, so a small bounded pool is the polite shape. Results
        # are consumed back in submission order, so the disposition counts,
        # the exact-dedup winner and the near-dup "first seen" key are
        # identical to a serial run. Nothing downstream observes completion
        # order.
        candidates = list(self._candidates())

        # PHASE C -- sequential, in row order. The admitted limit is enforced
        # HERE, because this is where `admitted` is counted; see
        # FETCH_OVERSAMPLE for why it cannot be enforced in PHASE A.
        for row, exact, text, fetch_reason, verdict in self._fetch_in_waves(candidates):
            if self._limit is not None and self.stats.admitted >= self._limit:
                self.stats.bump("limit_reached")
                break
            if fetch_reason is not None:
                self.stats.bump(fetch_reason)
                continue
            self.stats.fetched += 1

            assert verdict is not None
            if verdict.disposition in ("reject", "quarantine"):
                self.stats.bump(verdict.reason)
                self.verdicts.append(verdict)
                continue

            near = self._near_dup.check_and_add(text, verdict.name)
            if near is not None:
                self.stats.near_duplicates += 1
                self.stats.bump("near_duplicate_of_" + near)
                continue

            origin_spdx = None
            if self._enforce_license:
                decision = self._decide_license(row)
                if decision != "ALLOW":
                    self.stats.bump(f"license_{decision.lower()}")
                    continue
                # carried on the artifact: an attribution license (CC-BY-4.0) is recorded on the IngestionContext
                # from it (skill_ingestion._open_ingestion_provenance), for the credit and for license-takedown
                spdx_for = getattr(self._license_resolver, "spdx_for", None)
                origin_spdx = spdx_for(row.origin_repo) if callable(spdx_for) else None

            self.stats.admitted += 1
            parts = html_url_parts(row.html_url)
            ref = parts[1] if parts else "HEAD"
            self._cache[exact] = SourceArtifact(
                source_type=self.source_type,
                uri=row.html_url,
                content=text,
                content_hash=compute_content_hash(text),
                repository=row.origin_repo,
                path=row.origin_path,
                commit=ref,
                source_id=DATASET_REPO,
                license_metadata={
                    "dataset": DATASET_REPO,
                    "dataset_revision": DATASET_REVISION,
                    "row_content_hash": row.content_hash,
                    "row_source": row.source,
                    "row_stars": row.stars,
                    "dataset_repo": row.repo,
                    "dataset_path": row.path,
                    "origin_repo": row.origin_repo,
                    "origin_recoverable": row.origin_recoverable,
                    "is_mirror": row.is_mirror,
                    "gate_version": GATE_VERSION,
                    **({"spdx_id": origin_spdx} if origin_spdx else {}),
                },
            )
            yield SourceRef(
                uri=row.html_url,
                repository=row.origin_repo,
                path=row.origin_path,
                commit=ref,
                source_id=DATASET_REPO,
            )

    def _decide_license(self, row: SkillRow) -> str:
        if self._license_resolver is None:
            self.stats.bump("license_gate_unconfigured")
            return "ALLOW"
        decision = self._license_resolver(row.origin_repo)
        if self.stats.license_gate is None:
            self.stats.license_gate = {}
        self.stats.license_gate[decision] = self.stats.license_gate.get(decision, 0) + 1
        return decision

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        for artifact in self._cache.values():
            if artifact.uri == ref.uri:
                return artifact
        raise KeyError(f"no cached artifact for {ref.uri!r} -- discover() must run first")

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


def html_url_host(url: str) -> str:
    return urlparse(url or "").netloc


# ---------------------------------------------------------------------------
# License resolution
# ---------------------------------------------------------------------------
class GitHubLicenseResolver:
    """One cached SPDX lookup per repository, decided by the shared allowlist.

    WHY THE ALLOWLIST AND NOT A BLOCKLIST
        `repo_license_policy` is already the substrate's stated answer to this
        question, and it is an allowlist on purpose: a blocklist that defaults
        to permissive silently admits every license nobody thought about, and
        that omission is invisible after the fact. Its hard floor rejects the
        copyleft / non-commercial families regardless of configuration, and its
        stated posture is that NOASSERTION and an absent LICENSE are UNKNOWN,
        not permissive. Reusing it verbatim -- rather than writing a second,
        looser check here -- is what makes the admission decision auditable
        from the verdict's `allowlist_version`.

    WHY A SEPARATE ENDPOINT
        The repository metadata endpoint carries `license.spdx_id` for the
        license GitHub *detected*, which is Licensee's classification, not our
        judgment. `/license` returns the file plus the same detected id. We
        read the detected id and hand it to `classify_spdx` unaltered: this
        module does not classify licenses, it resolves a pointer and applies
        the disclosed policy.

    RATE LIMITS ARE REPORTED, NOT HIDDEN
        Unauthenticated GitHub allows 60 requests/hour per IP; a token raises
        it to 5,000/hour. A 2,000-row pilot touches far fewer than 60 distinct
        origin repositories only if the corpus is mirror-heavy, which it is --
        but a pilot that silently gives up on `403`/`429` would report a
        "license_unknown" quarantine count that is really a rate-limit count.
        `rate_limited` is tracked separately for exactly that reason.
    """

    def __init__(self, *, allow: Optional[list[str]] = None, min_interval: float = 0.0) -> None:
        self._cache: dict[str, tuple[str, Optional[str]]] = {}
        self._allow = allow
        self._min_interval = min_interval
        self._last_call = 0.0
        self.lookups = 0
        self.rate_limited = 0
        self.api_errors = 0

    def __call__(self, owner_repo: str) -> str:
        cached = self._cache.get(owner_repo)
        if cached is not None:
            return cached[0]
        spdx = self._fetch_spdx(owner_repo)
        decision, reason = self._decide(spdx)
        self._cache[owner_repo] = (decision, reason)
        self._cache[f"__id__{owner_repo}"] = (decision, spdx)
        return decision

    def spdx_for(self, owner_repo: str) -> Optional[str]:
        self(owner_repo)
        return self._cache.get(f"__id__{owner_repo}", (None, None))[1]

    def reason_for(self, owner_repo: str) -> Optional[str]:
        self(owner_repo)
        return self._cache.get(owner_repo, (None, None))[1]

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _fetch_spdx(self, owner_repo: str) -> Optional[str]:
        if not owner_repo or owner_repo.count("/") != 1:
            return None
        self.lookups += 1
        owner, repo = owner_repo.split("/", 1)
        for template in (
            "https://api.github.com/repos/{owner}/{repo}",
            "https://api.github.com/repos/{owner}/{repo}/license",
        ):
            url = template.format(owner=quote(owner, safe=""), repo=quote(repo, safe=""))
            self._throttle()
            try:
                status, body = _http_get(url, headers=_github_headers())
            except Exception as exc:  # noqa: BLE001 -- an outage is a row outcome
                log.warning("license lookup transport failure for %s: %s", owner_repo, exc)
                self.api_errors += 1
                return None
            if status == 403 or status == 429:
                self.rate_limited += 1
                return None
            if status == 404:
                continue
            if status != 200:
                self.api_errors += 1
                continue
            try:
                import json

                payload = json.loads(body)
            except ValueError:
                self.api_errors += 1
                continue
            if not isinstance(payload, dict):
                continue
            license_field = payload.get("license")
            if isinstance(license_field, dict):
                spdx = license_field.get("spdx_id")
                if isinstance(spdx, str) and spdx:
                    return spdx
        return None

    def _decide(self, spdx: Optional[str]) -> tuple[str, str]:
        from app.services.repo_license_policy import classify_spdx

        # this path records the credit (spdx_id on the artifact -> the IngestionContext), so it may admit CC-BY-4.0
        verdict = classify_spdx(spdx, allow=self._allow, records_attribution=True)
        return verdict.decision, verdict.reason

    def stats(self) -> dict[str, Any]:
        return {
            "repos_looked_up": self.lookups,
            "rate_limited": self.rate_limited,
            "api_errors": self.api_errors,
        }
