"""Validation-framework tasks as A-vs-C tasks: implement a record validator from a written spec.

The agent sees TASK.md (one record format: fields with types, ranges, regexes, required/optional, nested objects and
lists, cross-field rules, and the error code for each rule), validator.py (a stub of `validate(record) -> list[str]`)
and test_check.py. Each of the 18 specs (experiments/validation/data/specs.py) comes with 22-29 HAND-LABELLED
samples -- valid records, boundary cases and records breaking one or several rules -- in a fixed order.

Split (fixed before any run, the same for both arms): the VISIBLE check runs the samples at index 0, 2, 4, ... (every
other one); the GRADE runs ALL samples. A sample passes when the set of returned codes equals the labelled set.

Tasks: a seeded random sample (random.Random(seed)) of the 18 specs, keeping only those whose reference validator
(the same declaration that renders TASK.md) passes every labelled sample here -- no filtering on difficulty. Model
code runs in a subprocess, in a temp dir, with a timeout and no secrets in its env.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[0] / "validation" / "data"
CHECK = "python -m pytest -q test_check.py"
TIMEOUT_S = 60
_SAFE_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")
STUB = "def validate(record: dict) -> list[str]:\n    raise NotImplementedError\n"


@lru_cache(maxsize=1)
def specs():
    sys.path.insert(0, str(DATA))
    try:
        import specs as mod
    finally:
        sys.path.remove(str(DATA))
    return mod


def labelled(name: str) -> list[dict]:
    return specs().samples(name)


_HARNESS = r'''
import json, sys
ns = {"__name__": "validator"}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), "validator.py", "exec"), ns)
cases = json.load(open(sys.argv[2], encoding="utf-8"))
bad = []
for c in cases:
    try:
        got = ns["validate"](c["record"])
        got = sorted(set(got)) if isinstance(got, (list, tuple, set)) else got
    except Exception as e:
        got = f"raised {type(e).__name__}: {str(e)[:100]}"
    if got != c["expect"]:
        bad.append({"index": c["index"], "record": c["record"], "expected": c["expect"], "got": got})
print("@@VAL@@" + json.dumps({"n": len(cases), "bad": bad}))
'''


def _run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    env = {k: os.environ[k] for k in _SAFE_ENV if k in os.environ}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"})
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT_S, encoding="utf-8", errors="replace")


def run(name: str, solution: str, which: list[int]) -> tuple[bool, str]:
    cases = [{**c, "index": i} for i, c in enumerate(labelled(name)) if i in which]
    with tempfile.TemporaryDirectory(prefix="val_") as tmp:
        Path(tmp, "validator.py").write_text(solution, encoding="utf-8")
        Path(tmp, "h.py").write_text(_HARNESS, encoding="utf-8")
        Path(tmp, "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        try:
            p = _run(["h.py", "validator.py", "cases.json"], tmp)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        line = next((x for x in reversed(p.stdout.splitlines()) if x.startswith("@@VAL@@")), None)
        if line is None:
            return False, (p.stderr or p.stdout)[-800:]
        res = json.loads(line[len("@@VAL@@"):])
        if not res["bad"]:
            return True, f"{res['n']} samples pass"
        lines = [f"{len(res['bad'])} of {res['n']} samples wrong:"]
        for b in res["bad"][:5]:
            lines.append(f"  sample {b['index']}: record {json.dumps(b['record'])[:300]}\n"
                         f"    expected {b['expected']}, got {b['got']}")
        return False, "\n".join(lines)


def visible_idx(name: str) -> list[int]:
    return list(range(0, len(labelled(name)), 2))


def all_idx(name: str) -> list[int]:
    return list(range(len(labelled(name))))


def _reference_ok(name: str) -> bool:
    ref = specs().reference(name)
    return all(ref(c["record"]) == c["expect"] for c in labelled(name))


def sample(n: int, seed: int) -> list[dict]:
    names = sorted(specs().SPECS)
    random.Random(seed).shuffle(names)
    picked = [x for x in names if _reference_ok(x)][:n]
    return [{"task_id": f"val-{x}", "suite": "validation", "workspace": "validation", "val_spec": x} for x in picked]


def prompt(task: dict) -> str:
    title = specs().SPECS[task["val_spec"]].title
    return (f"{title}: implement validate(record) in validator.py so it returns the error codes of every rule a "
            "record breaks (the full spec and its conventions are in TASK.md). Check it with "
            "`python -m pytest -q test_check.py`.")


def files(task: dict, checker: Path) -> dict[str, str]:
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["val_spec"]}", "validator.py"],\n'
            f'                       capture_output=True, text=True, encoding="utf-8", errors="replace")\n'
            f'    print(r.stdout[-3000:])\n'
            f'    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]\n')
    return {"TASK.md": specs().task_md(task["val_spec"]), "validator.py": STUB, "test_check.py": test}


def solution_file(task: dict) -> str:
    return "validator.py"


def reference_solution(task: dict) -> str:
    """A validator.py that delegates to the reference engine (for validate_phase1b.py)."""
    return (f"import sys\nsys.path.insert(0, r\"{DATA}\")\nimport specs\n\n"
            f"validate = specs.reference({task['val_spec']!r})\n")


def grade(task: dict, solution: str) -> dict:
    name = task["val_spec"]
    vis, _ = run(name, solution, visible_idx(name))
    ok, why = run(name, solution, all_idx(name))
    return {"resolved": ok, "check_pass": vis, "error": None if ok else why[-600:]}


if __name__ == "__main__":
    # the visible check: `python validation_task.py <spec> <validator.py>` -> exit 0 when every visible sample passes
    name, sol = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")
    ok, out = run(name, sol, visible_idx(name))
    print("CHECK PASS" if ok else f"CHECK FAIL: {out}")
    sys.exit(0 if ok else 1)
