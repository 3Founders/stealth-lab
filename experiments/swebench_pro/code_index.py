"""
Symbol-level file access via tree-sitter, so the agent can read ONE function
instead of a whole file.

DELIBERATELY NOT THE FULL LSP-DAEMON VERSION. The original proposal was a
persistent pyright/gopls/typescript-language-server daemon per language,
queried over IPC for symbol relationships and call hierarchies. That is the
semantically correct version -- an LSP server has already solved cross-file
reference resolution, which is exactly the part I earlier said was too
expensive to hand-roll (per-language resolvers for Go interfaces, Python
imports, TS module resolution). It was not built today for a scheduling
reason, not a technical one: every SWE-bench Pro instance is a FRESH
checkout, so a persistent daemon means a COLD INDEX every instance --
commonly 30s-2min for gopls/pyright on a real repo -- which conflicts
directly with "each run under 5 minutes." Tree-sitter has no such cost: it
is a pure parser, no project-wide indexing, no daemon, milliseconds per
file, and it runs on the HOST (RepoSandbox never touches the SWE-bench
Docker image -- see run_experiment.snapshot_repo), so there is no
per-instance container cost either.

What this buys, and what it does NOT: outline + exact symbol extraction
across all four corpus languages, syntactically. It does NOT resolve
cross-file callers/callees -- "who calls this function" still needs `search`
by name, which over- and under-returns (comments, tests, shadowed names;
misses indirect calls). That gap is exactly what the LSP version would
close, and is the natural next step if this proves the token-saving thesis.

WHY EXTRACTION IS BYTE-EXACT, NOT LLM-SUMMARIZED: a symbol's source is a
literal slice of the file between two byte offsets a parser computed. It can
be wrong about WHICH bytes (a language gap, a parse error) but can never
hallucinate or silently drop a line the way a model asked to "summarize this
function" could. `test_code_index.py`'s round-trip tests check exactly this
property: every extracted body must appear verbatim in the source file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from tree_sitter_language_pack import get_parser

LANG_BY_EXT = {
    ".py": "python",
    ".go": "go",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}

# Node types that count as a "symbol" worth its own entry, per language.
# Deliberately excludes plain variable/const declarations (too numerous, and
# rarely what a subgoal like "fix the Add function" is pointing at) except
# JS/TS arrow-function assignments, which are how that shape names a function.
SYMBOL_TYPES = {
    "python": {"function_definition", "class_definition"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "javascript": {"function_declaration", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition",
                   "interface_declaration", "type_alias_declaration"},
    "tsx": {"function_declaration", "class_declaration", "method_definition",
            "interface_declaration", "type_alias_declaration"},
}

# The child node type that carries a symbol's own name, in priority order --
# Go methods use field_identifier, everything else identifier/type_identifier/
# property_identifier depending on node kind.
_NAME_TYPES = ("identifier", "field_identifier", "type_identifier", "property_identifier")

_KIND_LABEL = {
    "function_definition": "def", "function_declaration": "func",
    "class_definition": "class", "class_declaration": "class",
    "method_declaration": "method", "method_definition": "method",
    "type_declaration": "type", "interface_declaration": "interface",
    "type_alias_declaration": "type",
}


def language_for(path: str) -> str | None:
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower())


@dataclass
class Symbol:
    kind: str
    name: str
    qualified_name: str   # "ClassName.method" when nested, else same as name
    start_line: int        # 1-indexed, inclusive
    end_line: int           # 1-indexed, inclusive
    start_byte: int
    end_byte: int


def _name_of(node) -> str | None:
    """
    Direct children first, then grandchildren -- most symbol shapes name
    themselves one level down (`def add(...)`: `identifier` is a direct
    child), but Go's `type_declaration` wraps its name inside a `type_spec`
    (`type Server struct {...}` -> type_declaration > type_spec >
    type_identifier), one level deeper. Two shallow passes cover both
    without a per-language special case, and stop at grandchildren so this
    can never reach into a function body and pick up an unrelated identifier.
    """
    for child in node.children:
        if child.type in _NAME_TYPES:
            return child.text.decode("utf-8", errors="replace")
    for child in node.children:
        for grandchild in child.children:
            if grandchild.type in _NAME_TYPES:
                return grandchild.text.decode("utf-8", errors="replace")
    return None


def _walk(node, lang: str, out: list[Symbol], class_name: str | None = None) -> None:
    types = SYMBOL_TYPES[lang]
    for child in node.children:
        if child.type in types:
            name = _name_of(child)
            if name:
                qualified = f"{class_name}.{name}" if class_name else name
                out.append(Symbol(
                    kind=_KIND_LABEL.get(child.type, child.type), name=name,
                    qualified_name=qualified,
                    start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                    start_byte=child.start_byte, end_byte=child.end_byte,
                ))
            # Recurse into classes to find methods (qualified as Class.method);
            # do not recurse into functions -- nested/local functions are an
            # implementation detail, not something a subgoal names directly.
            if child.type in ("class_definition", "class_declaration") and name:
                _walk(child, lang, out, class_name=name)
                continue
        _walk(child, lang, out, class_name)


def outline(source: bytes, path: str) -> list[Symbol] | None:
    """All top-level (and class-nested) symbols in a file. None if the
    extension is not one of the four corpus languages -- the caller must
    fall back to read_file, never silently return an empty outline for a
    language this just does not cover."""
    lang = language_for(path)
    if lang is None:
        return None
    tree = get_parser(lang).parse(source)
    out: list[Symbol] = []
    _walk(tree.root_node, lang, out)
    return out


def find_symbol(source: bytes, path: str, name: str) -> list[Symbol] | None:
    """Symbols whose name OR qualified_name matches, case-sensitive exact
    match only -- a fuzzy match here would risk silently returning the wrong
    function's body, which is worse than a clear 'not found'."""
    syms = outline(source, path)
    if syms is None:
        return None
    return [s for s in syms if name in (s.name, s.qualified_name)]


def syntax_errors(source: bytes, path: str) -> tuple[int, int] | None:
    """
    (error_count, first_error_line) for a file, or None if the language is
    not one of the four this module covers -- None must NOT be treated as
    "0 errors": an unsupported language reporting a false green light would
    let broken code through exactly the file types this cannot check.

    WHY THIS EXISTS: a patch that does not even PARSE is a guaranteed
    f2p_failed -- the test harness cannot import/build the module at all.
    Every prior run in this project discovered that only from the external
    grading container, after the episode had already ended and `finish` or
    `subgoal_done` had already been called on the broken result. Tree-sitter
    error-recovers around malformed regions and marks them with ERROR (or
    MISSING, for an expected-but-absent token) nodes rather than failing the
    whole parse, so a single cheap host-side re-parse after every edit can
    catch this INSIDE the episode, while there are still tool calls left to
    fix it -- milliseconds, no Docker, no LLM call.

    This does not prove the code is semantically correct, only that it is
    not GRAMMATICALLY broken. It is deliberately a narrow, cheap check, not
    a substitute for running the real tests (which needs the target's Docker
    image and, per pro_harness timings measured this session, 30s-600s+ per
    run -- incompatible with a per-tool-call check inside the step budget).
    """
    lang = language_for(path)
    if lang is None:
        return None
    tree = get_parser(lang).parse(source)

    def _count(node) -> tuple[int, int]:
        n, first = 0, -1
        if node.type == "ERROR" or node.is_missing:
            n, first = 1, node.start_point[0] + 1
        for child in node.children:
            cn, cf = _count(child)
            n += cn
            if first < 0:
                first = cf
        return n, first

    count, first_line = _count(tree.root_node)
    return count, first_line
