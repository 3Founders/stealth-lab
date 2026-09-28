"""Held-out id exclusion for ingestion.

The Common rules in `.scratch/prompts/ingestion_build_prompts.md` require:

    Before any production ingestion of SWE-bench/SWE-rebench-family data,
    exclude the held-out ids in `experiments/swebench*/runs/design.json`
    (`test`, `calibration`) and record the exclusion count.

**No loader for this existed anywhere in `backend/app/`.** A repo-wide grep for
`design.json` returns 29 hits, every one of them under `experiments/`, `docs/`
or `.scratch/` -- nothing in `app/`, `scripts/` or `tests/`. This module is
that loader.

Design decisions
----------------
- **Fail closed.** A missing or unreadable design file raises
  `HeldOutUnavailable` rather than returning an empty set. An empty set reads
  as "nothing is held out" and would let held-out tasks straight into
  production. The failure mode we are guarding against is a *silent* one, so
  the error has to be loud.
- **Union of `test` and `calibration`.** The prompt names both; the plan doc
  (`docs/ingestion_sources_plan.md`) names only `test`. The union is the
  stricter reading and costs nothing, since an excluded id is simply never
  ingested.
- **Also returns `scored_repos`.** For step 6 this matters more than for the
  SWE-bench steps: a benchmark claim published on a repo whose CI history we
  ingested is still a claim on a held-out repo. The caller decides what to do
  with it; the loader just reports it rather than making that call silently.
- **Hash the design files.** A number in a run report is meaningless unless the
  design it excluded against is pinned. The report carries a sha256 per file.
- **Path is resolved from the repo root**, because `backend/` is the only
  importable package and `experiments/` is deliberately not importable. This
  module takes a `root` and reads the files as data, never importing them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

#: Splits that must never reach production ingestion.
HELD_OUT_SPLITS: tuple[str, ...] = ("test", "calibration")

#: `experiments/<name>/runs/design.json` for every design this repo maintains.
DESIGN_GLOBS: tuple[str, ...] = (
    "experiments/swebench/runs/design.json",
    "experiments/swebench_rebench/runs/design.json",
)


#: The TRACKED copy of the held-out ids (BLOCKERS.md I15). The designs above are gitignored, so on a fresh
#: clone, in CI or on a production worker they are absent; `experiments/export_held_out_ids.py` writes this file
#: from them (public ids and repos only, plus each design's sha256). Used whenever a design file is missing.
TRACKED_IDS: str = "experiments/held_out_ids.json"


class HeldOutUnavailable(RuntimeError):
    """A design file is missing or unreadable. Fail closed."""


def load_tracked_ids(path: Path, splits: Iterable[str] = HELD_OUT_SPLITS) -> "tuple[set[str], set[str], dict[str, Any]]":
    """(ids, repos, record) from the tracked list, or raise HeldOutUnavailable. The list must cover every
    requested split: a list exported for fewer splits would under-exclude."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldOutUnavailable(f"tracked held-out list is unreadable: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("instance_ids"), list):
        raise HeldOutUnavailable(f"tracked held-out list has no instance_ids: {path}")
    missing_splits = set(splits) - set(data.get("splits") or [])
    if missing_splits:
        raise HeldOutUnavailable(f"tracked held-out list does not cover splits {sorted(missing_splits)}: {path}")
    record = {"path": str(path).replace("\\", "/"), "sha256": hashlib.sha256(raw).hexdigest(),
              "sources": data.get("sources") or []}
    return ({str(v) for v in data["instance_ids"]}, {str(v) for v in data.get("scored_repos") or []}, record)


@dataclass(frozen=True)
class DesignSnapshot:
    path: str
    sha256: str
    dataset: str = ""
    dataset_revision: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "dataset": self.dataset,
                "dataset_revision": self.dataset_revision, "counts": dict(self.counts)}


