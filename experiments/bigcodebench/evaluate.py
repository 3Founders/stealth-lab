"""Local evaluation of one generated solution against one task's tests.

Like BigCodeBench's own harness, the solution and the task's unittest suite run as one
program. Here that program runs in a SEPARATE Python process with:
  * a fresh temporary working directory (deleted afterwards),
  * an environment holding no API keys or database URLs (only what Python needs),
  * a timeout, and a non-interactive matplotlib backend.
Generated code is screened first (safety.screen_code); refused code is never run.

Per-test results give both grades from one run: the VISIBLE check (the tests an agent
may run between attempts) and the GOLD grade (every test).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from safety import screen_code

MARKER = "@@KEL_RESULT@@"
_RUNNER = f'''

import json as _kel_json, unittest as _kel_unittest
_kel_results = {{}}
for _kel_test in _kel_unittest.defaultTestLoader.loadTestsFromTestCase(TestCases):
    _kel_r = _kel_unittest.TestResult()
    try:
        _kel_test.run(_kel_r)
        _kel_status = "pass" if _kel_r.wasSuccessful() else ("fail" if _kel_r.failures else "error")
        _kel_detail = (_kel_r.failures or _kel_r.errors or [[None, ""]])[0][1][-600:]
    except BaseException as _kel_exc:
        _kel_status, _kel_detail = "error", repr(_kel_exc)[-600:]
    _kel_results[_kel_test._testMethodName] = [_kel_status, _kel_detail]
print("{MARKER}" + _kel_json.dumps(_kel_results))
'''
_SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS")


def extract_code(text: str) -> str:
    """The python code block of a model reply (the one defining the entry point if several)."""
    blocks, rest = [], text or ""
    while "```" in rest:
        _, _, rest = rest.partition("```")
        body, sep, rest = rest.partition("```")
        if not sep:
            break
        first, _, remainder = body.partition("\n")
        blocks.append(remainder if first.strip().lower() in ("python", "py", "python3", "") else body)
    if not blocks:
        return (text or "").strip()
    with_func = [b for b in blocks if "def task_func" in b]
    return (with_func or blocks)[-1].strip()


def run_tests(code: str, test_code: str, visible: Sequence[str], *, timeout_s: float = 60.0) -> dict[str, Any]:
    started = time.time()
    refused = screen_code(code)
    if refused:
        return {"visible_pass": False, "gold_pass": False, "tests": {}, "error": f"refused: {refused}",
                "screened": True, "seconds": 0.0}
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.update({"MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0"})
    with tempfile.TemporaryDirectory(prefix="kel_bcb_") as tmp:
        program = Path(tmp) / "program.py"
        program.write_text(code + "\n\n" + test_code + _RUNNER, encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, str(program)], cwd=tmp, env=env, capture_output=True,
                                  text=True, timeout=timeout_s, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"visible_pass": False, "gold_pass": False, "tests": {}, "error": f"timeout after {timeout_s}s",
                    "screened": False, "seconds": round(time.time() - started, 2)}
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(MARKER)), None)
    if line is None:
        return {"visible_pass": False, "gold_pass": False, "tests": {},
                "error": (proc.stderr or proc.stdout or "no output")[-800:], "screened": False,
                "seconds": round(time.time() - started, 2)}
    results = json.loads(line[len(MARKER):])
    passed = {name for name, (status, _) in results.items() if status == "pass"}
    return {
        "visible_pass": bool(visible) and set(visible) <= passed,
        "gold_pass": bool(results) and passed == set(results),
        "tests": {name: status for name, (status, _) in results.items()},
        "failures": {name: detail for name, (status, detail) in results.items() if status != "pass"},
        "error": None, "screened": False, "seconds": round(time.time() - started, 2),
    }
