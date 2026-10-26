"""Offline tests of the long-horizon series harness (runner.py --series, arm C0, report.py --curve).

No model, no server, no keys, no network: `run_claude` is replaced by a stand-in that "solves" each task by writing the
function the way a careful developer would (or carelessly, to show the hidden grade catches it), and `install_c` by a
marker file. Run from experiments/abtest:  python -m pytest test_series_offline.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import report  # noqa: E402
import runner  # noqa: E402
import series_toy_task as toy  # noqa: E402

GOOD = {
    "slugify": '''def slugify(text):
    """Lower-case slug."""
    import re
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
''',
    "word_count": '''def word_count(text):
    """Number of words."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return len(text.split())
''',
    "title_case": '''def title_case(text):
    """Title-case each word."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return " ".join(w[:1].upper() + w[1:].lower() for w in text.split(" "))
''',
}
CARELESS = {  # passes the visible check, ignores the repo's conventions (no TypeError, no docstring)
    "slugify": "def slugify(text):\n    import re\n    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n",
}


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOCAL", tmp_path / ".local")

    def fake_install(ws):                         # what setup_c.mjs would write: here only a marker
        (ws / ".claude").mkdir(exist_ok=True)
        (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(runner, "install_c", fake_install)
    monkeypatch.setattr(runner, "setup_survey", lambda arm, ws, env: None)      # the survey is tested in test_series10_arms_offline.py
    solutions = dict(GOOD)
    seen = []

    def fake_claude(ws, prompt, env, extra, log_path):
        fn = next(t["fn"] for t in toy._TASKS if t["fn"] in prompt)
        arm = ws.parent.name
        seen.append((arm, fn, sorted(p.name for p in (ws / ".kell").iterdir()) if (ws / ".kell").exists() else None))
        with (ws / "textutils.py").open("a", encoding="utf-8") as f:
            f.write("\n" + solutions[fn])
        if arm in ("C", "C0"):                    # the hooks' library growth: one file per task
            (ws / ".kell" / f"learned-{fn}.md").write_text(fn, encoding="utf-8")
        log_path.write_text("", encoding="utf-8")
        return {"status": "done", "is_error": False, "claude_cost_usd": 0.10, "turns": 3, "duration_ms": 1000,
                "models": [], "model_usage": {}, "final": "ok"}

    monkeypatch.setattr(runner, "run_claude", fake_claude)
    return solutions, seen


SECRETS = {"token": "t", "gc": "k"}


def run_series(run="toyrun", arms=("A", "C", "C0")):
    recs = []
    for task in runner.load_series("series_toy_task"):
        for arm in arms:
            recs.append(runner.run_one(task, arm, run, SECRETS))
    return recs


def test_load_series_orders_the_tasks_and_marks_position_and_series():
    tasks = runner.load_series("series_toy_task:toy")
    assert [t["task_id"] for t in tasks] == ["toy-1-slugify", "toy-2-word_count", "toy-3-title_case"]
    assert [t["position"] for t in tasks] == [1, 2, 3]
    assert {t["series"] for t in tasks} == {"toy"} and {t["workspace"] for t in tasks} == {"toy-series"}


def test_load_series_refuses_a_series_that_spans_workspaces(monkeypatch):
    class Bad:
        @staticmethod
        def series(name):
            return [{"task_id": "a", "suite": "s", "workspace": "w1"}, {"task_id": "b", "suite": "s", "workspace": "w2"}]
    monkeypatch.setitem(sys.modules, "bad_series", Bad)
    with pytest.raises(SystemExit, match="share ONE workspace"):
        runner.load_series("bad_series")


def test_wipe_stealth_empties_it_but_keeps_the_directory_and_its_keep(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".kell" / "library").mkdir(parents=True)
    (ws / ".kell" / "library.md").write_text("x", encoding="utf-8")
    (ws / ".kell" / "library" / "L-1.diff").write_text("x", encoding="utf-8")
    (ws / ".kell" / ".keep").write_text("", encoding="utf-8")
    runner.wipe_stealth(ws)
    assert sorted(p.name for p in (ws / ".kell").iterdir()) == [".keep"]
    runner.wipe_stealth(tmp_path / "fresh")                 # a workspace with no .kell yet
    assert (tmp_path / "fresh" / ".kell" / ".keep").exists()


def test_the_three_arms_over_a_series(sandbox):
    _, seen = sandbox
    recs = run_series()
    by = {(r["task_id"], r["arm"]): r for r in recs}
    assert all(r["resolved"] for r in recs), [(r["task_id"], r["arm"], r["grade"]["error"]) for r in recs if not r["resolved"]]
    assert {(r["series"], r["position"]) for r in recs} == {("series_toy_task", 1), ("series_toy_task", 2), ("series_toy_task", 3)}
    # what each arm's workspace holds when its task starts
    start = {(arm, fn): files for arm, fn, files in seen}
    assert start[("A", "slugify")] is None and start[("A", "title_case")] is None, "A never has a .kell/"
    assert start[("C", "slugify")] == [".keep"]
    assert start[("C", "word_count")] == [".keep", "learned-slugify.md"], "C carries the first task's library to the second"
    assert start[("C", "title_case")] == [".keep", "learned-slugify.md", "learned-word_count.md"]
    assert start[("C0", "word_count")] == [".keep"], "C0 starts every task with nothing"
    assert start[("C0", "title_case")] == [".keep"]
    # the shared project is built once per arm: the repo persists, earlier functions are still there at the end
    for arm in ("A", "C", "C0"):
        text = (runner.LOCAL / "ws" / arm / "toy-series" / "textutils.py").read_text(encoding="utf-8")
        assert all(f"def {t['fn']}" in text for t in toy._TASKS)
    assert (runner.LOCAL / "ws" / "C0" / "toy-series" / ".claude" / "settings.json").exists(), "C0 is installed like C"
    assert by[("toy-3-title_case", "C")]["library_entries"] == 0     # no library.md in this stand-in; the key is present


def test_the_hidden_grade_catches_a_task_that_ignores_the_repos_conventions(sandbox):
    solutions, _ = sandbox
    solutions.update(CARELESS)
    task = runner.load_series("series_toy_task")[0]
    rec = runner.run_one(task, "A", "careless", SECRETS)
    assert rec["status"] == "done" and rec["resolved"] is False
    assert "no docstring" in rec["grade"]["error"] and "expected TypeError" in rec["grade"]["error"]


def test_a_later_task_is_graded_on_the_earlier_ones_too(sandbox):
    solutions, _ = sandbox
    solutions.update(CARELESS)
    tasks = runner.load_series("series_toy_task")
    runner.run_one(tasks[0], "A", "careless", SECRETS)              # task 1 careless
    solutions.update(GOOD)
    rec2 = runner.run_one(tasks[1], "A", "careless", SECRETS)        # task 2 careful, but task 1's function is still careless
    assert rec2["resolved"] is False and "slugify" in rec2["grade"]["error"]


def test_dry_run_prints_the_plan_and_runs_nothing(sandbox, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runner.py", "--run", "d", "--series", "series_toy_task", "--arms", "A,C,C0", "--dry-run"])
    assert runner.main() == 0
    out = capsys.readouterr().out
    assert "3 task(s), arms A,C,C0" in out
    assert "arm C0: the same install as C, but .kell/ is wiped before every task" in out
    assert out.count("toy-series") == 3
    assert not (runner.LOCAL / "ws").exists(), "a dry run creates no workspace"


def test_arg_checks(monkeypatch):
    for argv, msg in ([["--run", "x"], "exactly one of --tasks and --series"],
                      [["--run", "x", "--tasks", "mbpp:1", "--series", "series_toy_task"], "exactly one"],
                      [["--run", "x", "--tasks", "mbpp:1", "--arms", "A,C0"], "only with --series"],
                      [["--run", "x", "--series", "series_toy_task", "--arms", "A,Z"], "arms are"]):
        monkeypatch.setattr(sys, "argv", ["runner.py", *argv])
        with pytest.raises(SystemExit, match=msg):
            runner.main()


def _row(task, arm, pos, ok, cost):
    return {"task_id": task, "arm": arm, "position": pos, "series": "toy", "resolved": ok, "total_cost_usd": cost, "status": "done"}


def test_the_learning_curve_compares_the_arms_position_by_position():
    rows = {}
    for pos, (a, c, c0) in enumerate([(1.0, 1.0, 1.0), (1.0, 0.5, 1.0), (1.0, 0.25, 1.0)], start=1):
        for arm, cost in (("A", a), ("C", c), ("C0", c0)):
            rows[(f"t{pos}", arm)] = _row(f"t{pos}", arm, pos, True, cost)
    rows[("t4", "A")] = _row("t4", "A", 4, True, 1.0)                # position 4 not finished in every arm: not counted
    text = report.curve(rows)
    lines = text.splitlines()
    assert lines[0].split("|")[1].strip().startswith("A solved") and "C0 solved" in lines[0]
    assert any(l.strip().startswith("3") and "0.250" in l for l in lines)
    assert not any(l.strip().startswith("4") for l in lines[2:5])
    assert "positions after the first, C vs A: -62% cost, 2/2 solved (A 2/2)" in text
    assert "positions after the first, C0 vs A: +0% cost, 2/2 solved (A 2/2)" in text
    assert report.curve({}) == "no position is finished in every arm yet"
