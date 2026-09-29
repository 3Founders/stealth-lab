"""Stages S2/S3: score spans and files structurally, and rank them. Pure.

THE SCORE, AND WHAT EACH TERM IS FOR
    decisions   log(1 + cyclomatic - 1)      independent branches: straight-line code has none and scores nothing here
    nesting     max nesting depth, capped     control flow that has to be held in the head at once
    variety     log(1 + distinct callees)     a function that orchestrates many different things
    algorithmic loop / recursion / generator / regex-or-bitops / context manager / async: shapes of real logic
    intent      a docstring                   the author thought this worth explaining (small weight: it is cheap to add)
    size window a trapezoid over code lines   too small teaches nothing, too large does not fit a small model's context
    trivial     a named plumbing pattern      getters, delegates, stubs, data containers score ZERO, not "low"

    File importance = its best spans + how many OTHER files import it (centrality): a mid-sized module that half the
    repository depends on is more "important" than a large leaf. Tests are kept but weighed down (x0.4): how a project
    tests its hard parts is worth having, not worth ranking first.

The weights are hand-set and were checked by reading the ranked output on real repositories (see the cascade design doc),
not fitted. They are module constants so an offline test can pin them and a later measurement can retune them.
"""
from __future__ import annotations

import math
import posixpath
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from app.services.code_cascade.python_features import SpanFeatures

W_DECISIONS = 1.6
W_NESTING = 0.6
NESTING_CAP = 4
W_VARIETY = 0.9
W_INTENT = 0.3
BONUS = {"has_loop": 0.5, "has_recursion": 0.7, "is_generator": 0.4, "uses_regex_or_bitops": 0.4,
         "uses_context_manager": 0.3, "is_async": 0.3}
TEST_FILE_WEIGHT = 0.4
ROLE_WEIGHT = {"source": 1.0, "test": TEST_FILE_WEIGHT, "example": 0.5}
CENTRALITY_WEIGHT = 1.0
# code lines: nothing below LO, ramps to full credit at PEAK_LO, full through PEAK_HI, ramps to nothing at HI
SIZE_LO, SIZE_PEAK_LO, SIZE_PEAK_HI, SIZE_HI = 6, 15, 90, 160


def size_window(code_lines: int) -> float:
    if code_lines <= SIZE_LO or code_lines >= SIZE_HI:
        return 0.0
    if code_lines < SIZE_PEAK_LO:
        return (code_lines - SIZE_LO) / (SIZE_PEAK_LO - SIZE_LO)
    if code_lines <= SIZE_PEAK_HI:
        return 1.0
    return (SIZE_HI - code_lines) / (SIZE_HI - SIZE_PEAK_HI)


def span_score(f: SpanFeatures) -> float:
    if f.trivial_reason:
        return 0.0
    algorithmic = sum(weight for flag, weight in BONUS.items() if getattr(f, flag))
    raw = (W_DECISIONS * math.log1p(max(0, f.cyclomatic - 1))
           + W_NESTING * min(f.max_nesting, NESTING_CAP)
           + W_VARIETY * math.log1p(f.distinct_calls)
           + algorithmic
           + (W_INTENT if f.has_docstring else 0.0))
    return raw * size_window(f.code_lines)


# ---------------------------------------------------------------------------------------------- import graph

def module_name(path: str) -> str:
    """`pkg/sub/mod.py` -> `pkg.sub.mod`; `pkg/__init__.py` -> `pkg`."""
    stem = posixpath.splitext(path)[0]
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve(importer: str, imported: str, by_module: Mapping[str, str], by_suffix: Mapping[str, list[str]]) -> Optional[str]:
    if imported.startswith("."):
        level = len(imported) - len(imported.lstrip("."))
        base = importer.split("/")[:-1]
        base = base[: len(base) - (level - 1)] if level > 1 else base
        target = ".".join(base + ([imported.lstrip(".")] if imported.lstrip(".") else []))
        return by_module.get(target)
    if imported in by_module:
        return by_module[imported]
    # an absolute import of an in-repo module under a source root (src/pkg/mod.py imported as pkg.mod)
    candidates = by_suffix.get(imported, [])
    return candidates[0] if len(candidates) == 1 else None


def centrality(imports_by_file: Mapping[str, Iterable[str]]) -> dict[str, int]:
    """In-degree: how many DISTINCT other files import each file. Only edges that resolve inside the repository count."""
    by_module = {module_name(p): p for p in imports_by_file}
    by_suffix: dict[str, list[str]] = defaultdict(list)
    for mod, path in by_module.items():
        parts = mod.split(".")
        for i in range(1, len(parts)):
            by_suffix[".".join(parts[i:])].append(path)
    indegree: dict[str, set[str]] = defaultdict(set)
    for importer, imported_names in imports_by_file.items():
        for name in imported_names:
            target = _resolve(importer, name, by_module, by_suffix)
            if target and target != importer:
                indegree[target].add(importer)
            # `from pkg import mod` records `pkg`; also try pkg.<each imported symbol> is not available here, so the
            # package's own module gets the edge and its submodules only via explicit dotted imports.
    return {path: len(importers) for path, importers in indegree.items()}


@dataclass(frozen=True)
class RankedSpan:
    path: str
    features: SpanFeatures
    score: float
    is_test: bool
    role: str = "source"


def file_importance(spans: list[RankedSpan], indegree: int, *, role: str = "source") -> float:
    top = sorted((s.score for s in spans), reverse=True)[:3]
    if not top or top[0] <= 0:
        return 0.0
    body = top[0] + 0.5 * sum(top[1:])
    score = body + CENTRALITY_WEIGHT * math.log1p(indegree)
    return score * ROLE_WEIGHT.get(role, 1.0)
