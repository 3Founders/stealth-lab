"""A toy long-horizon SERIES: three tasks in ONE small Python repo, in order. A working template for real series
(Spider 2.0-DBT, SWE-rebench ...) and the module the offline tests drive; it needs no dataset and no network.

What a series is: tasks that share one workspace and arrive in real order. Arm A's and C's workspaces (and C's .kell/)
persist from task to task; C0 is C with .kell/ wiped before every task. So what each later task can reuse -- the
conventions the first task established, the repo's facts, the earlier fixes -- is exactly what the three arms differ in.

The repo is `textutils.py`. Every task adds one function and the repo has ONE convention (NOTES.md): every public function
raises TypeError for a non-str argument. The visible check (test_check.py, rewritten per task) covers the new function's
happy path only; the hidden grade also checks the convention and that the earlier functions still work -- so a task that
ignores what the first one set up can pass its visible check and still fail the grade.

The module contract (runner.py reads exactly this):
  series(name)              -> ordered task dicts {task_id, suite, workspace, ...}, one shared workspace
  setup_project(task, ws)   -> build the shared project once per workspace and arm
  files(task, checker)      -> {relative path: text} written before the task (instructions, the visible check)
  prompt(task)              -> one line, what a developer would type
  grade_workspace(task, ws) -> {"resolved": bool, "check_pass": bool, "error": str|None}, from the hidden grader
  CHECK                     -> the visible check command (dispatch's STEALTHLAB_DISPATCH_CHECK)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CHECK = "python -m pytest -q test_check.py"
TIMEOUT_S = 60
WORKSPACE = "toy-series"
HOW = "Add it to textutils.py (see NOTES.md for the repo's conventions). Check it with `python -m pytest -q test_check.py`."

_TASKS = [
    {"fn": "slugify", "ask": "slugify(text): lower-case, runs of non-alphanumerics become one '-', no leading or trailing '-'",
     "visible": [("slugify('Hello, World!')", "'hello-world'")],
     "hidden": [("slugify('  A  b--C ')", "'a-b-c'"), ("slugify('')", "''"), ("slugify('x')", "'x'")]},
    {"fn": "word_count", "ask": "word_count(text): the number of whitespace-separated words",
     "visible": [("word_count('a b  c')", "3")],
     "hidden": [("word_count('')", "0"), ("word_count('  one ')", "1"), ("word_count('a\\nb\\tc')", "3")]},
    {"fn": "title_case", "ask": "title_case(text): first letter of every word upper-case, the rest lower-case",
     "visible": [("title_case('hello wORLD')", "'Hello World'")],
     "hidden": [("title_case('')", "''"), ("title_case('a b')", "'A B'"), ("title_case('ÉCOLE')", "'École'")]},
]

NOTES = """# textutils conventions

- Every public function in textutils.py raises TypeError when its argument is not a str.
- Every public function has a one-line docstring.
- No third-party imports.
"""


def series(name: str | None = None) -> list[dict]:
    if name not in (None, "", "toy"):
        raise SystemExit(f"series_toy_task has one series, 'toy' (got {name!r})")
    return [{"task_id": f"toy-{i + 1}-{t['fn']}", "suite": "toy", "workspace": WORKSPACE, "index": i} for i, t in enumerate(_TASKS)]


def sample(n: int, seed: int) -> list[dict]:        # the module also works as a plain suite
    return series(None)[:n]


def setup_project(task: dict, ws: Path) -> None:
    (ws / "textutils.py").write_text('"""Small text helpers."""\n', encoding="utf-8")
    (ws / "NOTES.md").write_text(NOTES, encoding="utf-8")


def files(task: dict, checker: Path) -> dict[str, str]:
    t = _TASKS[task["index"]]
    asserts = "\n".join(f"    assert {call} == {want}" for call, want in t["visible"])
    return {
        "TASK.md": f"# Task {task['index'] + 1} of {len(_TASKS)}\n\nAdd {t['ask']}.\n",
        "test_check.py": f"from textutils import {t['fn']}\n\n\ndef test_{t['fn']}():\n{asserts}\n",
    }


def prompt(task: dict) -> str:
    t = _TASKS[task["index"]]
    return f"Add {t['ask']} -- {HOW}"


_GRADER = r'''
import ast, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("textutils", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
tasks = json.loads(sys.argv[2])
problems = []
src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src)
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
        if not ast.get_docstring(node):
            problems.append(f"{node.name}: no docstring")
        try:
            getattr(mod, node.name)(123)
            problems.append(f"{node.name}: accepted a non-str")
        except TypeError:
            pass
        except Exception as e:
            problems.append(f"{node.name}: {type(e).__name__} for a non-str, expected TypeError")
for t in tasks:
    fn = getattr(mod, t["fn"], None)
    if fn is None:
        problems.append(f"{t['fn']} is missing")
        continue
    for call, want in t["visible"] + t["hidden"]:
        try:
            got = eval(call, {t["fn"]: fn})
            if got != eval(want):
                problems.append(f"{call} -> {got!r}, expected {want}")
        except Exception as e:
            problems.append(f"{call} raised {type(e).__name__}")
print(json.dumps(problems))
'''


def grade_workspace(task: dict, ws: Path) -> dict:
    """The hidden grade: this task's function AND every earlier one still right, plus the repo's conventions."""
    upto = _TASKS[: task["index"] + 1]
    with tempfile.TemporaryDirectory() as tmp:
        g = Path(tmp) / "grader.py"
        g.write_text(_GRADER, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(g), str(ws / "textutils.py"), json.dumps(upto)],
                               capture_output=True, text=True, timeout=TIMEOUT_S, cwd=tmp)
        except subprocess.TimeoutExpired:
            return {"resolved": False, "check_pass": False, "error": "timeout"}
    if r.returncode != 0:
        return {"resolved": False, "check_pass": False, "error": (r.stderr or r.stdout)[-300:]}
    problems = json.loads(r.stdout.strip().splitlines()[-1])
    return {"resolved": not problems, "check_pass": not problems, "error": "; ".join(problems)[:300] or None}


def issue_text(task: dict) -> str:
    """The task as an issue (multiturn.py rewrites it as a vague opener): what is wanted, without the repo's conventions
    (NOTES.md holds those -- a task that never looks there is what the hidden grade catches)."""
    return f"Feature request: textutils.py needs {_TASKS[task['index']]['ask']}."
