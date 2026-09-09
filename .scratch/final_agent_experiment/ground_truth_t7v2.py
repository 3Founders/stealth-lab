"""
T7-v2 corrected ground-truth scanner (final pre-score remediation, section B).

Root-cause fix for the original T7 grader's real bug: it matched a defined
function's NAME as a bare regex substring anywhere in another file's text,
which cannot distinguish "this file genuinely imports and calls that
specific function" from "this file happens to define/use an unrelated,
independently-defined function with the identical name" (confirmed real
false positive this pass: `_now_iso` is independently defined in FOUR
separate files -- local_claims.py, local_store.py, tasks_extension.py,
ingestion_scheduler.py -- each calling only its own local copy; the old
scanner falsely credited three of them as "callers" of the fourth).

Fix: a caller file counts only if it contains a REAL import binding the
name to the defining module -- `from <dotted.path.to.module> import ...,
fn_name, ...` (module resolved via its real file path) OR a qualified
call `<module_or_alias>.fn_name(` where that alias/module is itself
demonstrably imported from the defining module in the same file. This is
still static/textual (no real import-graph execution), but it requires an
actual import statement to exist, not just a name collision.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _module_dotted_path(repo_root: Path, py_file: Path) -> str:
    rel = py_file.relative_to(repo_root / "backend").with_suffix("")
    return ".".join(rel.parts)


def t7v2_ground_truth(repo_root: Path, scope_files: list[str]) -> dict:
    """scope_files: repo-relative paths (e.g. 'backend/app/services/applicability.py')
    defining the bounded T7-v2 search scope. Callers are searched across all
    of backend/app/ (matching the original task's own wording), but a hit
    only counts with a real import statement, verified via ast."""
    app_root = repo_root / "backend" / "app"
    scope_paths = [repo_root / f for f in scope_files]
    scope_modules = {p: _module_dotted_path(repo_root, p) for p in scope_paths}

    definitions: dict[str, Path] = {}
    for py in scope_paths:
        text = py.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_") \
               and not node.name.startswith("__"):
                definitions.setdefault(node.name, py)

    result = {}
    for fn_name, def_file in definitions.items():
        def_module = scope_modules[def_file]
        callers = set()
        for py in app_root.rglob("*.py"):
            if py in scope_paths:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            if fn_name not in text:
                continue  # cheap pre-filter
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            imported_names: set[str] = set()  # names bound directly to fn_name via `from X import fn_name [as alias]`
            imported_modules: dict[str, str] = {}  # local alias -> dotted module path, for `import X as alias` / `import X`
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == def_module:
                    for alias in node.names:
                        if alias.name == fn_name:
                            imported_names.add(alias.asname or alias.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == def_module:
                            imported_modules[alias.asname or alias.name.split(".")[-1]] = alias.name
            if not imported_names and not imported_modules:
                continue
            # Direct-name call: `fn_name(` where fn_name was imported directly.
            hit = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in imported_names:
                    hit = True
                    break
                if isinstance(node, ast.Attribute) and node.attr == fn_name \
                   and isinstance(node.value, ast.Name) and node.value.id in imported_modules:
                    hit = True
                    break
            if hit:
                callers.add(py)
        if len(callers) >= 2:
            result[fn_name] = {
                "defining_file": str(def_file.relative_to(repo_root)),
                "external_calling_files": sorted(str(p.relative_to(repo_root)) for p in callers),
            }
    return result


if __name__ == "__main__":
    import json
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    scope = [
        "backend/app/services/agent_search.py",
        "backend/app/services/applicability.py",
        "backend/app/services/reuse_detection.py",
    ]
    print(json.dumps(t7v2_ground_truth(root, scope), indent=2))
