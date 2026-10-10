"""BIRD-SQL Mini-Dev (SQLite) as A-vs-C tasks: answer a business question with one SQL query on a real database.

The agent sees TASK.md (the question, BIRD's evidence hint and the database's path), query.sql (empty) and
test_check.py. Split (fixed before any run, the same for both arms): the VISIBLE check runs the query and compares
the SHAPE of its result -- number of rows and of columns -- with the reference query's; the GRADE is BIRD's own
execution accuracy: the set of result rows equals the reference's. A query that returns the right shape with the
wrong values passes the check and fails the grade, as a real analyst's spot check would.

Databases: BIRD Mini-Dev's 11 SQLite files, copied to .local/bird_dbs (outside the data folder the agents may not
read); the check opens them read-only. Tasks: a seeded random sample of the 500, keeping only those whose reference
query runs here within the time limit (no filtering on difficulty).
"""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import sys
import threading
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[0] / "bird" / "data" / "minidev" / "MINIDEV"
DBS = HERE / ".local" / "bird_dbs"
CHECK = "python -m pytest -q test_check.py"
TIMEOUT_S = 60
RULE = (" Work only inside this directory and the database file TASK.md names (read it, never change it): do not "
        "open, list or search any other file outside this directory.")


@lru_cache(maxsize=1)
def rows() -> dict[str, dict]:
    return {str(r["question_id"]): r for r in json.loads((DATA / "mini_dev_sqlite.json").read_text(encoding="utf-8"))}


def db_path(db_id: str) -> Path:
    dst = DBS / f"{db_id}.sqlite"
    if not dst.exists():
        DBS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DATA / "dev_databases" / db_id / f"{db_id}.sqlite", dst)
    return dst


def run_sql(db_id: str, sql: str, timeout_s: float = TIMEOUT_S) -> list[tuple]:
    """The query's rows, on a read-only connection; raises on an error or after timeout_s."""
    con = sqlite3.connect(f"file:{db_path(db_id).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        return con.execute(sql).fetchall()
    finally:
        timer.cancel()
        con.close()


def check(r: dict, sql: str, full: bool) -> tuple[bool, str]:
    try:
        got = run_sql(r["db_id"], sql)
    except Exception as e:  # noqa: BLE001 -- any SQL error is a failed check
        return False, f"the query failed: {type(e).__name__}: {str(e)[:200]}"
    want = run_sql(r["db_id"], r["SQL"])
    if not full:
        gs = (len(got), len(got[0]) if got else 0)
        ws = (len(want), len(want[0]) if want else 0)
        return gs == ws, "" if gs == ws else f"result shape (rows, columns) is {gs}, expected {ws}"
    return set(got) == set(want), "" if set(got) == set(want) else "the result rows differ from the expected ones"


def sample(n: int, seed: int) -> list[dict]:
    ids = sorted(rows(), key=int)
    random.Random(seed).shuffle(ids)
    picked = []
    for qid in ids:
        if len(picked) >= n:
            break
        try:
            run_sql(rows()[qid]["db_id"], rows()[qid]["SQL"], 30)
        except Exception:  # noqa: BLE001 -- a reference that does not run here is not a task
            continue
        picked.append(qid)
    return [{"task_id": f"bird-{q}", "suite": "bird", "workspace": f"bird-{rows()[q]['db_id']}", "bird_id": q}
            for q in picked]


def summary(task: dict, limit: int = 180) -> str:
    text = " ".join(rows()[task["bird_id"]]["question"].split())
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def prompt(task: dict) -> str:
    return (f"{summary(task)} -- Write one SQLite query that answers it into query.sql (the full question, a hint "
            "and the database path are in TASK.md). Check it with `python -m pytest -q test_check.py`.")


def files(task: dict, checker: Path) -> dict[str, str]:
    r = rows()[task["bird_id"]]
    md = (f"# Question\n\n{r['question']}\n\n# Hint\n\n{r['evidence'] or '(none)'}\n\n# Database\n\n"
          f"SQLite file (read-only): `{db_path(r['db_id'])}`\n\nPut ONE SQLite query answering the question in "
          f"`query.sql`.\n")
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["bird_id"]}", "query.sql"],\n'
            f'                       capture_output=True, text=True)\n'
            f'    print(r.stdout[-2000:])\n'
            f'    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]\n')
    return {"TASK.md": md, "query.sql": "", "test_check.py": test}


def solution_file(task: dict) -> str:
    return "query.sql"


def grade(task: dict, solution: str) -> dict:
    r = rows()[task["bird_id"]]
    vis, _ = check(r, solution, full=False)
    ok, why = check(r, solution, full=True)
    return {"resolved": ok, "check_pass": vis, "error": None if ok else why}


if __name__ == "__main__":
    # the visible check: `python bird_task.py <question_id> <query.sql>` -> exit 0 when the result's shape matches
    qid, sql = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")
    ok, why = check(rows()[qid], sql, full=False)
    print("CHECK PASS" if ok else f"CHECK FAIL: {why}")
    sys.exit(0 if ok else 1)
