"""Offline tests of the real-repository series module (swe_series_task.py) and the runner hooks it needs.

No network, no model, no `uv`: the "upstream repository" is a small git repo built in a temp dir (two issues), the
SWE-rebench records are written from its real `git diff`s, and the Python toolchain is a stand-in that uses this
interpreter (no venv, no installs; pytest runs for real). Run from experiments/abtest:
    python -m pytest test_swe_series_offline.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner  # noqa: E402
import swe_series_task as swe  # noqa: E402

BUG = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = BUG.replace("return a - b\n\n\ndef sub", "return a + b\n\n\ndef sub")
WITH_MUL = FIXED + "\n\ndef mul(a, b):\n    return a * b\n"
T1 = "from mini import sub\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n"
T2 = T1.replace("import sub", "import add, sub") + "\n\ndef test_add():\n    assert add(2, 3) == 5\n"
T3 = T2.replace("import add, sub", "import add, mul, sub") + "\n\ndef test_mul():\n    assert mul(4, 3) == 12\n"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture()
def upstream(tmp_path, monkeypatch):
    """A tiny upstream with two issues (add is wrong; mul is missing), the records for them, and the module pointed at it."""
    up = tmp_path / "upstream"
    (up / "tests").mkdir(parents=True)
    git(up, "init", "-q")
    git(up, "config", "user.email", "t@t.invalid")
    git(up, "config", "user.name", "t")
    shas = []
    for mini, test in ((BUG, T1), (FIXED, T2), (WITH_MUL, T3)):
        (up / "mini.py").write_text(mini, encoding="utf-8", newline="\n")
        (up / "tests" / "test_mini.py").write_text(test, encoding="utf-8", newline="\n")
        git(up, "add", "-A")
        git(up, "commit", "-q", "-m", f"c{len(shas) + 1}")
        shas.append(git(up, "rev-parse", "HEAD").strip())

    def diff(a, b, path):
        return git(up, "diff", "--no-color", a, b, "--", path)

    base = {"repo": "x/mini", "install_config": {"python": "3.9", "test_cmd": "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider"},
            "requirements": "pytest==8.3.5\n-e git+https://github.com/x/mini.git@abc#egg=mini\n", "created_at": "2024-01-01 00:00:00"}
    recs = [
        {**base, "instance_id": "x__mini-1", "base_commit": shas[0], "problem_statement": "add() subtracts instead of adding.\n\nSteps:\n1. add(2, 3)\n",
         "patch": diff(shas[0], shas[1], "mini.py"), "test_patch": diff(shas[0], shas[1], "tests"),
         "FAIL_TO_PASS": ["tests/test_mini.py::test_add"], "PASS_TO_PASS": ["tests/test_mini.py::test_sub"]},
        {**base, "instance_id": "x__mini-2", "base_commit": shas[1], "problem_statement": "there is no mul()",
         "patch": diff(shas[1], shas[2], "mini.py"), "test_patch": diff(shas[1], shas[2], "tests"),
         "FAIL_TO_PASS": ["tests/test_mini.py::test_mul"], "PASS_TO_PASS": ["tests/test_mini.py::test_add", "tests/test_mini.py::test_sub"]},
    ]
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "mini.json").write_text(json.dumps(recs), encoding="utf-8")

    class Stand(swe.UvToolchain):
        installs: list = []

        def venv(self, path, python):
            return Path(sys.executable)

        def install(self, py, requirements):
            Stand.installs.append(("deps", list(requirements)))

        def install_editable(self, py, project):
            Stand.installs.append(("editable", project.name))

    monkeypatch.setattr(swe, "LOCAL", tmp_path / "swe")
    monkeypatch.setattr(swe, "DATA", tmp_path / "tasks")
    monkeypatch.setattr(swe, "CLONES", tmp_path / "swe" / "repos")
    monkeypatch.setattr(swe, "VENVS", tmp_path / "swe" / "venvs")
    monkeypatch.setattr(swe, "SERIES", {"mini": {"repo": "x/mini", "python": "3.9", "issues": ["x__mini-1", "x__mini-2"]}})
    monkeypatch.setattr(swe, "SKIPPED", {})
    monkeypatch.setattr(swe, "clone_url", lambda repo: str(up))
    monkeypatch.setattr(swe, "TOOLCHAIN", Stand())
    Stand.installs = []
    return {"up": up, "shas": shas, "recs": recs, "toolchain": Stand}


def ws_dir(tmp_path, arm="A"):
    ws = tmp_path / "ws" / arm / "swe-mini"
    ws.mkdir(parents=True)
    return ws


# ---------------------------------------------------------------- the series and its text

def test_the_series_is_the_issues_in_order_in_one_workspace(upstream):
    tasks = swe.series("mini")
    assert [t["task_id"] for t in tasks] == ["x__mini-1", "x__mini-2"]
    assert {t["workspace"] for t in tasks} == {"swe-mini"} and [t["index"] for t in tasks] == [0, 1]
    assert swe.series(None) == tasks and swe.sample(1, 0) == tasks[:1]
    with pytest.raises(SystemExit, match="unknown swe series"):
        swe.series("nope")
    loaded = runner.load_series("swe_series_task:mini")
    assert [t["position"] for t in loaded] == [1, 2] and loaded[0]["series"] == "mini"


def test_the_prompt_is_one_line_and_cut_while_issue_text_keeps_the_issue(upstream):
    t = swe.series("mini")[0]
    assert "\n" not in swe.prompt(t) and swe.prompt(t).startswith("add() subtracts instead of adding. Steps:")
    assert swe.issue_text(t).startswith("add() subtracts") and "\n" in swe.issue_text(t)
    assert swe.files(t, Path("x")) == {}, "nothing is added to the repository: no hint, no visible check"
    assert swe.CHECK == ""
    long = dict(upstream["recs"][0], problem_statement="word " * 2000)
    (swe.DATA / "mini.json").write_text(json.dumps([long, upstream["recs"][1]]), encoding="utf-8")
    assert len(swe.prompt(t)) == swe.PROMPT_CHARS


def test_missing_data_names_the_command_that_fetches_it(upstream, monkeypatch):
    monkeypatch.setattr(swe, "DATA", swe.DATA / "empty")
    with pytest.raises(SystemExit, match="swe_series_task.py fetch mini"):
        swe.issue_text(swe.series("mini")[0])


# ---------------------------------------------------------------- small helpers

def test_helpers():
    assert swe.patch_files("diff --git a/x/y.py b/x/y.py\n--- a/x/y.py\n+++ b/x/y.py\ndiff --git a/t.py b/t.py\n") == ["x/y.py", "t.py"]
    out = "PASSED tests/a.py::test_a[1-x y]\nFAILED tests/a.py::test_b - boom\nPASSED tests/a.py::test_c\nSKIPPED [1] tests/a.py:3: why\n"
    assert swe.parse_passed(out) == {"tests/a.py::test_a[1-x y]", "tests/a.py::test_c"}
    assert swe.test_args({"install_config": {"test_cmd": "pytest --no-header --tb=line"}}) == ["--no-header", "--tb=line", "-rA"]
    assert swe.test_args({}) and "-rA" in swe.test_args({})
    assert swe.clean_requirements("a==1\n\n# c\n-e git+https://x@y#egg=z\nb @ file:///x\nc==2\n--index-url u\n") == ["a==1", "c==2"]


# ---------------------------------------------------------------- the workspace goes to each issue's base commit

def test_prepare_task_puts_the_files_at_the_base_commit_and_keeps_what_belongs_to_the_arm(upstream, tmp_path):
    ws = ws_dir(tmp_path)
    git(ws, "init", "-q")
    (ws / ".kell").mkdir()
    (ws / ".kell" / "library.md").write_text("GOAL|learned", encoding="utf-8")
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (ws / "README.md").write_text("runner's own file", encoding="utf-8")
    (ws / "leftover.py").write_text("from an earlier task", encoding="utf-8")
    t1, t2 = swe.series("mini")
    swe.prepare_task(t1, ws)
    assert (ws / "mini.py").read_text(encoding="utf-8") == BUG and (ws / "tests" / "test_mini.py").read_text(encoding="utf-8") == T1
    assert not (ws / "leftover.py").exists() and not (ws / "README.md").exists()
    assert (ws / ".kell" / "library.md").exists() and (ws / ".claude" / "settings.json").exists() and (ws / ".git").is_dir()
    (ws / "mini.py").write_text("an earlier task's accepted fix", encoding="utf-8")
    swe.prepare_task(t2, ws)
    assert (ws / "mini.py").read_text(encoding="utf-8") == FIXED, "the previous task's work is NOT carried over: the next base commit"
    assert (ws / ".kell" / "library.md").read_text(encoding="utf-8") == "GOAL|learned"
    assert ("editable", "swe-mini") in upstream["toolchain"].installs
    # the hidden tests and the gold patch are nowhere in the workspace
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts)
    assert "test_mul" not in text and "a * b" not in text


# ---------------------------------------------------------------- grading

def prepared(upstream, tmp_path, i=0, arm="A"):
    ws = ws_dir(tmp_path, arm)
    git(ws, "init", "-q")
    task = swe.series("mini")[i]
    swe.prepare_task(task, ws)
    return task, ws


def test_no_change_is_not_solved_and_the_gold_fix_is_(upstream, tmp_path):
    task, ws = prepared(upstream, tmp_path)
    before = swe.grade_workspace(task, ws)
    assert before["resolved"] is False and before["f2p"] == "0/1" and "FAIL_TO_PASS still failing" in before["error"]
    (ws / "mini.py").write_text(FIXED, encoding="utf-8")
    after = swe.grade_workspace(task, ws)
    assert after["resolved"] is True and after["f2p"] == "1/1" and after["p2p"] == "1/1" and after["error"] is None
    assert (ws / "mini.py").read_text(encoding="utf-8") == FIXED and not (ws / "tests" / "test_add.py").exists(), \
        "grading works on a copy: the workspace is untouched"


def test_an_agent_that_edits_the_tests_instead_of_the_code_gains_nothing(upstream, tmp_path):
    task, ws = prepared(upstream, tmp_path)
    (ws / "tests" / "test_mini.py").write_text(T2.replace("== 5", "== -1"), encoding="utf-8")        # a test bent to the bug
    assert swe.grade_workspace(task, ws)["resolved"] is False, "the PR's own test file replaces it"
    (ws / "tests" / "test_mini.py").write_text(T1 + "\n\ndef test_add():\n    assert True\n", encoding="utf-8")
    assert swe.grade_workspace(task, ws)["resolved"] is False, "a test_add the agent wrote is replaced by the real one"


def test_breaking_a_pass_to_pass_test_is_caught(upstream, tmp_path):
    task, ws = prepared(upstream, tmp_path)
    (ws / "mini.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b\n", encoding="utf-8")
    g = swe.grade_workspace(task, ws)
    assert g["resolved"] is False and g["check_pass"] is True and g["p2p"] == "0/1" and "PASS_TO_PASS broken" in g["error"]


def test_grading_ignores_the_arms_own_directories(upstream, tmp_path):
    task, ws = prepared(upstream, tmp_path)
    (ws / ".kell").mkdir()
    (ws / ".kell" / "conftest.py").write_text("raise SystemExit('must not be collected')", encoding="utf-8")
    (ws / "mini.py").write_text(FIXED, encoding="utf-8")
    assert swe.grade_workspace(task, ws)["resolved"] is True


def test_a_test_patch_that_does_not_apply_is_infrastructure_not_a_score(upstream, tmp_path):
    task, ws = prepared(upstream, tmp_path)
    bad = dict(upstream["recs"][0], test_patch=upstream["recs"][0]["test_patch"].replace("def test_sub", "def test_other"))
    (swe.DATA / "mini.json").write_text(json.dumps([bad, upstream["recs"][1]]), encoding="utf-8")
    g = swe.grade_workspace(task, ws)
    assert g["resolved"] is False and g["infra"] is True and "does not apply" in g["error"]


def test_the_gold_check_accepts_both_issues(upstream, capsys):
    res = swe.validate("mini")
    assert [r["usable"] for r in res] == [True, True]
    assert all(r["base_fails_f2p"] and r["gold_resolves"] for r in res)
    assert "base F2P fails   gold resolves" in capsys.readouterr().out
    only = swe.validate("mini", ["x__mini-2"])
    assert [r["instance_id"] for r in only] == ["x__mini-2"]


def test_the_gold_check_refuses_an_issue_whose_gold_patch_does_not_fix_it(upstream, capsys):
    bad = dict(upstream["recs"][0], patch=upstream["recs"][0]["patch"].replace("a + b", "a * b"))
    (swe.DATA / "mini.json").write_text(json.dumps([bad, upstream["recs"][1]]), encoding="utf-8")
    res = swe.validate("mini")
    assert res[0]["usable"] is False and res[0]["gold_resolves"] is False and res[1]["usable"] is True


# ---------------------------------------------------------------- the runner, end to end with a stand-in claude

SECRETS = {"token": "t", "gc": "k", "zai": "z"}


@pytest.fixture()
def sandbox(upstream, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOCAL", tmp_path / ".local")
    monkeypatch.setattr(runner, "install_c", lambda ws: (ws / ".claude").mkdir(exist_ok=True) or (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8"))
    monkeypatch.setattr(runner, "setup_survey", lambda arm, ws, env: None)      # the real survey is tested in test_series10_arms_offline.py
    seen = []

    def fake_claude(ws, prompt, env, extra, log_path, **kw):
        arm = ws.parent.name
        seen.append({"arm": arm, "prompt": prompt, "path": env["PATH"].split(";" if ";" in env["PATH"] else ":")[0],
                     "stealth": sorted(p.name for p in (ws / ".kell").iterdir()) if (ws / ".kell").exists() else None,
                     "mini": (ws / "mini.py").read_text(encoding="utf-8")})
        if "subtracts" in prompt:
            (ws / "mini.py").write_text(FIXED, encoding="utf-8")
        else:
            (ws / "mini.py").write_text(WITH_MUL, encoding="utf-8")
        if arm in ("C", "C0"):
            (ws / ".kell" / f"learned-{len(seen)}.md").write_text("x", encoding="utf-8")
        log_path.write_text("", encoding="utf-8")
        return {"status": "done", "is_error": False, "claude_cost_usd": 0.1, "turns": 2, "duration_ms": 10, "models": [],
                "model_usage": {}, "final": "ok"}

    monkeypatch.setattr(runner, "run_claude", fake_claude)
    return seen


def test_the_runner_runs_a_series_on_real_commits(sandbox, upstream):
    seen = sandbox
    recs = []
    for task in runner.load_series("swe_series_task:mini"):
        for arm in ("A", "C", "C0"):
            recs.append(runner.run_one(task, arm, "swe", SECRETS))
    assert all(r["status"] == "done" and r["resolved"] for r in recs), [(r["task_id"], r["arm"], r["grade"]) for r in recs if not r["resolved"]]
    assert [r["position"] for r in recs] == [1, 1, 1, 2, 2, 2]
    # each task starts from ITS base commit, whatever the arm left behind before
    by_arm = {a: [s for s in seen if s["arm"] == a] for a in ("A", "C", "C0")}
    for arm, s in by_arm.items():
        assert s[0]["mini"] == BUG and s[1]["mini"] == FIXED, arm
    # .kell/ persists for C, is wiped for C0, does not exist for A
    assert by_arm["A"][1]["stealth"] is None
    assert by_arm["C"][1]["stealth"] == [".keep", "learned-" + str(seen.index(by_arm["C"][0]) + 1) + ".md"]
    assert by_arm["C0"][1]["stealth"] == [".keep"]
    # the agent's `python` is the arm's venv, first on PATH
    assert all(s["path"] == str(Path(sys.executable).parent) for s in seen)
    assert "subtracts" in by_arm["A"][0]["prompt"] and by_arm["A"][0]["prompt"].endswith(runner.RULE)


def test_a_dry_run_lists_the_real_series_and_peeking_at_the_hidden_tests_is_flagged(upstream, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["runner.py", "--run", "d", "--series", "swe_series_task:mini", "--arms", "A,C,C0", "--dry-run"])
    assert runner.main() == 0
    out = capsys.readouterr().out
    assert "2 task(s), arms A,C,C0" in out and "x__mini-1" in out and "workspace swe-mini" in out
    log = tmp_path / "t.jsonl"
    for tool_input in ({"command": "cat /x/experiments/abtest/.local/swe/tasks/mini.json"},
                       {"file_path": "C:\\x\\experiments\\abtest\\.local\\swe\\tasks\\mini.json"},
                       {"command": "python experiments/abtest/swe_series_task.py"}):
        log.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "input": tool_input}]}}) + "\n", encoding="utf-8")
        assert runner.peeked(log), tool_input
    log.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "pytest"}}]}}) + "\n", encoding="utf-8")
    assert runner.peeked(log) == []


def test_candidates_cli_prints_the_window_ids_on_request(capsys, monkeypatch):
    c = {"repo": "a/b", "python": "3.9", "n_with_py": 12, "span_days": 7, "first": "2024-01-01", "last": "2024-01-08", "p2p_median": 5,
         "license": "MIT License", "ids": ["a__b-1", "a__b-2"], "reuse": 0.4, "tightest_reuse": 0.2}
    monkeypatch.setattr(swe, "candidates", lambda *a, **k: [c])
    assert swe.main(["candidates"]) == 0
    out = capsys.readouterr().out
    assert "a/b" in out and "reuse 0.40 (tightest window: 0.20)" in out, "each candidate prints its overlap"
    assert swe.main(["candidates", "--ids"]) == 0
    assert '["a__b-1", "a__b-2"]' in capsys.readouterr().out
    assert swe.main([]) == 2 and "candidates [--ids]" in capsys.readouterr().out


def test_the_shipped_series_are_ten_ordered_issues_of_one_repository_each(monkeypatch):
    import importlib
    real = importlib.reload(swe)
    try:
        assert set(real.SERIES) >= {"jsonargparse", "sqlglot"}
        for name, spec in real.SERIES.items():
            ids = spec["issues"]
            assert len(ids) == 10 and len(set(ids)) == 10 and spec["python"] == "3.9"
            assert all(i.startswith(spec["repo"].replace("/", "__") + "-") for i in ids), name
            tasks = real.series(name)
            assert [t["task_id"] for t in tasks] == ids and {t["workspace"] for t in tasks} == {f"swe-{name}"}
    finally:
        importlib.reload(swe)


def test_an_issue_whose_project_cannot_be_installed_is_unusable_and_the_others_still_run(upstream, capsys):
    tc = upstream["toolchain"]
    real = tc.install_editable

    def picky(self, py, project):
        if (project / "mini.py").read_text(encoding="utf-8") == BUG:           # issue 1's base commit
            raise RuntimeError("the editable install failed: error: Dependency `jsonnet @ https://x/zipball/master` has no archive extension")
        return real(self, py, project)

    tc.install_editable = picky
    res = swe.validate("mini")
    assert [r["usable"] for r in res] == [False, True]
    assert "environment:" in res[0]["gold"]["error"] and "zipball/master" in res[0]["gold"]["error"] and res[0]["gold"]["infra"] is True


def test_a_pass_to_pass_test_broken_by_the_environment_is_excluded_for_that_issue_only(upstream, tmp_path):
    broken = "tests/test_mini.py::test_never_passes"            # fails here with and without the real fix (a dependency version unlike the dataset's)
    r1 = dict(upstream["recs"][0], PASS_TO_PASS=upstream["recs"][0]["PASS_TO_PASS"] + [broken])
    (swe.DATA / "mini.json").write_text(json.dumps([r1, upstream["recs"][1]]), encoding="utf-8")
    assert swe.main(["validate", "mini"]) == 0
    rows = json.loads((swe.LOCAL / "validate_mini.json").read_text(encoding="utf-8"))
    assert [r["usable"] for r in rows] == [True, True], "without the exclusion the first issue would be refused"
    assert rows[0]["p2p_exclude"] == [broken] and rows[1]["p2p_exclude"] == [] and rows[0]["gold"]["p2p_excluded"] == 1
    assert "_passed" not in rows[0]["base"] and "_passed" not in rows[0]["gold"]
    task, ws = prepared(upstream, tmp_path)
    assert swe.excluded_p2p(task) == [broken]
    (ws / "mini.py").write_text(FIXED, encoding="utf-8")
    g = swe.grade_workspace(task, ws)
    assert g["resolved"] is True and g["p2p_excluded"] == 1 and g["p2p"] == "1/1" and "_passed" not in g
    (ws / "mini.py").write_text(FIXED.replace("def sub(a, b):\n    return a - b", "def sub(a, b):\n    return a + b"), encoding="utf-8")
    assert swe.grade_workspace(task, ws)["resolved"] is False, "a P2P test that passes at the base is still enforced"
    t2, ws2 = prepared(upstream, tmp_path, i=1, arm="C")
    assert swe.excluded_p2p(t2) == [], "the exclusion belongs to the issue it was found for"


# ---------------------------------------------------------------- the reuse opportunity (series-10 window choice)

def _patch(*files):
    return chr(10).join(f"diff --git a/{f} b/{f}" for f in files) + chr(10)


def test_the_reuse_opportunity_counts_later_issues_that_touch_an_earlier_issues_file():
    r = swe.reuse_opportunity([_patch("a.py"), _patch("b.py"), _patch("a.py", "c.py"), _patch("c.py"), _patch("z.py")])
    assert r["touching"] == 2 and r["share"] == 0.5 and r["issues"] == 5 and r["files_per_issue"] == [1, 1, 2, 1, 1]
    assert swe.reuse_opportunity([_patch("a.py")])["share"] == 0.0 and swe.reuse_opportunity([])["share"] == 0.0


def test_the_window_with_the_most_reuse_wins_among_the_tight_ones_and_slack_zero_is_the_old_rule():
    spans, overlaps = [10, 25, 90, 12], [0.1, 0.5, 0.9, 0.5]
    assert swe.pick_window(spans, overlaps, slack=30) == 3, "window 2 is too loose; 1 and 3 tie on reuse and 3 is tighter"
    assert swe.pick_window([10, 12], [0.4, 0.4], slack=30) == 0, "ties go to the tighter span"
    assert swe.pick_window(spans, overlaps, slack=0) == 0, "slack 0: the tightest window only"
