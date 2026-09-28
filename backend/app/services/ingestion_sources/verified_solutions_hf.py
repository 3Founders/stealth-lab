"""
Verified-solution corpora (SWE-rebench, SWE-rebench-V2, SWE-bench-extra,
SWE-Gym) as an ingestion source.

WHAT THIS IS FOR
Verified solutions are the one corpus class that carries its own check: an
issue text, a gold patch, and a test patch with an explicit FAIL_TO_PASS
list. `docs/findings.md` says the product direction is routing with a check,
so this is the highest-value population we ingest. Each accepted row becomes
a Procedure whose check is the F2P predicate, with the gold patch preserved
through `verified_solutions.preserve()`.

WHAT THE RESEARCH FOUND (2026-09-28, sources in .scratch/ingestion/)
Four things here are not obvious and are load-bearing enough to state up
front, because each one would silently corrupt the pipeline if assumed:

1. `FAIL_TO_PASS` / `PASS_TO_PASS` are real `list[str]` columns in all four
   datasets -- NOT JSON strings, which is how SWE-bench itself stores them.
   `_as_str_list` accepts both because a reader written for the card rather
   than the parquet is exactly the failure this guards against: the
   SWE-bench-extra card documents these columns as `str` and is wrong.

2. There is NO `resolved` / `resolved_by` field in any of the four. Every
   row is a validated task by construction (the upstream pipelines ran the
   test patch and then the solution patch). Do not code against `resolved`.

3. The four sources do NOT agree on the license field, and this is the
   single biggest engineering item in this module:
     - SWE-bench-extra `license`   -- lowercase SPDX slug, 8 values, 0 nulls
     - SWE-rebench-V2  `license`   -- SPDX-ish slug, 16 values, 348 nulls,
                                      plus 5,038 rows of `custom-check-github`
                                      (GitHub's "we found a LICENSE we could
                                      not map" placeholder -- a
                                      non-identification, like NOASSERTION)
     - SWE-rebench V1  `license_name` -- a GitHub Licensee DISPLAY NAME with
                                      ~56 spellings of ~15 licenses, 1.5-2.5%
                                      null. Fed unmodified to
                                      `classify_spdx` it quarantines ~96% of
                                      the largest Python corpus for a reason
                                      that has nothing to do with licensing.
     - SWE-Gym         ABSENT     -- no per-instance license at all
   So `normalize_spdx()` below is a hard prerequisite, and an unmappable
   value returns None (-> QUARANTINE) rather than a guess. The dataset card
   license (CC-BY-4.0 / MIT) covers the packaging only; every card says so
   and says to respect each repository's own license.

4. Only SWE-rebench-V2 is multi-language (20 codes, measured: py 22.6%,
   go 19.2%, ts 13.1%, js 12.9%, rust 9.7%, ...). The other three are
   Python-only. The paper's own §3.7 language figures (py 21.6% / go 20.6%)
   disagree with the released data; the measured table is the one used here.

WHY A DISPLAY-NAME NORMALIZER AND NOT `identify_spdx_from_text`
`identify_spdx_from_text` is a license-BODY header matcher. These rows carry
no license text, only a name. There is nothing for it to match, so a
normalizer keyed on the observed name spellings is the only thing that can
work -- and it is a lookup table, not a guesser, precisely so that an
unseen spelling fails closed into QUARANTINE.

SCOPE / PROVENANCE (hard rule 2)
Everything that survives the gate carries source id, revision, instance id,
base_commit, extractor version and SPDX id in `license_metadata`, and
`commit` on the artifact so `verified_solutions.durable_locator` can build a
real `source_locator`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)

log = logging.getLogger(__name__)

# Bumped when the synthesized document or the row mapping changes in a way
# that would alter `content_hash` for an unchanged upstream row. A change
# here invalidates dedup against previously ingested rows, so it is stamped
# into the content rather than left implicit.
EXTRACTOR_VERSION = "verified_solutions_hf@1"

# One release per source, pinned. A floating revision would silently change
# the corpus under a dedup key, which is the failure mode a golden set and an
# `ingested_artifacts` table both exist to prevent.
SOURCES: dict[str, dict[str, Any]] = {
    "swe_rebench": {
        "dataset_repo": "nebius/SWE-rebench",
        "revision": "89cdfbab4ab1bd8f5a658bb212d1b63624f4f881",
        "split": "filtered",
        "license_field": "license_name",
        "language": "python",
        # `test` has a null docker_image on 69.3% of rows; `filtered` is
        # exactly the subset with a pullable per-instance image.
        "note": "filtered split == test rows with a non-null docker_image",
    },
    "swe_rebench_v2": {
        "dataset_repo": "nebius/SWE-rebench-V2",
        "revision": "475dd5e8703bb5fb22dd3c60b5d038b019eba1e0",
        "split": "train",
        "license_field": "license",
        "language": None,
        "note": "multi-language; has meta.llm_metadata B1-B6 flags",
    },
    "swe_bench_extra": {
        "dataset_repo": "nebius/SWE-bench-extra",
        "revision": "11dcbfb30e19552df2a2f8030bd764adc95c92a5",
        "split": "train",
        "license_field": "license",
        "language": "python",
        "note": "spurious __index_level_0__ column must be dropped",
    },
    "swe_gym": {
        "dataset_repo": "SWE-Gym/SWE-Gym",
        "revision": "bb94ed9e39bbeb96a7fcbfb533b80f25a7fd59cb",
        "split": "train",
        "license_field": None,
        "language": "python",
        "note": "NO per-instance license field; every row is a license reject",
    },
}

# ---------------------------------------------------------------------------
# License: display name -> SPDX
# ---------------------------------------------------------------------------
# Every key is a spelling actually observed in the corpora (researched
# 2026-09-28). Unknown spellings return None on purpose: `classify_spdx`
# turns None into QUARANTINE, which is the correct posture for a license we
# cannot identify, versus a REJECT which asserts we did identify it and it
# was copyleft.
_DISPLAY_NAME_TO_SPDX: dict[str, str] = {
    # MIT
    "mit license": "MIT",
    "mit": "MIT",
    "expat": "MIT",
    "mit no attribution": "MIT-0",
    "mit-cmu license": "MIT-CMU",
    "mit-cmu": "MIT-CMU",
    # Apache
    "apache license 2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "mit/apache-2.0 dual license": "MIT",
    "apache license 2.0 or mit license": "Apache-2.0",
    # BSD family
    'bsd 3-clause "new" or "revised" license': "BSD-3-Clause",
    "bsd 3-clause clear license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "modified bsd license": "BSD-3-Clause",
    "bsd license": "BSD-2-Clause",
    'bsd 2-clause "simplified" license': "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "simplified bsd license": "BSD-2-Clause",
    'bsd 4-clause "original" or "old" license': "BSD-4-Clause",
    "bsd-4-clause": "BSD-4-Clause",
    "original bsd license": "BSD-4-Clause",
    "isc license": "ISC",
    "isc": "ISC",
    # Public-domain-ish
    "the unlicense": "Unlicense",
    "unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0",
    "cc0 1.0 universal": "CC0-1.0",
    "zope public license 2.1": "ZPL-2.1",
    "zpl 2.1": "ZPL-2.1",
    "zpl-2.1": "ZPL-2.1",
    "zope public license": "ZPL-2.1",
    # Permissive but not on DEFAULT_ALLOWLIST -> normalize, then let the
    # policy decide. We do not silently widen the allowlist here.
    "academic free license 3.0": "AFL-3.0",
    "afl-3.0": "AFL-3.0",
    "mozilla public license 2.0": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "psf license agreement": "PSF-2.0",
    "public domain": "Unlicense",
}

# Values that are explicitly non-identifications, not licenses. Normalizing
# any of these to a real id would be inventing a license grant.
_NON_IDENTIFYING: frozenset[str] = frozenset({
    "custom-check-github",
    "noassertion",
    "unknown",
    "none",
    "null",
    "",
})

# Copyleft spellings that must survive normalization as REJECT, never be
# dropped to None (-> QUARANTINE). A GPL row silently becoming a quarantine
# instead of a reject would understate the reason counts.
_COPYLEFT_PREFIXES: tuple[str, ...] = ("gpl", "agpl", "lgpl", "sspl", "cc-by-nc", "cc-by-nd")

_WS_RE = re.compile(r"\s+")


def normalize_spdx(raw: Any) -> Optional[str]:
    """Map a dataset license value to an SPDX id, or None when unmappable.

    Accepts both the SPDX slugs (V2, SWE-bench-extra) and the GitHub display
    names (V1). Already-normalized ids pass through upper-cased-after-
    lower-casing so the caller can hand the result straight to
    `repo_license_policy.classify_spdx`, which upper-cases internally.
    """
    if raw is None:
        return None
    text = _WS_RE.sub(" ", str(raw)).strip()
    if text.lower() in _NON_IDENTIFYING:
        return None
    lowered = text.lower()
    if lowered in _DISPLAY_NAME_TO_SPDX:
        return _DISPLAY_NAME_TO_SPDX[lowered]
    if any(lowered.startswith(prefix) for prefix in _COPYLEFT_PREFIXES):
        return text
    if re.fullmatch(r"[A-Za-z0-9.+-]+", text) and "-" in text:
        return text
    return None


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------

def _as_str_list(value: Any) -> list[str]:
    """FAIL_TO_PASS is a real list in all four corpora, but a JSON string in
    SWE-bench itself and in at least one published card. Accept both, and
    never raise on a shape we do not recognise -- a malformed test list is a
    rejection reason, not a crashed batch."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return [text]
        if isinstance(parsed, list):
            return [str(v) for v in parsed if v is not None]
        return [str(parsed)]
    return [str(value)]


