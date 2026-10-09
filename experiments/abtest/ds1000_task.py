"""DS-1000 as A-vs-C tasks: workspace files, the agent-visible check, and the hidden grade.

The agent sees TASK.md (the problem), solution.py (where its code goes) and test_check.py. The check is DS-1000's
own CHECK (experiments/ds1000/evaluate.py): test case 1 for problems with several, else a smoke run -- the expected
answer is never shown to it. It is a pytest test, so the capture hook reads its verdict like any project's tests.
The grade (GOLD: every test case, DS-1000's own grading) runs after the session, outside the workspace.
"""
from __future__ import annotations

import json
import random
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "ds1000"
sys.path.insert(0, str(ROOT))
import evaluate  # noqa: E402

LIBRARIES = ("Pandas", "Numpy", "Sklearn", "Scipy")
# not: Pytorch / Tensorflow (not in the eval env); Matplotlib (its grader fails even the reference solution here)
TIMEOUT_S = 240.0   # cold imports in the eval env take up to ~80 s (sklearn)


@lru_cache(maxsize=1)
def problems() -> dict[str, dict]:
    out = {}
    for line in (ROOT / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[str(r["metadata"]["problem_id"])] = r
    return out


def sample(n: int, seed: int) -> list[dict]:
    """n tasks, round-robin over LIBRARIES, original (unperturbed) problems, in a seeded order."""
    rng = random.Random(seed)
    # only problems with a REAL visible test (test case 1 of >= 2): a single-case problem leaves the agent a smoke run
    # ("the code runs"), which checks nothing -- dispatch (and any agent) would accept an unchecked answer
    pools = {lib: [p for p in problems().values() if p["metadata"]["library"] == lib
                   and p["metadata"]["perturbation_type"] == "Origin"
                   and evaluate.check_kind(p["code_context"], int(p["metadata"]["test_case_cnt"])) == "case1"]
             for lib in LIBRARIES}
    for pool in pools.values():
        rng.shuffle(pool)
    picked = []
    while len(picked) < n and any(pools.values()):              # stops when every pool is used up
        for lib in LIBRARIES:
            if len(picked) < n and pools[lib]:
                picked.append(pools[lib].pop())
    return [{"task_id": f"ds1000-{p['metadata']['problem_id']}", "suite": "ds1000",
             "workspace": f"ds1000-{p['metadata']['library'].lower()}", "problem_id": str(p["metadata"]["problem_id"])}
            for p in picked]


CHECK = "python -m pytest -q test_check.py"   # the task's own check: the agent runs it, and so does dispatch
HOW = ("The full problem is in TASK.md. Put your code in solution.py: only the code that goes where the problem's "
       "solution placeholder is (it is inserted into the problem's own context and must produce what the problem "
       "asks, e.g. assign `result`). Check it with `python -m pytest -q test_check.py`.")


def summary(task: dict, limit: int = 180) -> str:
    """The problem's opening, one line -- what a developer would type first (and the library's entry title)."""
    text = problems()[task["problem_id"]]["prompt"].replace("Problem:", " ")
    text = " ".join(text.split())
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "))
    return (cut[:end + 1] if end > 60 else cut.rstrip() + "...").strip()


def prompt(task: dict) -> str:
    return f"{summary(task)} -- {HOW}"


def files(task: dict, checker: Path) -> dict[str, str]:
    p = problems()[task["problem_id"]]
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["problem_id"]}", "solution.py"],\n'
            f'                       capture_output=True, text=True)\n'
            f'    print(r.stdout[-2000:])\n'
            f'    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]\n')
    return {"TASK.md": p["prompt"], "solution.py": "# your solution code here\n", "test_check.py": test}


def grade(task: dict, solution: str) -> dict:
    p = problems()[task["problem_id"]]
    g = evaluate.grade(solution, p["code_context"], int(p["metadata"]["test_case_cnt"]), timeout_s=TIMEOUT_S)
    return {"resolved": bool(g["gold_pass"]), "check_pass": bool(g["check_pass"]), "error": g.get("error")}


if __name__ == "__main__":
    # the visible check: `python ds1000_task.py <problem_id> <solution.py>` -> exit 0 when DS-1000's CHECK passes
    pid, sol = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")
    p = problems()[pid]
    g = evaluate.grade(sol, p["code_context"], int(p["metadata"]["test_case_cnt"]), timeout_s=TIMEOUT_S)
    print("CHECK PASS" if g["check_pass"] else f"CHECK FAIL: {(g.get('error') or 'wrong result')[-1500:]}")
    sys.exit(0 if g["check_pass"] else 1)