@dataclass(frozen=True)
class HeldOutSet:
    """The union across every design file, plus the per-file detail needed to
    make the run report auditable."""

    ids: frozenset[str]
    scored_repos: tuple[str, ...]
    snapshots: tuple[DesignSnapshot, ...]
    missing: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.ids)

    def is_held_out(self, candidate: Any) -> bool:
        """Membership test tolerant of the id shapes we actually see: a bare
        `owner__repo-1234` instance id, a `repo` slug, or a full
        `owner/repo@commit` locator."""
        if candidate is None:
            return False
        text = str(candidate).strip()
        if not text:
            return False
        if text in self.ids:
            return True
        for repo in self.scored_repos:
            if text == repo or text.startswith(repo + "/") or text.startswith(repo + "@"):
                return True
        return False

    def filter(self, candidates: Iterable[Any]) -> "tuple[list[Any], list[Any]]":
        """Split into `(kept, excluded)`. The excluded list is what gets
        counted in the run report."""
        kept: list[Any] = []
        excluded: list[Any] = []
        for candidate in candidates:
            (excluded if self.is_held_out(candidate) else kept).append(candidate)
        return kept, excluded

    def as_dict(self) -> dict[str, Any]:
        return {
            "excluded_ids": len(self.ids),
            "scored_repos": list(self.scored_repos),
            "designs": [s.as_dict() for s in self.snapshots],
            "missing_designs": list(self.missing),
        }


def load_design(path: Path) -> DesignSnapshot:
    """Read one design file, or raise. Never returns partial data."""
    if not path.is_file():
        raise HeldOutUnavailable(f"held-out design file is missing: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HeldOutUnavailable(f"held-out design file is unreadable: {path}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldOutUnavailable(f"held-out design file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise HeldOutUnavailable(f"held-out design file is not an object: {path}")
    counts = {
        key: len(value)
        for key, value in data.items()
        if isinstance(value, list)
    }
    return DesignSnapshot(
        path=str(path).replace("\\", "/"),
        sha256=hashlib.sha256(raw).hexdigest(),
        dataset=str(data.get("dataset") or ""),
        dataset_revision=str(data.get("dataset_revision") or ""),
        counts=counts,
    )


def load_held_out(
    root: "str | Path",
    *,
    design_globs: Iterable[str] = DESIGN_GLOBS,
    splits: Iterable[str] = HELD_OUT_SPLITS,
    allow_missing: bool = False,
) -> HeldOutSet:
    """Load the union of held-out ids and scored repos across every design.

    `allow_missing=True` downgrades a missing file to an entry in `missing`,
    for a dry run that only wants to know what *would* be excluded. Any real
    ingestion must leave it False.
    """
    base = Path(root)
    ids: set[str] = set()
    repos: set[str] = set()
    snapshots: list[DesignSnapshot] = []
    missing: list[str] = []
    wanted = tuple(splits)
    globs = tuple(design_globs)

    # Any design missing (a fresh clone, CI, a worker): use the whole tracked list instead -- never a mix of
    # some designs and nothing for the rest (BLOCKERS.md I15).
    tracked = base / TRACKED_IDS
    if any(not (base / g).is_file() for g in globs) and tracked.is_file():
        t_ids, t_repos, record = load_tracked_ids(tracked, wanted)
        counts = {"instance_ids": len(t_ids), "scored_repos": len(t_repos)}
        return HeldOutSet(
            ids=frozenset(t_ids), scored_repos=tuple(sorted(t_repos)),
            snapshots=(DesignSnapshot(path=record["path"], sha256=record["sha256"], counts=counts),),
            missing=(),
        )

    for glob in globs:
        path = base / glob
        if not path.is_file():
            if not allow_missing:
                raise HeldOutUnavailable(
                    f"held-out design file is missing: {path}, and there is no tracked list at "
                    f"{TRACKED_IDS} (run experiments/export_held_out_ids.py where the designs exist). "
                    f"Pass allow_missing=True for a dry run only; a real ingestion must fail closed.")
            missing.append(glob)
            continue
        snapshots.append(load_design(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        for split in wanted:
            for value in data.get(split) or []:
                ids.add(str(value))
        for value in data.get("scored_repos") or []:
            repos.add(str(value))

    return HeldOutSet(
        ids=frozenset(ids),
        scored_repos=tuple(sorted(repos)),
        snapshots=tuple(snapshots),
        missing=tuple(missing),
    )
