"""
Best-effort, name-based static call-graph reachability over a repo checkout.

Aimed at Pattern A (see GRAPH_EXPERIMENT.md section 8): the largest unfixed
failure category is an agent editing only some of the files a fix genuinely
requires, because the missing file is reachable only by tracing a call from
the file it did find into a helper defined elsewhere -- not visible from the
issue text or a name/regex search alone.

NAME-BASED, NOT TYPE-RESOLVED. This is the same limitation code_index.py
already documents for itself: two unrelated functions sharing a name in
different files collide in a purely name-keyed index. That makes this an
advisory signal, not a proof -- see symbolic_htn_agent.py for how the two
tiers (advisory context vs. an optional narrow hard gate) are kept honest
about that distinction. callgraph_check.py measures the real hit rate against
known Pattern-A instances before either tier is trusted.

Pure and host-side, like code_index.py: no DB, no LLM, tree-sitter only.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from tree_sitter_language_pack import get_parser

import code_index
from agent import BINARY_EXT, SKIP_DIRS

# Node types that represent "call this thing", per language tree-sitter
# grammar. Name-based: resolves to the identifier being called, not its
# type -- code_index.py notes the same gap for its own outline/find_symbol.
_CALL_NODE_TYPES = {
    "python": {"call"},
    "go": {"call_expression"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
    "tsx": {"call_expression"},
}

# Tunable constants -- kept here, at the top, rather than buried in function
# bodies, so a caller can retune without reading the implementation.
MAX_INDEX_FILES = 4000          # bounds a pathological repo's index-build time
MAX_INDEX_SECONDS = 20.0
MAX_HOPS = 2                    # matches Pattern A: the fix lives one call away
MAX_REACHABLE_NODES = 200
MAX_SEEDS_PER_FILE = 20         # a file naming itself once should not seed hundreds

_FILE_RE = re.compile(r'\b([\w][\w/.-]*\.(?:py|go|js|jsx|ts|tsx))\b')


def _callee_name(call_node) -> Optional[str]:
    """
    The rightmost identifier in a call's callee expression: `f` -> "f",
    `a.b.c` -> "c", `pkg.Sub.Method` -> "Method". One walk covers Python
    attribute access, Go selector expressions and JS/TS member access,
    since tree-sitter children are ordered left-to-right matching source
    position -- the last identifier visited in a pre-order walk is the
    rightmost one in the source.
    """
    func = call_node.child_by_field_name("function")
    if func is None:
        func = call_node.children[0] if call_node.children else None
    if func is None:
        return None
    idents = []

    def collect(n):
        if n.type in ("identifier", "field_identifier", "property_identifier"):
            idents.append(n)
        for c in n.children:
            collect(c)

    collect(func)
    if not idents:
        return None
    return idents[-1].text.decode("utf-8", errors="replace")


def call_targets(source: bytes, path: str) -> Optional[list[str]]:
    """Names called anywhere in `source`. None if the language is not one
    of the four this module covers -- same None-means-unsupported contract
    as code_index.outline/syntax_errors, never an empty list standing in
    for "not checked"."""
    lang = code_index.language_for(path)
    if lang is None or lang not in _CALL_NODE_TYPES:
        return None
    tree = get_parser(lang).parse(source)
    types = _CALL_NODE_TYPES[lang]
    names: list[str] = []

    def walk(node):
        if node.type in types:
            name = _callee_name(node)
            if name:
                names.append(name)
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return names


@dataclass
class SymbolIndex:
    by_name: dict[str, list[tuple[str, "code_index.Symbol"]]] = field(default_factory=dict)
    files_indexed: int = 0
    truncated: bool = False


def build_repo_symbol_index(root: str, max_files: int = MAX_INDEX_FILES,
                            max_seconds: float = MAX_INDEX_SECONDS) -> SymbolIndex:
    """
    Reverse name -> [(file, Symbol), ...] index over an entire checkout.

    Reuses agent.py's SKIP_DIRS/BINARY_EXT so this walks the same tree
    `search`/`list_dir` do -- no separate exclusion list to drift out of
    sync. Bounded by file count and wall-clock so a huge repo cannot blow
    the per-instance time budget; `truncated` reports whether that bound
    was hit, so a caller can tell an incomplete index from a genuinely
    small repo.
    """
    idx = SymbolIndex()
    t0 = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in BINARY_EXT:
                continue
            if idx.files_indexed >= max_files or time.time() - t0 > max_seconds:
                idx.truncated = True
                return idx
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                with open(full, "rb") as f:
                    source = f.read()
            except OSError:
                continue
            syms = code_index.outline(source, rel)
            if syms is None:
                continue
            idx.files_indexed += 1
            for s in syms:
                idx.by_name.setdefault(s.name, []).append((rel, s))
    return idx


@dataclass
class Reachability:
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    # (file, qualified_name, hop) -- which hop found which hit, so a caller
    # can explain "why is this file here" rather than presenting a flat list.
    trace: list[tuple[str, str, int]] = field(default_factory=list)


def reachable_symbols(seed: list[tuple[str, str]], root: str, index: SymbolIndex,
                      max_hops: int = MAX_HOPS,
                      max_nodes: int = MAX_REACHABLE_NODES) -> Reachability:
    """
    BFS from seed (file, symbol_name) pairs, following call_targets one hop
    at a time, resolved against `index`.

    Over-approximates by construction (name-only resolution), so this is a
    hint-generation function, not a prover -- see the module docstring.
    `max_hops=2` deliberately matches Pattern A's own description: the
    missing file sits one call away from the file the agent did find, and
    two hops gives slack for one intermediate helper.
    """
    out = Reachability()
    seen_files: set[str] = set()
    seen_syms: set[tuple[str, str]] = set()
    frontier = list(seed)
    hop = 0
    while frontier and hop < max_hops and len(seen_syms) < max_nodes:
        hop += 1
        next_frontier: list[tuple[str, str]] = []
        for rel, name in frontier:
            key = (rel, name)
            if key in seen_syms:
                continue
            seen_syms.add(key)
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, "rb") as f:
                    source = f.read()
            except OSError:
                continue
            matches = code_index.find_symbol(source, rel, name)
            if not matches:
                continue
            sym = matches[0]
            called = call_targets(source[sym.start_byte:sym.end_byte], rel) or []
            for callee in called:
                for target_rel, target_sym in index.by_name.get(callee, []):
                    tkey = (target_rel, target_sym.name)
                    if tkey in seen_syms or len(seen_syms) >= max_nodes:
                        continue
                    if target_rel not in seen_files:
                        seen_files.add(target_rel)
                        out.files.append(target_rel)
                    out.symbols.append(f"{target_rel}:{target_sym.qualified_name}")
                    out.trace.append((target_rel, target_sym.qualified_name, hop))
                    next_frontier.append((target_rel, target_sym.name))
        frontier = next_frontier
    return out


def seeds_in_file(root: str, rel_path: str, limit: int = MAX_SEEDS_PER_FILE) -> list[tuple[str, str]]:
    """Every top-level symbol in one file, as (path, name) seeds -- used
    when the caller knows WHICH file matters but not which symbol in it."""
    full = os.path.join(root, rel_path)
    if not os.path.isfile(full):
        return []
    try:
        with open(full, "rb") as f:
            source = f.read()
    except OSError:
        return []
    syms = code_index.outline(source, rel_path) or []
    return [(rel_path, s.name) for s in syms[:limit]]


def seed_from_text(text: str, root: str) -> list[tuple[str, str]]:
    """Every top-level symbol in every file `text` names by path -- the
    entry point for seeding from a subgoal goal string or an issue body,
    which usually names a file without naming the specific function."""
    seeds: list[tuple[str, str]] = []
    for m in _FILE_RE.finditer(text):
        seeds.extend(seeds_in_file(root, m.group(1)))
    return seeds
