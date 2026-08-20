"""
Slot binder registry (procedure extraction, memory-substrate map).
Top-level, not inside procedure_extraction/, because instantiation
(a later pass -- seeding an HTN plan or an Agent memory_block from a
retrieved procedure) will need the same registry to RESOLVE a bound slot
back into a real value at execution time; extraction only needs to name
which binder covers a slot.

Every binder wraps a producer that ALREADY EXISTS and is already tested
-- no new analysis capability is written here. The registry only records
WHICH structural producer a slot binds from, which is precisely the part
real experience contributes and a hand-authored procedure template
cannot: a debugging procedure learned from an episode where the failure
traced through the call graph should say so, one learned from an episode
where only naming-convention test discovery mattered should say that
instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

_REGISTRY: dict[str, "BinderSpec"] = {}


@dataclass
class BinderSpec:
    name: str
    description: str
    # (repo_root, seed_files) -> list[str] of file paths this binder
    # would surface -- same shape every local_retrieval.py producer
    # already returns, deliberately, so coverage-checking (below) is a
    # plain set comparison against real output, not a translation.
    produce: Callable[[str, list[str]], list[str]]


def register_binder(name: str, description: str = ""):
    def decorator(fn: Callable[[str, list[str]], list[str]]):
        _REGISTRY[name] = BinderSpec(name=name, description=description, produce=fn)
        return fn
    return decorator


def get_binder(name: str) -> Optional[BinderSpec]:
    return _REGISTRY.get(name)


def known_binder_names() -> list[str]:
    """What validators.py's V3 (slot integrity) checks a SlotSpec.binder
    against -- one exported function, so a new @register_binder is
    automatically valid to reference, no second list to keep in sync."""
    return list(_REGISTRY.keys())


@register_binder(
    "call_graph_reachable",
    "Files reachable from the episode's entry point via call_graph.py's "
    "BFS reachability -- appropriate when the episode's edits followed a "
    "call chain from a known symbol.",
)
def _call_graph_reachable(repo_root: str, seed_files: list[str]) -> list[str]:
    from app.services.local_retrieval import get_call_graph_ranked_names
    return get_call_graph_ranked_names(repo_root, seed_files)


@register_binder(
    "import_deps",
    "Files import-reachable from the seed via import_deps.py's tree-sitter "
    "extraction -- appropriate when the episode's edits followed module "
    "import structure rather than a call chain.",
)
def _import_deps(repo_root: str, seed_files: list[str]) -> list[str]:
    from app.services.import_deps import import_targets_for_many
    return import_targets_for_many(repo_root, seed_files)


@register_binder(
    "related_tests",
    "Test files discovered via related_tests.py's naming-convention "
    "matching -- appropriate when the episode's work centered on making "
    "a specific test suite pass.",
)
def _related_tests(repo_root: str, seed_files: list[str]) -> list[str]:
    from app.services.related_tests import related_test_files_for_many
    return related_test_files_for_many(repo_root, seed_files)


@register_binder(
    "relevant_symbols",
    "Symbol names extracted via code_index.py's outline() -- appropriate "
    "when the episode's slot is a NAME (a function/class to find or "
    "modify) rather than a file path.",
)
def _relevant_symbols(repo_root: str, seed_files: list[str]) -> list[str]:
    from app.services.local_retrieval import get_relevant_symbols
    return get_relevant_symbols(repo_root, seed_files)


@register_binder(
    "literal",
    "No structural producer applies -- the slot's value is a constant "
    "captured directly from the episode. The honest fallback when a "
    "read file matches no other binder's output; never silently omitted.",
)
def _literal(repo_root: str, seed_files: list[str]) -> list[str]:
    return list(seed_files)


def best_binder_for(repo_root: str, seed_files: list[str], target_files: set[str]) -> str:
    """
    The real slot-inference decision: which registered binder's output
    (seeded from `seed_files`) best COVERS `target_files` -- the files
    the episode actually read. Ties broken by registration order (call
    graph before import deps before tests before symbols) -- reflects
    that a call-chain match is a stronger, more specific signal than a
    naming-convention one, not an arbitrary ordering.

    Falls back to 'literal' if no registered producer covers anything --
    an honest admission that this episode's file selection wasn't
    explained by any known structural signal, not a forced, wrong match.
    """
    if not target_files:
        return "literal"

    best_name = "literal"
    best_coverage = 0
    for name in ("call_graph_reachable", "import_deps", "related_tests", "relevant_symbols"):
        spec = _REGISTRY[name]
        try:
            produced = set(spec.produce(repo_root, seed_files))
        except Exception:  # noqa: BLE001 -- a producer's own real failure
            # (e.g. a language it doesn't support) must not abort slot
            # inference; that slot just falls back to literal instead.
            continue
        coverage = len(produced & target_files)
        if coverage > best_coverage:
            best_coverage = coverage
            best_name = name
    return best_name
