"""MBPP+ (EvalPlus) as A-vs-C tasks: short practical Python functions.

The agent sees TASK.md (the problem with its one example), solution.py (the function's signature) and test_check.py,
which runs the problem's 3 ORIGINAL assertions -- the visible tests. The grade compares the solution's output with
the reference solution's on every base and "plus" input (EvalPlus's ~35x larger set, built to catch subtly wrong
solutions), with the problem's float tolerance; a problem whose example compares sets compares sets.

Skipped, so the grader never fails a correct answer for a format reason: problems whose inputs must be tuples
(EvalPlus stores inputs as JSON lists; their contracts assert tuples), and problems whose reference does not pass
its own assertions here. Model code runs in a subprocess, in a temp dir, with a timeout and no secrets in its env.
"""
from __future__ import annotations

import gzip
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "mbppplus" / "data" / "MbppPlus.jsonl.gz"
CHECK = "python -m pytest -q test_check.py"
TIMEOUT_S = 120
_SAFE_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")
HOW = ("Implement the function in solution.py (keep its name and signature; the task is in TASK.md). Check it with "
       "`python -m pytest -q test_check.py`.")


@lru_cache(maxsize=1)
def rows() -> dict[str, dict]:
    return {r["task_id"]: r for r in (json.loads(x) for x in gzip.open(DATA, "rt", encoding="utf-8"))}


def _signature(r: dict) -> str:
    m = re.search(rf"^def {re.escape(r['entry_point'])}\(.*?\):", r["canonical_solution"], re.M | re.S)
    return m.group(0) if m else f"def {r['entry_point']}(*args):"


def _usable(r: dict) -> bool:
    return bool(r.get("plus_input")) and "tuple" not in (r.get("contract") or "") and bool(r.get("assertion"))


_HARNESS = r'''
import copy, json, math, sys
ns_ref, ns_sol = {}, {}
exec(open(sys.argv[1], encoding="utf-8").read(), ns_ref)
exec(open(sys.argv[2], encoding="utf-8").read(), ns_sol)
spec = json.load(open(sys.argv[3], encoding="utf-8"))
ref, sol = ns_ref[spec["entry"]], ns_sol[spec["entry"]]
def same(a, b):
    if spec["sets"]:
        try: return set(a) == set(b)
        except TypeError: pass
    if isinstance(a, float) or isinstance(b, float):
        try: return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=max(spec["atol"], 1e-9))
        except (TypeError, ValueError): return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b) and type(a) == type(b):
        return all(same(x, y) for x, y in zip(a, b))
    return a == b
for i, inp in enumerate(spec["inputs"]):
    try: want = ref(*copy.deepcopy(inp))
    except Exception: continue                     # an input the reference itself rejects is not a test
    try: got = sol(*copy.deepcopy(inp))
    except Exception as e:
        print("@@MBPP@@" + json.dumps({"ok": False, "at": i, "why": f"raised {type(e).__name__}: {str(e)[:120]}"})); sys.exit(0)
    if not same(want, got):
        print("@@MBPP@@" + json.dumps({"ok": False, "at": i, "why": f"input {str(inp)[:120]}: expected {str(want)[:80]}, got {str(got)[:80]}"})); sys.exit(0)
print("@@MBPP@@" + json.dumps({"ok": True}))
'''


def _run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    env = {k: os.environ[k] for k in _SAFE_ENV if k in os.environ}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"})
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT_S, encoding="utf-8", errors="replace")


def visible(r: dict, solution: str) -> tuple[bool, str]:
    """The 3 original assertions."""
    with tempfile.TemporaryDirectory(prefix="mbpp_v_") as tmp:
        Path(tmp, "p.py").write_text(solution + "\n" + r["assertion"], encoding="utf-8")
        try:
            p = _run(["p.py"], tmp)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return p.returncode == 0, (p.stderr or p.stdout)[-1200:]


def hidden(r: dict, solution: str) -> tuple[bool, str]:
    """Every base and plus input, against the reference's output."""
    with tempfile.TemporaryDirectory(prefix="mbpp_h_") as tmp:
        Path(tmp, "ref.py").write_text(r["canonical_solution"], encoding="utf-8")
        Path(tmp, "sol.py").write_text(solution, encoding="utf-8")
        Path(tmp, "h.py").write_text(_HARNESS, encoding="utf-8")
        Path(tmp, "spec.json").write_text(json.dumps({
            "entry": r["entry_point"], "atol": float(r.get("atol") or 0),
            "sets": "set(" in r["prompt"], "inputs": list(r["base_input"]) + list(r["plus_input"])}), encoding="utf-8")
        try:
            p = _run(["h.py", "ref.py", "sol.py", "spec.json"], tmp)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        line = next((x for x in reversed(p.stdout.splitlines()) if x.startswith("@@MBPP@@")), None)
        if line is None:
            return False, (p.stderr or p.stdout)[-600:]
        res = json.loads(line[len("@@MBPP@@"):])
        return bool(res["ok"]), res.get("why", "")


def sample(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    ids = sorted(t for t, r in rows().items() if _usable(r))
    rng.shuffle(ids)
    picked = []
    for tid in ids:
        if len(picked) >= n:
            break
        r = rows()[tid]
        if visible(r, r["canonical_solution"])[0] and hidden(r, r["canonical_solution"])[0]:
            picked.append(tid)
    return [{"task_id": f"mbpp-{t.split('/')[-1]}", "suite": "mbpp", "workspace": "mbpp", "mbpp_id": t} for t in picked]


def summary(task: dict, limit: int = 180) -> str:
    text = " ".join(rows()[task["mbpp_id"]]["prompt"].replace('"""', " ").split())
    text = text.split(" assert ")[0]
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def prompt(task: dict) -> str:
    return f"{summary(task)} -- {HOW}"


def files(task: dict, checker: Path) -> dict[str, str]:
    r = rows()[task["mbpp_id"]]
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["mbpp_id"]}", "solution.py"],\n'
            f'                       capture_output=True, text=True)\n'
            f'    print(r.stdout[-2000:])\n'
            f'    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]\n')
    return {"TASK.md": r["prompt"].replace('"""', "").strip() + "\n",
            "solution.py": f"{_signature(r)}\n    pass\n", "test_check.py": test}


def grade(task: dict, solution: str) -> dict:
    r = rows()[task["mbpp_id"]]
    vis, _ = visible(r, solution)
    ok, why = hidden(r, solution)
    return {"resolved": ok, "check_pass": vis, "error": None if ok else why}


if __name__ == "__main__":
    # the visible check: `python mbpp_task.py <task_id> <solution.py>` -> exit 0 when the 3 assertions pass
    tid, sol = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")
    ok, out = visible(rows()[tid], sol)
    print("CHECK PASS" if ok else f"CHECK FAIL: {out}")
    sys.exit(0 if ok else 1)
