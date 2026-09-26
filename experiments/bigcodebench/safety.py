"""Which BigCodeBench tasks may run on this machine, and static screening of generated code.

A task is runnable locally only if the libraries its SOLUTION needs are pure computation
(no filesystem, processes, network) and installed, and every module its TEST code
imports is installed. Generated code is screened before execution: importing a banned
module, or calling eval/exec/open/__import__, fails the attempt without running it.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from typing import Iterable, Optional

SAFE_SOLUTION_LIBS = frozenset({
    "pandas", "numpy", "scipy", "dateutil", "math", "re", "datetime", "json", "collections", "itertools",
    "random", "string", "statistics", "functools", "operator", "heapq", "bisect", "decimal", "fractions",
    "calendar", "textwrap", "difflib", "unicodedata", "hashlib", "base64", "binascii", "hmac", "struct",
    "copy", "types", "typing", "enum", "array", "cmath", "numbers", "secrets", "zlib", "codecs", "html",
    "uuid", "pprint", "dataclasses", "string",
})
BANNED_MODULES = frozenset({
    "os", "sys", "shutil", "subprocess", "socket", "requests", "urllib", "http", "ftplib", "smtplib", "glob",
    "pathlib", "ctypes", "multiprocessing", "threading", "signal", "pickle", "shelve", "sqlite3", "tempfile",
    "importlib", "builtins", "asyncio", "webbrowser", "psutil", "platform", "io", "zipfile", "tarfile",
})
BANNED_CALLS = frozenset({"eval", "exec", "open", "__import__", "compile", "input", "breakpoint"})


def installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module.split(".")[0]) is not None
    except (ImportError, ValueError):
        return False


def test_imports(test_code: str) -> set[str]:
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        return {"<unparseable>"}
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def runnable_locally(libs: Iterable[str], test_code: str) -> tuple[bool, Optional[str]]:
    roots = {lib.split(".")[0] for lib in libs}
    unsafe = roots - SAFE_SOLUTION_LIBS
    if unsafe:
        return False, f"solution needs {sorted(unsafe)}"
    missing = sorted(m for m in roots | test_imports(test_code) if not installed(m))
    if missing:
        return False, f"not installed: {missing}"
    return True, None


def screen_code(code: str) -> Optional[str]:
    """None if the generated code may run; otherwise why it may not."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} (line {exc.lineno})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name.split(".")[0] in BANNED_MODULES]
            if bad:
                return f"imports a banned module: {bad}"
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in BANNED_MODULES:
            return f"imports a banned module: {node.module}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            return f"calls {node.func.id}()"
        elif isinstance(node, ast.Attribute) and node.attr in ("system", "popen", "remove", "rmtree", "unlink"):
            return f"uses .{node.attr}"
    if re.search(r"__\w+__\s*\(", code) and "__init__" not in code and "__name__" not in code:
        return "calls a dunder function directly"
    return None
