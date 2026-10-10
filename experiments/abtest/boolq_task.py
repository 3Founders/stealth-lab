"""BoolQ (SuperGLUE) as A-vs-C tasks: answer a yes/no question about a passage.

The agent sees TASK.md (passage and question), answer.txt (empty) and test_check.py. BoolQ has no partial test of
an answer that is one bit, so the VISIBLE check is the answer's FORMAT only (exactly "true" or "false"); the GRADE
is the dataset's label. This is the "no real visible test" case: a cheap answer passes the check whatever it says,
and only the test-first tests (which cannot know the label either) stand between it and delivery. Reported apart.

Tasks: a seeded random sample of the 3,270 validation questions (no filtering).
"""
from __future__ import annotations

import random
import sys
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "boolq" / "data" / "validation.parquet"
CHECK = "python -m pytest -q test_check.py"


@lru_cache(maxsize=1)
def rows() -> list[dict]:
    import pyarrow.parquet as pq
    return pq.read_table(DATA).to_pylist()


def sample(n: int, seed: int) -> list[dict]:
    idx = list(range(len(rows())))
    random.Random(seed).shuffle(idx)
    return [{"task_id": f"boolq-{i}", "suite": "boolq", "workspace": "boolq", "boolq_idx": i} for i in idx[:n]]


def prompt(task: dict) -> str:
    q = rows()[task["boolq_idx"]]["question"]
    return (f"Answer this yes/no question from the passage in TASK.md: {q}? -- Write exactly true or false into "
            "answer.txt. Check it with `python -m pytest -q test_check.py`.")


def files(task: dict, checker: Path) -> dict[str, str]:
    r = rows()[task["boolq_idx"]]
    md = f"# Passage\n\n{r['passage']}\n\n# Question\n\n{r['question']}?\n\nWrite exactly `true` or `false` in answer.txt.\n"
    test = ('def test_check():\n'
            '    a = open("answer.txt", encoding="utf-8").read().strip().lower()\n'
            '    assert a in ("true", "false"), f"answer.txt must be exactly true or false, not {a!r}"\n')
    return {"TASK.md": md, "answer.txt": "", "test_check.py": test}


def solution_file(task: dict) -> str:
    return "answer.txt"


def grade(task: dict, solution: str) -> dict:
    a = solution.strip().lower()
    fmt = a in ("true", "false")
    ok = fmt and (a == "true") == bool(rows()[task["boolq_idx"]]["answer"])
    return {"resolved": ok, "check_pass": fmt, "error": None if ok else f"answered {a!r}"}


if __name__ == "__main__":
    print(grade({"boolq_idx": int(sys.argv[1])}, Path(sys.argv[2]).read_text(encoding="utf-8")))
