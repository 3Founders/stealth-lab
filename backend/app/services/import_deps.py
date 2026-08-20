"""
Import-derived dependency extraction (ticket 14, memory-substrate map:
"import-derived dependency edges" -- FILTER tier, deterministic
derivation, distinct from name-resolved call-graph edges which are RANK
tier per the same table). Same host-side/filesystem-only discipline as
call_graph.py/code_index.py/related_tests.py: pure parsing plus real
filesystem existence checks, no DB, no LLM, no network.

WHY THIS IS A SEPARATE MODULE FROM call_graph.py, not an addition to
it: call_graph.py tracks CALLS (function invocations); this tracks
IMPORTS (module/file dependencies). Ticket 14's own table lists them as
two distinct signals with different derivation precision (import
statements are syntactically unambiguous; a call target name is not,
per call_graph.py's own "NAME-BASED, NOT TYPE-RESOLVED" limitation) --
conflating the two modules would blur that distinction the ticket is
explicit about.

RESOLUTION HONESTY, stated once here rather than per-function: an
import STATEMENT is unambiguous (that's why it's a FILTER-tier signal,
not a RANK-tier one), but resolving it to a REAL FILE on disk is a
separate, genuinely harder problem -- full module resolution (Python's
package/namespace-package rules, Go's go.mod-driven module paths, JS/TS
node_modules + tsconfig path-mapping) is real resolver-algorithm work
this repo has no existing implementation of, and reimplementing even a
subset badly would produce confident-looking WRONG answers, worse than
an honest partial result. What's actually done:
- Python: dotted module paths (`app.services.foo`) and relative imports
  (`from . import bar`) ARE resolved against the real checkout on disk
  -- both are simple, deterministic path arithmetic (dots -> path
  separators, `.`/`..` -> parent-directory walks), no package-manager
  logic required.
- JS/TS/TSX: RELATIVE imports (`./foo`, `../bar`) are resolved the same
  way, trying the common real extensions. BARE imports (`lodash`,
  `@scope/pkg`) are NOT resolved -- that needs node_modules/tsconfig
  resolution -- and are returned as raw strings instead, honestly
  unresolved rather than guessed.
- Go: NOT resolved at all -- a Go import path like `myapp/pkg/util` only
  maps to a real directory via go.mod's declared module name, which
  this repo has no parser for. Returned as a raw string.
"""
from __future__ import annotations

import os
from typing import Optional

from app.services.code_index import language_for

