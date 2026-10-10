"""MultiPL-E HumanEval-JS as A-vs-C tasks: implement a JavaScript function; the check is `node --test`, not pytest.

The agent sees TASK.md, solution.js (the problem's comment, the function's signature and a CommonJS export) and
check.test.js. Split (fixed before any run, the same for both arms): the VISIBLE check runs the problem's asserts
at index 0, 2, 4, ... (every other one, in the dataset's order); the GRADE runs all of them. MultiPL-E ships no
reference JavaScript solution, so tasks are not filtered on one: a seeded random sample of the 161 problems.
"""
from __future__ import annotations

import random
import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "multiple" / "data" / "humaneval-js.parquet"
CHECK = "node --test check.test.js"
TIMEOUT_S = 60


@lru_cache(maxsize=1)
def rows() -> dict[str, dict]:
    import pyarrow.parquet as pq
    return {r["name"]: r for r in pq.read_table(DATA).to_pylist()}


def fn_name(r: dict) -> str:
    return re.findall(r"function\s+(\w+)\s*\(", r["prompt"])[-1]


def asserts(r: dict) -> list[str]:
    return [ln.strip() for ln in r["tests"].splitlines() if ln.strip().startswith("assert.")]


def test_file(r: dict, which: list[int], module: str = "./solution.js") -> str:
    lines = "\n".join(f"  {a}" for i, a in enumerate(asserts(r)) if i in which)
    return (f"const assert = require('node:assert');\nconst test = require('node:test');\n"
            f"const {{ {fn_name(r)} }} = require('{module}');\n\n"
            f"test('{fn_name(r)}', () => {{\n  const candidate = {fn_name(r)};\n{lines}\n}});\n")


def run(r: dict, solution: str, which: list[int]) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="mjs_") as tmp:
        Path(tmp, "solution.js").write_text(solution, encoding="utf-8")
        Path(tmp, "t.test.js").write_text(test_file(r, which), encoding="utf-8")
        try:
            p = subprocess.run(["node", "--test", "t.test.js"], cwd=tmp, capture_output=True, text=True,
                               timeout=TIMEOUT_S, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return p.returncode == 0, (p.stdout + p.stderr)[-800:]


def sample(n: int, seed: int) -> list[dict]:
    names = sorted(rows(), key=lambda x: int(x.split("_")[1]))
    random.Random(seed).shuffle(names)
    return [{"task_id": f"mjs-{x.split('_')[1]}", "suite": "multiple_js", "workspace": "multiple-js", "mjs_name": x}
            for x in names[:n]]


def prompt(task: dict) -> str:
    r = rows()[task["mjs_name"]]
    first = " ".join(ln.lstrip("/ ").strip() for ln in r["prompt"].splitlines() if ln.startswith("//"))[:170]
    return (f"{first} -- Implement {fn_name(r)} in solution.js (JavaScript; keep its name and the export; the task "
            "is in TASK.md). Check it with `node --test check.test.js`.")


def files(task: dict, checker: Path) -> dict[str, str]:
    r = rows()[task["mjs_name"]]
    vis = list(range(0, len(asserts(r)), 2))
    stub = r["prompt"].rstrip() + "\n  // TODO\n}\n\nmodule.exports = { " + fn_name(r) + " };\n"
    return {"TASK.md": "# Task\n\nImplement this JavaScript function in `solution.js`:\n\n```js\n" + r["prompt"] +
                       "}\n```\n",
            "solution.js": stub, "check.test.js": test_file(r, vis)}


def solution_file(task: dict) -> str:
    return "solution.js"


def grade(task: dict, solution: str) -> dict:
    r = rows()[task["mjs_name"]]
    n = len(asserts(r))
    vis, _ = run(r, solution, list(range(0, n, 2)))
    ok, out = run(r, solution, list(range(n)))
    return {"resolved": ok, "check_pass": vis, "error": None if ok else out[-300:]}


if __name__ == "__main__":
    print(grade({"mjs_name": sys.argv[1]}, Path(sys.argv[2]).read_text(encoding="utf-8")))
