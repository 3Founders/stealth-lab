"""Structural Goal features from a verified patch (docs/plan_2026-10_priors_library_survey.md §2.3).

The size and spread of the fix that solved a task are among the strongest known predictors of how hard
it is for an agent (SWE-bench difficulty studies: files touched, lines changed, cross-package edits).
They need no training: computed once from the reference patch, stored with the evidence item, and
appended to the Goal's phi next to the PCA of its embedding, where the existing regression W learns
their weights. A Goal without a known patch gets the population mean (zeros) plus a missing flag.

numpy only.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Optional, Sequence

import numpy as np

FEATURES = ("log_files", "log_hunks", "log_lines_added", "log_lines_removed", "n_languages", "log_tests",
            "cross_package", "missing")

_LANG = {".py": "python", ".pyi": "python", ".js": "js", ".jsx": "js", ".mjs": "js", ".ts": "ts", ".tsx": "ts",
         ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".scala": "scala", ".c": "c", ".h": "c",
         ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
         ".swift": "swift", ".m": "objc", ".sh": "shell", ".sql": "sql", ".yml": "yaml", ".yaml": "yaml",
         ".toml": "toml", ".json": "json", ".md": "docs", ".rst": "docs", ".txt": "docs", ".cfg": "config",
         ".ini": "config", ".html": "html", ".css": "css"}
_FILE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)


def patch_stats(patch: str, *, tests: Optional[int] = None) -> dict[str, Any]:
    """Raw counts from a unified diff (git format)."""
    files = [m.group(2) for m in _FILE.finditer(patch or "")]
    hunks = added = removed = 0
    for line in (patch or "").splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    langs = {_LANG.get("." + f.rsplit(".", 1)[-1].lower(), "other") for f in files if "." in f.rsplit("/", 1)[-1]}
    tops = {f.split("/", 1)[0] if "/" in f else "." for f in files}
    return {"files": len(files), "hunks": hunks, "lines_added": added, "lines_removed": removed,
            "languages": len(langs), "tests": tests, "packages": len(tops)}


def vector(stats: Optional[Mapping[str, Any]]) -> np.ndarray:
    """Unstandardised feature vector in FEATURES order."""
    if not stats or stats.get("files") is None:
        return np.array([0.0] * (len(FEATURES) - 1) + [1.0])
    tests = stats.get("tests")
    return np.array([math.log1p(stats["files"]), math.log1p(stats["hunks"]), math.log1p(stats["lines_added"]),
                     math.log1p(stats["lines_removed"]), float(stats.get("languages") or 0),
                     math.log1p(tests) if tests is not None else 0.0,
                     1.0 if (stats.get("packages") or 0) > 1 else 0.0, 0.0])


def standardisation(rows: Sequence[Optional[Mapping[str, Any]]]) -> tuple[np.ndarray, np.ndarray]:
    """(mean, sd) over the rows that HAVE features (the missing flag is centred over all rows)."""
    vecs = np.stack([vector(r) for r in rows]) if rows else np.zeros((0, len(FEATURES)))
    have = vecs[:, -1] == 0 if len(vecs) else np.zeros(0, dtype=bool)
    mean, sd = np.zeros(len(FEATURES)), np.ones(len(FEATURES))
    if have.any():
        mean[:-1] = vecs[have, :-1].mean(axis=0)
        spread = vecs[have, :-1].std(axis=0)
        sd[:-1] = np.where(spread > 1e-9, spread, 1.0)
    if len(vecs):
        mean[-1] = float(vecs[:, -1].mean())
    return mean, sd


def standardised(stats: Optional[Mapping[str, Any]], mean: np.ndarray, sd: np.ndarray) -> np.ndarray:
    v = vector(stats)
    out = (v - mean) / sd
    if v[-1] == 1.0:                      # unknown patch: the mean of every feature, flag on
        out[:-1] = 0.0
    return out
