"""Grade one solution for one DS-1000 problem, in the pinned evaluation environment.

GOLD   = DS-1000's own grading, unchanged: `test_execution(solution)` over every test case,
         plus `test_string(solution)` when the problem defines one.
CHECK  = what an agent could run before delivering (the runtime check routing uses):
           * problems with >= 2 test cases: test case 1 only (test_execution with its loop
             limited to the first case);
           * problems with 1 test case: a smoke run -- the solution executes on the test
             input and assigns a non-None `result`; the expected answer is NOT consulted.

The program runs in a separate process of the evaluation interpreter (.evalenv: pinned
pandas/numpy/scipy/sklearn, no app code), in a fresh temp dir, with an environment
holding no keys or database URLs, and a timeout. Generated code is screened first;
refused code is never run.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Optional

MARKER = "@@KEL_DS1000@@"
_SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS")
EVAL_PYTHON = Path(__file__).resolve().parent / ".evalenv" / "Scripts" / "python.exe"

BANNED_MODULES = frozenset({
    "os", "sys", "shutil", "subprocess", "socket", "requests", "urllib", "http", "ftplib", "smtplib", "glob",
    "pathlib", "ctypes", "multiprocessing", "threading", "signal", "pickle", "shelve", "sqlite3", "tempfile",
    "importlib", "builtins", "asyncio", "webbrowser", "psutil", "platform", "zipfile", "tarfile",
})
BANNED_CALLS = frozenset({"eval", "exec", "open", "__import__", "compile", "input", "breakpoint"})
_LOOP = re.compile(r"for i in range\((\d+)\):")


def fit_indentation(solution: str, code_context: str) -> str:
    """Place the solution at the indentation of DS-1000's `[insert]` point: problems that
    complete a function body insert indented code. Dedent, then indent to match -- the same
    normalisation for every model and every arm (idempotent for already-correct code)."""
    lines = code_context.splitlines()
    at = next((i for i, ln in enumerate(lines) if "[insert]" in ln), None)
    if at is None:
        return solution
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    before = next((ln for ln in reversed(lines[:at]) if ln.strip()), "")
    if before.rstrip().endswith(":"):                 # [insert] is the body of a block (e.g. `def f(df):`)
        indent = before[: len(before) - len(before.lstrip())] + "    "
    body = solution.strip("\n").splitlines()
    code_lines = [ln for ln in body if ln.strip() and not ln.lstrip().startswith("#")]
    common = min((len(ln) - len(ln.lstrip()) for ln in code_lines), default=0)
    out = []
    for ln in body:
        lead = len(ln) - len(ln.lstrip())
        out.append((indent + ln[min(lead, common):]) if ln.strip() else "")
    return "\n".join(out)


def screen_code(code: str) -> Optional[str]:
    try:
        tree = ast.parse(fit_indentation(code, "[insert]"))
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
    return None


def check_kind(code_context: str, test_case_cnt: int) -> str:
    """'case1' when test case 1 alone can be run as the check, else 'smoke'."""
    head, sep, tail = code_context.partition("def test_execution(")
    if test_case_cnt >= 2 and sep and len(_LOOP.findall(tail.split("\ndef ")[0])) == 1:
        return "case1"
    return "smoke"


def extract_code(text: str) -> str:
    """The solution code from a chat reply: the last ```python block (or the whole text),
    cut at DS-1000's end markers."""
    blocks, rest = [], text or ""
    while "```" in rest:
        _, _, rest = rest.partition("```")
        body, sep, rest = rest.partition("```")
        if not sep:
            break
        first, _, remainder = body.partition("\n")
        blocks.append(remainder if first.strip().lower() in ("python", "py", "python3", "") else body)
    code = blocks[-1] if blocks else (text or "")
    for end in ("</code>", "\nEND SOLUTION", "# SOLUTION END"):
        code = code.split(end)[0]
    return code.replace("<code>", "").replace("BEGIN SOLUTION", "").strip("\n")


def _program(code_context: str, solution: str, kind: str) -> str:
    head, sep, tail = code_context.partition("def test_execution(")
    fn_body, _, after = tail.partition("\ndef ")
    case1 = "def _kel_case1(" + _LOOP.sub("for i in range(1):", fn_body, count=1) if kind == "case1" else ""
    return f"""{code_context}

{case1}

import json as _kel_json
_kel_solution = {solution!r}
_kel_res = {{}}

def _kel_run(name, fn):
    try:
        fn()
        _kel_res[name] = "pass"
    except BaseException as exc:
        _kel_res[name] = "fail: " + repr(exc)[-300:]

def _kel_smoke():
    test_input, _ = generate_test_case(1)
    env = {{"test_input": test_input}}
    exec(exec_context.replace("[insert]", _kel_solution), env)
    assert env.get("result") is not None

_kel_run("check", {"lambda: _kel_case1(_kel_solution)" if kind == "case1" else "_kel_smoke"})
_kel_run("gold_exec", lambda: test_execution(_kel_solution))
if "test_string" in globals():
    _kel_run("gold_string", lambda: test_string(_kel_solution))
print({MARKER!r} + _kel_json.dumps(_kel_res))
"""


def grade(solution: str, code_context: str, test_case_cnt: int, *, timeout_s: float = 60.0,
          screen: bool = True) -> dict[str, Any]:
    started = time.time()
    kind = check_kind(code_context, test_case_cnt)
    solution = fit_indentation(solution, code_context)
    refused = screen_code(solution) if screen else None
    if refused:
        return {"check_pass": False, "gold_pass": False, "check_kind": kind, "error": f"refused: {refused}",
                "screened": True, "detail": {}, "seconds": 0.0}
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.update({"MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0"})
    with tempfile.TemporaryDirectory(prefix="kel_ds1000_") as tmp:
        program = Path(tmp) / "program.py"
        program.write_text(_program(code_context, solution, kind), encoding="utf-8")
        try:
            proc = subprocess.run([str(EVAL_PYTHON), str(program)], cwd=tmp, env=env, capture_output=True,
                                  text=True, timeout=timeout_s, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"check_pass": False, "gold_pass": False, "check_kind": kind,
                    "error": f"timeout after {timeout_s}s", "screened": False, "detail": {},
                    "seconds": round(time.time() - started, 2)}
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(MARKER)), None)
    if line is None:
        return {"check_pass": False, "gold_pass": False, "check_kind": kind,
                "error": (proc.stderr or proc.stdout or "no output")[-600:], "screened": False, "detail": {},
                "seconds": round(time.time() - started, 2)}
    res = json.loads(line[len(MARKER):])
    gold = res.get("gold_exec") == "pass" and res.get("gold_string", "pass") == "pass"
    return {"check_pass": res.get("check") == "pass", "gold_pass": gold, "check_kind": kind, "error": None,
            "screened": False, "detail": res, "seconds": round(time.time() - started, 2)}
