"""Import graph across a repository's files, for the centrality term of the file score. Pure, in-memory.

"How many other files import this one" separates a module the repository is built on from a leaf that merely exists. Only
edges that RESOLVE to a file in the repository count; an import of a package from the registry is not evidence about any
file here.

WHAT RESOLVES
    Python   absolute (`pkg.sub.mod`, also under a `src/` root) and relative (`from . import x`) imports
    JS/TS    relative specifiers (`./x`, `../y/z`) including `export ... from` re-exports, `require()` and `import()`, trying
             the source extensions and `index.*`; bare specifiers (`lodash`, `@scope/pkg`) are registry imports and are skipped
    Go       a package path under the repository's own `go.mod` module name maps to a directory; every non-test `.go` file in
             that directory receives the edge
    other    Rust and Java files are scored without a centrality term (0), and the funnel says so.
"""
from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Iterable, Mapping, Optional

from app.services.code_cascade import python_features, scoring

JS_LANGS = {"javascript", "typescript", "tsx"}
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _string_content(node) -> Optional[str]:
    text = node.text.decode("utf-8", errors="replace")
    return text[1:-1] if len(text) >= 2 and text[0] in "'\"`" else None


def js_specifiers(text: str, language: str) -> list[str]:
    from tree_sitter_language_pack import get_parser

    tree = get_parser(language).parse(text.encode("utf-8"))
    out: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            if source is not None and (value := _string_content(source)):
                out.append(value)
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type in {"identifier", "import"} and fn.text.decode() in {"require", "import"}:
                args = node.child_by_field_name("arguments")
                first = args.named_children[0] if args is not None and args.named_children else None
                if first is not None and first.type == "string" and (value := _string_content(first)):
                    out.append(value)
        stack.extend(node.children)
    return out


def go_specifiers(text: str) -> list[str]:
    from tree_sitter_language_pack import get_parser

    tree = get_parser("go").parse(text.encode("utf-8"))
    out: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "import_spec":
            path = node.child_by_field_name("path")
            if path is not None and (value := _string_content(path)):
                out.append(value)
        elif node.type in {"function_declaration", "method_declaration"}:
            continue
        stack.extend(node.children)
    return out


def go_module_name(go_mod_text: str) -> Optional[str]:
    for line in go_mod_text.splitlines():
        line = line.strip()
        if line.startswith("module "):
            return line.split(None, 1)[1].strip().strip('"')
    return None


def _resolve_js(importer: str, spec: str, paths: set[str]) -> Optional[str]:
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    stem, ext = posixpath.splitext(base)
    candidates = [base] if ext in _JS_EXTS else []
    candidates += [base + e for e in _JS_EXTS] + [posixpath.join(base, "index" + e) for e in _JS_EXTS]
    if ext in {".js", ".jsx", ".mjs", ".cjs"}:       # TS sources are imported with their compiled extension
        candidates += [stem + e for e in (".ts", ".tsx")]
    return next((c for c in candidates if c in paths), None)


def build_indegree(files: Mapping[str, tuple[str, str]], *, go_mod_text: Optional[str] = None) -> dict[str, int]:
    """`files`: path -> (text, language). Returns path -> number of distinct OTHER files importing it."""
    paths = set(files)
    incoming: dict[str, set[str]] = defaultdict(set)

    python = {p: python_features.module_imports(text) for p, (text, lang) in files.items() if lang == "python"}
    py_by_module = {scoring.module_name(p): p for p in python}
    py_by_suffix: dict[str, list[str]] = defaultdict(list)
    for module, path in py_by_module.items():
        parts = module.split(".")
        for i in range(1, len(parts)):
            py_by_suffix[".".join(parts[i:])].append(path)
    for importer, names in python.items():
        for name in names:
            target = scoring._resolve(importer, name, py_by_module, py_by_suffix)  # noqa: SLF001 -- same package, one resolver
            if target and target != importer:
                incoming[target].add(importer)

    go_module = go_module_name(go_mod_text) if go_mod_text else None
    go_by_dir: dict[str, list[str]] = defaultdict(list)
    for path, (_t, lang) in files.items():
        if lang == "go" and not path.endswith("_test.go"):
            go_by_dir[posixpath.dirname(path)].append(path)

    for importer, (text, lang) in files.items():
        if lang in JS_LANGS:
            for spec in js_specifiers(text, lang):
                target = _resolve_js(importer, spec, paths)
                if target and target != importer:
                    incoming[target].add(importer)
        elif lang == "go" and go_module:
            for spec in go_specifiers(text):
                if spec == go_module or spec.startswith(go_module + "/"):
                    rel = spec[len(go_module):].lstrip("/")
                    for target in go_by_dir.get(rel, []):
                        if target != importer:
                            incoming[target].add(importer)
    return {path: len(importers) for path, importers in incoming.items()}