_PY_SOURCE_EXTS = (".py",)
_JS_SOURCE_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_JS_INDEX_NAMES = tuple(f"index{ext}" for ext in _JS_SOURCE_EXTS)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _python_import_targets(source: bytes, path: str) -> list[str]:
    """Returns raw import specifiers -- dotted module paths
    ('app.services.foo') or relative-import markers with the trailing
    dotted part ('.', '..pkg') -- exactly as written, unresolved. See
    _resolve_python_import for turning one into a real file path."""
    from tree_sitter_language_pack import get_parser

    tree = get_parser("python").parse(source)
    targets: list[str] = []

    def walk(node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    targets.append(_node_text(child, source))
                elif child.type == "aliased_import":
                    for sub in child.children:
                        if sub.type == "dotted_name":
                            targets.append(_node_text(sub, source))
                            break
        elif node.type == "future_import_statement":
            # `from __future__ import X` is its own distinct grammar
            # node (NOT import_from_statement) -- confirmed by direct
            # tree-sitter inspection, not assumed; an earlier version of
            # this function silently missed every __future__ import for
            # exactly this reason. __future__ itself never resolves to a
            # real file (it's an interpreter pseudo-module), so it's
            # still returned as an honest, unresolved raw string, same
            # as any other unresolvable import.
            targets.append("__future__")
        elif node.type == "import_from_statement":
            # The FROM target is the first dotted_name/relative_import
            # child (before the 'import' keyword). For `from X import Y`
            # or `from .mod import Y`, that target alone is the real
            # module -- the Y names after 'import' are imported NAMES,
            # not further modules, and are ignored.
            #
            # REAL FIX, found by testing against a synthesized file, not
            # assumed correct: `from . import foo` / `from .. import bar`
            # (dots with NO trailing module name) is a different shape --
            # the from-target is JUST the dots (relative_import with only
            # an import_prefix child, no nested dotted_name), and the
            # actual importable thing is the NAME after 'import' (e.g.
            # `foo`), which combined with the dots forms the real
            # relative target ('.foo'). Detected by checking whether the
            # relative_import node has a nested dotted_name; when it
            # doesn't, the names after 'import' are combined with the
            # dot prefix instead of being ignored.
            from_target = None
            dots_only = False
            for child in node.children:
                if child.type == "dotted_name":
                    from_target = _node_text(child, source)
                    break
                elif child.type == "relative_import":
                    has_nested_module = any(c.type == "dotted_name" for c in child.children)
                    from_target = _node_text(child, source)
                    dots_only = not has_nested_module
                    break

            if from_target and not dots_only:
                targets.append(from_target)
            elif from_target and dots_only:
                seen_import_kw = False
                for child in node.children:
                    if child.type == "import":
                        seen_import_kw = True
                        continue
                    if seen_import_kw and child.type == "dotted_name":
                        targets.append(from_target + _node_text(child, source))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return targets


def _resolve_python_import(root: str, importing_rel_path: str, target: str) -> Optional[str]:
    """Real filesystem resolution for one Python import target, relative
    to the file that imported it (needed for relative imports) and the
    repo root (needed for absolute/dotted ones). Returns a real,
    existing relative path, or None if nothing on disk matches -- never
    a guessed path returned unconditionally."""
    importing_dir = os.path.dirname(importing_rel_path)

    if target.startswith("."):
        # Relative import: leading dots count levels up from the
        # importing file's own directory (one dot = same package).
        depth = len(target) - len(target.lstrip("."))
        remainder = target[depth:]
        base_dir = importing_dir
        for _ in range(depth - 1):
            base_dir = os.path.dirname(base_dir)
        module_path = remainder.replace(".", os.sep) if remainder else ""
        candidate_dir = os.path.join(base_dir, module_path) if module_path else base_dir
    else:
        candidate_dir = target.replace(".", os.sep)

    for suffix in (".py",):
        candidate = candidate_dir + suffix
        if os.path.isfile(os.path.join(root, candidate)):
            return candidate.replace(os.sep, "/")
    init_candidate = os.path.join(candidate_dir, "__init__.py")
    if os.path.isfile(os.path.join(root, init_candidate)):
        return init_candidate.replace(os.sep, "/")
    return None


def _js_import_targets(source: bytes, path: str, lang: str) -> list[str]:
    """Raw import path strings (e.g. './foo', '../bar', 'lodash') from
    ES `import` statements. CommonJS `require('...')` calls are also
    covered -- common enough in real JS/TS repos to be worth the small
    extra check, unlike chasing every dynamic-import variant."""
    from tree_sitter_language_pack import get_parser

    tree = get_parser(lang).parse(source)
    targets: list[str] = []

    def string_fragment(string_node) -> Optional[str]:
        for sub in string_node.children:
            if sub.type == "string_fragment":
                return _node_text(sub, source)
        return None

    def walk(node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    frag = string_fragment(child)
                    if frag:
                        targets.append(frag)
        elif node.type == "call_expression":
            callee = node.children[0] if node.children else None
            if callee is not None and callee.type == "identifier" and _node_text(callee, source) == "require":
                args = next((c for c in node.children if c.type == "arguments"), None)
                if args is not None:
                    for arg in args.children:
                        if arg.type == "string":
                            frag = string_fragment(arg)
                            if frag:
                                targets.append(frag)
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return targets


def _resolve_js_import(root: str, importing_rel_path: str, target: str) -> Optional[str]:
    """Real filesystem resolution for a RELATIVE JS/TS import only
    (leading './' or '../') -- a bare specifier ('lodash', '@scope/x')
    needs node_modules/tsconfig resolution this repo doesn't implement,
    so it is deliberately left unresolved (see module docstring)."""
    if not (target.startswith("./") or target.startswith("../")):
        return None
    importing_dir = os.path.dirname(importing_rel_path)
    candidate_base = os.path.normpath(os.path.join(importing_dir, target))

    for ext in _JS_SOURCE_EXTS:
        candidate = candidate_base + ext
        if os.path.isfile(os.path.join(root, candidate)):
            return candidate.replace(os.sep, "/")
    for index_name in _JS_INDEX_NAMES:
        candidate = os.path.join(candidate_base, index_name)
        if os.path.isfile(os.path.join(root, candidate)):
            return candidate.replace(os.sep, "/")
    return None


def import_targets(root: str, rel_path: str) -> list[str]:
    """
    Real import-dependency extraction for one source file. Returns
    resolved, real, existing relative paths where resolution is
    tractable (Python dotted/relative imports, JS/TS relative imports),
    and raw, unresolved import-specifier strings otherwise (Go paths,
    bare JS/TS specifiers) -- never silently dropped, since an
    unresolved-but-real import target is still a usable, if weaker,
    FILTER candidate for local_retrieval.py's substring matching.
    """
    full = os.path.join(root, rel_path)
    if not os.path.isfile(full):
        return []
    try:
        with open(full, "rb") as f:
            source = f.read()
    except OSError:
        return []

    lang = language_for(rel_path)
    results: list[str] = []

    if lang == "python":
        for raw in _python_import_targets(source, rel_path):
            resolved = _resolve_python_import(root, rel_path, raw)
            results.append(resolved if resolved else raw)
    elif lang in ("javascript", "typescript", "tsx"):
        for raw in _js_import_targets(source, rel_path, lang):
            resolved = _resolve_js_import(root, rel_path, raw)
            results.append(resolved if resolved else raw)
    elif lang == "go":
        # Raw only -- go.mod-driven resolution not implemented (see
        # module docstring). Still real, deterministic extraction of
        # WHAT is imported, even though WHERE it lives isn't resolved.
        from tree_sitter_language_pack import get_parser
        tree = get_parser("go").parse(source)

        def walk(node):
            if node.type == "import_spec":
                for child in node.children:
                    if child.type == "interpreted_string_literal":
                        results.append(_node_text(child, source).strip('"'))
            for child in node.children:
                walk(child)
        walk(tree.root_node)

    return results


def import_targets_for_many(root: str, rel_paths: list[str]) -> list[str]:
    """Convenience wrapper matching related_tests.related_test_files_for_many's
    shape -- union + dedup across several files, directly assignable to
    StructuralContext.import_deps."""
    seen: set[str] = set()
    result: list[str] = []
    for rel_path in rel_paths:
        for target in import_targets(root, rel_path):
            if target not in seen:
                seen.add(target)
                result.append(target)
    return result