@dataclass(frozen=True)
class VerifiedSolutionRow:
    source_key: str
    dataset_repo: str
    revision: str
    split: str
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    fail_to_fail: tuple[str, ...]
    pass_to_fail: tuple[str, ...]
    license_raw: Optional[str]
    license_spdx: Optional[str]
    language: str
    environment_setup_commit: Optional[str]
    docker_image: Optional[str]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """(repo, base_commit) -- the same code state reached by two PRs is
        one dedup target, and instance_id alone would not catch it."""
        return f"{self.repo}@{self.base_commit}"


def _meta_field(meta: Any, *path: str) -> Any:
    cur = meta
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def row_to_verified_solution_row(
    raw: dict[str, Any], *, source_key: str
) -> VerifiedSolutionRow:
    """Map one upstream row. Field names differ per source, so every lookup
    is defensive; a missing field becomes empty and is rejected downstream
    with a named reason rather than raising here."""
    spec = SOURCES[source_key]
    license_field = spec["license_field"]
    license_raw = raw.get(license_field) if license_field else None
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    language = raw.get("language") or spec["language"] or "unknown"
    return VerifiedSolutionRow(
        source_key=source_key,
        dataset_repo=spec["dataset_repo"],
        revision=spec["revision"],
        split=spec["split"],
        instance_id=str(raw.get("instance_id") or "").strip(),
        repo=str(raw.get("repo") or "").strip(),
        base_commit=str(raw.get("base_commit") or "").strip(),
        problem_statement=str(raw.get("problem_statement") or "").strip(),
        patch=str(raw.get("patch") or ""),
        test_patch=str(raw.get("test_patch") or ""),
        fail_to_pass=tuple(_as_str_list(raw.get("FAIL_TO_PASS"))),
        pass_to_pass=tuple(_as_str_list(raw.get("PASS_TO_PASS"))),
        fail_to_fail=tuple(_as_str_list(raw.get("FAIL_TO_FAIL"))),
        pass_to_fail=tuple(_as_str_list(raw.get("PASS_TO_FAIL"))),
        license_raw=None if license_raw is None else str(license_raw),
        license_spdx=normalize_spdx(license_raw),
        language=str(language),
        environment_setup_commit=(
            str(raw["environment_setup_commit"])
            if raw.get("environment_setup_commit")
            else None
        ),
        docker_image=(
            str(raw.get("docker_image") or raw.get("image_name") or "") or None
        ),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Held-out exclusion
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeldOutSet:
    instance_ids: frozenset[str]
    repos: frozenset[str]
    design_paths: tuple[str, ...]


_HELD_OUT_SPLITS: tuple[str, ...] = ("test", "calibration")


def load_held_out(
    design_paths: Iterable[Path], *, include_repos: bool = True
) -> HeldOutSet:
    """Collect the ids we must never ingest, from our own experiment designs.

    `test` and `calibration` are the held-out splits by the rule in the build
    prompts. `scored_repos` is included by default and that default is a
    judgement call worth stating: our experiments measure on those
    repositories, so ingesting OTHER instances from the same repository
    teaches the agent the same codebase the held-out instances live in. The
    instance-level rule alone would not catch that. Set `include_repos=False`
    to relax it.
    """
    ids: set[str] = set()
    repos: set[str] = set()
    seen: list[str] = []
    for path in design_paths:
        if not path.exists():
            log.warning("held-out design missing: %s", path)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for split in _HELD_OUT_SPLITS:
            ids.update(str(v) for v in data.get(split, []) or [])
        if include_repos:
            repos.update(str(v).lower() for v in data.get("scored_repos", []) or [])
        seen.append(str(path))
    return HeldOutSet(
        instance_ids=frozenset(ids),
        repos=frozenset(repos),
        design_paths=tuple(seen),
    )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

@dataclass
class GateCounters:
    rows_seen: int = 0
    accepted: int = 0
    duplicate: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    seen_keys: set[str] = field(default_factory=set)
    language_counts: dict[str, int] = field(default_factory=dict)
    license_counts: dict[str, int] = field(default_factory=dict)
    quality_counts: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


def evaluate_row(
    row: VerifiedSolutionRow,
    *,
    held_out: HeldOutSet,
    license_classify: Any,
    counters: GateCounters,
) -> Optional[str]:
    """Return None if the row is admissible, else the rejection reason.

    Order matters and is deliberate: cheap structural checks and the
    held-out exclusion run before the license call, so a held-out id is
    counted as held-out rather than as whatever else is wrong with it.
    """
    counters.rows_seen += 1

    if not row.instance_id:
        return "missing_instance_id"
    if not row.repo or not row.base_commit:
        return "missing_repo_or_commit"
    if not row.problem_statement:
        return "missing_problem_statement"
    if not row.patch:
        return "missing_patch"
    if not row.test_patch:
        return "missing_test_patch"
    if not row.fail_to_pass:
        return "no_fail_to_pass"

    if row.instance_id in held_out.instance_ids:
        return "held_out_instance"
    if row.repo.lower() in held_out.repos:
        return "held_out_repo"

    if row.fail_to_fail:
        counters.quality_counts["has_fail_to_fail"] = (
            counters.quality_counts.get("has_fail_to_fail", 0) + 1
        )
        return "flaky_fail_to_fail"
    if row.pass_to_fail:
        counters.quality_counts["has_pass_to_fail"] = (
            counters.quality_counts.get("has_pass_to_fail", 0) + 1
        )
        return "gold_patch_breaks_test"

    key = row.dedup_key
    if key in counters.seen_keys:
        counters.duplicate += 1
        return "duplicate"
    counters.seen_keys.add(key)

    if row.license_spdx is None:
        counters.license_counts["unmappable"] = (
            counters.license_counts.get("unmappable", 0) + 1
        )
        return "license_unmappable"

    verdict = license_classify(row.license_spdx)
    decision = getattr(verdict, "decision", verdict)
    counters.license_counts[decision] = counters.license_counts.get(decision, 0) + 1
    if decision != "ALLOW":
        return f"license_{decision.lower()}"

    counters.accepted += 1
    counters.language_counts[row.language] = (
        counters.language_counts.get(row.language, 0) + 1
    )
    return None


# ---------------------------------------------------------------------------
# Document synthesis
# ---------------------------------------------------------------------------

_FENCE = "```"


def build_skill_document(row: VerifiedSolutionRow) -> str:
    """Synthesize a SKILL.md-shaped document from the issue + gold patch.

    The compiler's only parsing entry point is `parse_skill_md()`, and there
    is deliberately no second one, so a corpus row has to arrive as a
    SKILL.md-shaped document rather than as a new format. This mirrors what
    the CI-workflow source already does: frontmatter plus one numbered step
    per source step. The FAIL_TO_PASS list becomes the check, which is the
    whole point of this corpus -- the procedure carries a verifiable outcome.
    """
    name = f"{row.repo.split('/')[-1]}-{row.instance_id.rsplit('-', 1)[-1]}"
    description = (
        f"Verified fix for {row.instance_id} in {row.repo} "
        f"at commit {row.base_commit[:12]}."
    )
    lines: list[str] = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "---",
        "",
        f"# {name}",
        "",
        "## Issue",
        "",
        row.problem_statement,
        "",
        "## Verified solution",
        "",
        "The following patch was applied upstream and is known to turn the "
        "listed tests from failing to passing.",
        "",
        f"{_FENCE}diff",
        row.patch,
        _FENCE,
        "",
        "## Check",
        "",
        "These tests fail before the patch and pass after it. A run that does "
        "not reproduce this transition has not verified the solution.",
        "",
    ]
    for test in row.fail_to_pass:
        lines.append(f"- `{test}`")
    lines.append("")
    if row.pass_to_pass:
        lines.extend([
            "These tests passed before and must still pass afterwards.",
            "",
        ])
        for test in row.pass_to_pass:
            lines.append(f"- `{test}`")
        lines.append("")
    lines.extend([
        "## Test patch",
        "",
        f"{_FENCE}diff",
        row.test_patch,
        _FENCE,
        "",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class VerifiedSolutionSource:
    """`SourceAdapter` over one verified-solution corpus.

    Sync by contract (see `base.py`): `discover()` enumerates instance refs
    and `fetch()` materializes one row into a document. The HF read itself
    is streamed inside `_iter_raw_rows` so a 32k-row corpus never lands in
    memory at once.
    """

    source_type = "verified_solution"

    def __init__(
        self,
        source_key: str,
        *,
        held_out: HeldOutSet,
        license_classify: Any,
        counters: Optional[GateCounters] = None,
        row_limit: Optional[int] = None,
        rows: Optional[Iterable[dict[str, Any]]] = None,
    ) -> None:
        if source_key not in SOURCES:
            raise KeyError(f"unknown verified-solution source: {source_key!r}")
        self.source_key = source_key
        self.spec = SOURCES[source_key]
        self.held_out = held_out
        self.license_classify = license_classify
        self.counters = counters if counters is not None else GateCounters()
        self.row_limit = row_limit
        self._rows = rows

    @property
    def dataset_repo(self) -> str:
        return self.spec["dataset_repo"]

    def _iter_raw_rows(self) -> Iterator[dict[str, Any]]:
        if self._rows is not None:
            yield from self._rows
            return
        from datasets import load_dataset

        dataset = load_dataset(
            self.spec["dataset_repo"],
            split=self.spec["split"],
            revision=self.spec["revision"],
            streaming=True,
        )
        for raw in dataset:
            yield dict(raw)

    def iter_admissible(self, *, counters: Optional[GateCounters] = None) -> Iterator[tuple[VerifiedSolutionRow, str]]:
        """Yield (row, content) for every row that passes every gate."""
        produced = 0
        tally = counters if counters is not None else self.counters
        for raw in self._iter_raw_rows():
            if self.row_limit is not None and produced >= self.row_limit:
                return
            row = row_to_verified_solution_row(raw, source_key=self.source_key)
            reason = evaluate_row(
                row,
                held_out=self.held_out,
                license_classify=self.license_classify,
                counters=tally,
            )
            if reason is not None:
                tally.reject(reason)
                continue
            produced += 1
            yield row, build_skill_document(row)

    def discover(self) -> Iterator[SourceRef]:
        for row, _content in self.iter_admissible():
            yield SourceRef(
                uri=f"hf://{row.dataset_repo}@{row.revision}/{row.split}/{row.instance_id}",
                repository=row.repo,
                commit=row.base_commit,
                source_id=row.instance_id,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        # A private counter set, because `discover()` and `fetch()` are two
        # passes over the same rows and the dedup set is pass-local state.
        # Sharing it would make every ref discovered by `discover()` a
        # duplicate of itself by the time `fetch()` re-evaluated it.
        for row, content in self.iter_admissible(counters=GateCounters()):
            if row.instance_id != ref.source_id:
                continue
            return SourceArtifact(
                source_type=self.source_type,
                uri=ref.uri,
                content=content,
                content_hash=compute_content_hash(content),
                repository=row.repo,
                path=f"instances/{row.instance_id}",
                commit=row.base_commit,
                source_id=row.instance_id,
                license_metadata={
                    # The raw shipped value is kept for audit; `spdx_id` is
                    # what arms `screening.spdx_license_signal` downstream.
                    "license": row.license_raw,
                    "spdx_id": row.license_spdx,
                },
            )
        raise KeyError(f"no admissible row for source_id={ref.source_id!r}")

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash
