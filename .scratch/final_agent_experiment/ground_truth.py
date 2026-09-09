"""
Ground-truth scanners for T1/T3/T7, computed ONCE against the frozen
commit's own backend/app/ tree (not per-trial -- the pinned commit never
changes, so ground truth is invariant across all 12 trials). Pure
AST/regex scans, no model calls, fully deterministic and reproducible.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


def t1_ground_truth(repo_root: Path) -> dict:
    server_py = repo_root / "backend" / "app" / "mcp_server" / "server.py"
    text = server_py.read_text(encoding="utf-8")
    lines = text.split("\n")
    names = []
    for i, line in enumerate(lines):
        if line.strip() == "@server.tool()":
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.match(r"\s*(async def|def)\s+(\w+)", lines[j])
                if m:
                    names.append(m.group(2))
                    break
    return {
        "tool_function_names": sorted(names),
        "tool_function_count": len(names),
        "shared_resolver": "_canonical_procedure_id",
    }


def _find_call_sites(repo_root: Path, func_name: str, exclude_file: Path | None = None) -> dict:
    """Every file under backend/app/ that references func_name as a NAME
    (call or import), definition file excluded from the 'external' count
    but still reported."""
    root = repo_root / "backend" / "app"
    definer = None
    external_callers = set()
    pattern = re.compile(r"\b" + re.escape(func_name) + r"\b")
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if not pattern.search(text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        defines_here = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name
            for node in ast.walk(tree)
        )
        if defines_here:
            definer = py
        else:
            external_callers.add(py)
    return {
        "function": func_name,
        "defined_in": str(definer.relative_to(repo_root)) if definer else None,
        "external_caller_files": sorted(str(p.relative_to(repo_root)) for p in external_callers),
        "external_caller_count": len(external_callers),
    }


def t3_candidate_functions(repo_root: Path) -> dict:
    """Every function defined in capability.py that's called from >=1
    OTHER module -- the set of valid choices the task's 'find a function
    used by at least one other module' could legitimately mean."""
    cap_py = repo_root / "backend" / "app" / "services" / "procedure_extraction" / "capability.py"
    text = cap_py.read_text(encoding="utf-8")
    tree = ast.parse(text)
    top_level_funcs = [
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    out = {}
    for fn in top_level_funcs:
        info = _find_real_call_sites(repo_root, fn)
        if info["external_caller_count"] >= 1:
            out[fn] = info
    return out


def _find_real_call_sites(repo_root: Path, func_name: str) -> dict:
    """T3-v2 (final pre-score remediation, bounded T1/T3 pass): AST-aware
    replacement for _find_call_sites' text-substring scan. Confirmed real
    defect in the original: both real T3 candidates (wilson_interval,
    band_for_p) have their names embedded in PROSE COMMENTS/DOCSTRINGS
    across multiple files (e.g. backend/app/api/implementations.py:170,
    backend/app/services/capabilities.py:43-44/268,
    backend/app/services/procedure_graph_api.py:29-30/357,
    backend/app/services/product_model.py:16) -- confirmed via direct
    grep. A regex substring match cannot tell a real call/import from a
    docstring merely naming the function for documentation purposes, so
    the OLD scanner's "does this file mention func_name at all" question
    (used both to build the candidate list AND, in verify_T3, to detect
    "does the OLD name have zero remaining references anywhere") can
    NEVER return true after even a functionally perfect rename -- the
    docstring/comment mentions persist regardless, making task_success
    structurally unreachable. Comments are never part of the AST at all
    (Python's parser drops them), and a docstring is an ast.Constant
    (str), not an ast.Name/ast.Attribute/import alias -- so switching to
    a pure AST walk for genuine identifier references excludes both,
    for free, without needing separate string/comment-detection logic."""
    root = repo_root / "backend" / "app"
    definer = None
    external_callers = set()
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if func_name not in text:  # cheap pre-filter before the real AST parse
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        defines_here = False
        references_here = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                defines_here = True
            elif isinstance(node, ast.Name) and node.id == func_name:
                references_here = True
            elif isinstance(node, ast.Attribute) and node.attr == func_name:
                references_here = True
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == func_name or alias.asname == func_name:
                        references_here = True
        if defines_here:
            definer = py
        elif references_here:
            external_callers.add(py)
    return {
        "function": func_name,
        "defined_in": str(definer.relative_to(repo_root)) if definer else None,
        "external_caller_files": sorted(str(p.relative_to(repo_root)) for p in external_callers),
        "external_caller_count": len(external_callers),
    }


def t3_ground_truth_for(repo_root: Path, func_name: str) -> dict:
    return _find_real_call_sites(repo_root, func_name)


def t7_ground_truth(repo_root: Path) -> dict:
    """Every function whose name starts with '_' defined anywhere under
    backend/app/services/, called from >=2 DIFFERENT files outside its
    own defining file (search scope for callers = all of backend/app/,
    matching the task's own wording -- it does not restrict caller
    location to services/ only)."""
    services_root = repo_root / "backend" / "app" / "services"
    app_root = repo_root / "backend" / "app"

    definitions: dict[str, Path] = {}
    for py in services_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_") \
               and not node.name.startswith("__"):
                # only top-level or class-level defs, first definer wins if duplicate name across files
                definitions.setdefault(node.name, py)

    result = {}
    for fn_name, def_file in definitions.items():
        pattern = re.compile(r"\b" + re.escape(fn_name) + r"\b")
        callers = set()
        for py in app_root.rglob("*.py"):
            if py == def_file:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
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
    print("=== T1 ===")
    print(json.dumps(t1_ground_truth(root), indent=2))
    print("=== T3 candidates ===")
    print(json.dumps(t3_candidate_functions(root), indent=2))
    print("=== T7 (this can take a few seconds) ===")
    print(json.dumps(t7_ground_truth(root), indent=2))
