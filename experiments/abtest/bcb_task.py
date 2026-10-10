"""BigCodeBench as A-vs-C tasks: practical Python functions (files, text, dates, data, ...) with unit-test suites.

The agent sees TASK.md (the task), solution.py (the function's signature and imports, from the dataset's code
prompt) and test_check.py, which runs the task's VISIBLE tests -- about a third of its unit tests, chosen by the
product's benchmark importer (app.benchmarks.bigcodebench). The grade runs the FULL suite, after the session,
outside the workspace. Only tasks whose libraries are on the safe list and installed here, and whose reference
solution passes the full suite here, are used (experiments/bigcodebench/safety.py, the earlier experiment's rules).
"""
from __future__ import annotations

import random
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "bigcodebench"
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.benchmarks import bigcodebench as bcb  # noqa: E402


def _load(name: str, file: Path):
    """BigCodeBench's own evaluate.py / safety.py, by path: DS-1000 also has an `evaluate` module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_safety = _load("bcb_safety", ROOT / "safety.py")
sys.modules.setdefault("safety", _safety)              # evaluate.py imports `safety`
run_tests = _load("bcb_evaluate", ROOT / "evaluate.py").run_tests
runnable_locally = _safety.runnable_locally

TIMEOUT_S = 120.0
CHECK = "python -m pytest -q test_check.py"   # the task's own visible tests: the agent runs it, and so does dispatch
HOW = ("Implement the function in solution.py (keep its name and signature; the task is in TASK.md). Check it with "
       "`python -m pytest -q test_check.py`.")


@lru_cache(maxsize=1)
def _data() -> tuple[dict, dict]:
    rows = {r["task_id"]: r for r in bcb.load_rows(ROOT / "data" / f"bigcodebench-{bcb.LATEST_VERSION}.parquet")}
    tasks = {t.external_id: t for t in bcb.tasks_from_rows(rows.values())}
    return rows, tasks


def _valid(tid: str) -> bool:
    rows, tasks = _data()
    t, r = tasks[tid], rows[tid]
    if t.excluded_reason is not None or not t.visible_tests or not runnable_locally(t.libs, t.test_code)[0]:
        return False
    return bool(run_tests(r["code_prompt"] + r["canonical_solution"], t.test_code, t.visible_tests,
                          timeout_s=TIMEOUT_S)["gold_pass"])


def sample(n: int, seed: int) -> list[dict]:
    """n tasks, round-robin over primary domains, in a seeded order; each one's reference passes here."""
    rows, tasks = _data()
    rng = random.Random(seed)
    by_domain: dict[str, list[str]] = {}
    for tid, t in sorted(tasks.items()):
        if t.excluded_reason is None and t.visible_tests and runnable_locally(t.libs, t.test_code)[0]:
            by_domain.setdefault(t.domains[0] if t.domains else "general", []).append(tid)
    for pool in by_domain.values():
        rng.shuffle(pool)
    picked: list[str] = []
    while len(picked) < n and any(by_domain.values()):
        for d in sorted(by_domain):
            while by_domain[d] and len(picked) < n:
                tid = by_domain[d].pop()
                if _valid(tid):
                    picked.append(tid)
                    break
    return [{"task_id": f"bcb-{tid.split('/')[-1]}", "suite": "bcb",
             "workspace": "bcb-" + "-".join(
                 w for w in (tasks[tid].domains[0] if tasks[tid].domains else "general").lower().split()
                 if w.isalnum() and w not in ("and", "in", "python"))[:40],
             "bcb_id": tid} for tid in picked]


def summary(task: dict, limit: int = 180) -> str:
    rows, _ = _data()
    text = " ".join(str(rows[task["bcb_id"]]["instruct_prompt"]).split())
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "))
    return (cut[:end + 1] if end > 60 else cut.rstrip() + "...").strip()


def prompt(task: dict) -> str:
    return f"{summary(task)} -- {HOW}"


def files(task: dict, checker: Path) -> dict[str, str]:
    rows, _ = _data()
    r = rows[task["bcb_id"]]
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["bcb_id"]}", "solution.py"],\n'
            f'                       capture_output=True, text=True)\n'
            f'    print(r.stdout[-2000:])\n'
            f'    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]\n')
    return {"TASK.md": str(r["instruct_prompt"]), "solution.py": str(r["code_prompt"]), "test_check.py": test}


def grade(task: dict, solution: str) -> dict:
    _, tasks = _data()
    t = tasks[task["bcb_id"]]
    g = run_tests(solution, t.test_code, t.visible_tests, timeout_s=TIMEOUT_S)
    return {"resolved": bool(g["gold_pass"]), "check_pass": bool(g["visible_pass"]), "error": g.get("error")}


if __name__ == "__main__":
    # the visible check: `python bcb_task.py <task_id> <solution.py>` -> exit 0 when the visible tests pass
    tid, sol = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")
    _, tasks = _data()
    t = tasks[tid]
    g = run_tests(sol, t.test_code, t.visible_tests, timeout_s=TIMEOUT_S)
    failed = {k: v for k, v in (g.get("tests") or {}).items() if k in set(t.visible_tests) and v != "pass"}
    print("CHECK PASS" if g["visible_pass"] else f"CHECK FAIL: {failed or (g.get('error') or '')[-1500:]}")
    sys.exit(0 if g["visible_pass"] else 1)
