"""
T7-v2 replacement task ground truth: the single largest function (by
physical body line count) among top-level-defined functions/methods in
every *.py file directly under backend/app/services/ (not subdirectories).

Deterministic, unambiguous: pure AST line-span measurement, no fuzzy
cross-file call-graph inference (the exact class of ambiguity that made
the original T7 grader unreliable -- see t7-review.md). "Largest" = highest
(end_lineno - lineno + 1), computed once per function/method
(including inside classes, since 'function definition' plausibly includes
methods and the task statement should say so explicitly), ties broken by
(file, then function name) alphabetically for a single deterministic answer.
"""
from __future__ import annotations

import ast
from pathlib import Path


def t7v2_largest_function(repo_root: Path) -> dict:
    services_dir = repo_root / "backend" / "app" / "services"
    best = None  # (lines, file_rel, name)
    all_sizes = []
    for py in sorted(services_dir.glob("*.py")):  # top-level only, not rglob
        text = py.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", None)
                if end is None:
                    continue
                n_lines = end - node.lineno + 1
                rel = str(py.relative_to(repo_root))
                all_sizes.append((n_lines, rel, node.name))
                key = (n_lines, rel, node.name)
                if best is None or (key[0] > best[0]) or (key[0] == best[0] and (key[1], key[2]) < (best[1], best[2])):
                    best = key
    all_sizes.sort(reverse=True)
    return {
        "answer": {"function_name": best[2], "file": best[1], "line_count": best[0]} if best else None,
        "top_10_for_margin_check": all_sizes[:10],
        "files_scanned": len(list(services_dir.glob("*.py"))),
    }


if __name__ == "__main__":
    import json
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    print(json.dumps(t7v2_largest_function(root), indent=2))
