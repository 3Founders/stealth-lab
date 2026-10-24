"""The five arms of the series-10 test (A, C0, C-R, C-L, C): what each keeps between issues, the reset of the router's state, the one
survey at series start, and the blind mode. Offline: stand-ins for the shell, no Modal, no Postgres."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modal_series as ms          # noqa: E402
import runner                      # noqa: E402
from test_modal_series_offline import ENV_SUB, Proc, Recorder, fake_series, spec  # noqa: E402,F401


def populate(ws: Path) -> None:
    d = ws / ".stealth"
    (d / "survey").mkdir(parents=True)
    (d / "library" / "solutions").mkdir(parents=True)
    (d / "index").mkdir()
    for name in ("claims.md", "library.md", "SUMMARY.md", "evidence.md", "routing.md", "areas.md", "dag_state.json",
                 "meta.json", "workspace.md", ".keep", ".auto-survey", ".series_survey.json", "survey/worklist.md",
                 "library/solutions/L-1.diff", "index/library.idx"):
        (d / name).write_text("x", encoding="utf-8")


def left(ws: Path) -> set[str]:
    d = ws / ".stealth"
    return {str(p.relative_to(d)).replace(chr(92), "/") for p in d.rglob("*") if p.is_file()}


FACTS = {"claims.md", "survey/worklist.md"}
LIBRARY = {"library.md", "SUMMARY.md", "evidence.md", "library/solutions/L-1.diff", "index/library.idx"}
ROUTING = {"routing.md", "areas.md", "dag_state.json"}
BOOKKEEPING = {"meta.json", "workspace.md", ".keep", ".auto-survey", ".series_survey.json"}


@pytest.mark.parametrize("arm,kept", [
    ("C0", set()),
    ("C-R", FACTS),
    ("C-L", FACTS | LIBRARY),
    ("C", FACTS | LIBRARY | ROUTING),
])
def test_each_arm_keeps_exactly_what_the_table_says(tmp_path, arm, kept):
    populate(tmp_path)
    runner.wipe_stealth(tmp_path, arm)
    assert left(tmp_path) == kept | BOOKKEEPING, arm


def test_c0_and_c_r_start_every_issue_with_an_empty_library():
    for arm in ("C0", "C-R"):
        assert runner.ARM_STATE[arm]["library"] is False and runner.ARM_STATE[arm]["routing"] is False
    assert runner.ARM_STATE["C0"]["facts"] is False, "C0 has no survey either"
    assert runner.ARM_STATE["C-L"]["routing"] is False and runner.ARM_STATE["C-L"]["library"] is True
    assert all(runner.ARM_STATE["C"][k] for k in ("facts", "library", "routing")) and runner.ARM_STATE["C"]["server"] == "kept"


def test_the_arms_are_one_list_everywhere():
    assert runner.ARMS == ms.ARMS == ("A", "C0", "C-R", "C-L", "C")
    assert tuple(runner.ARM_STATE) == ms.STEALTH_ARMS
    assert {a for a in runner.ARM_STATE if runner.server_resets(a)} == set(ms.SERVER_RESET_ARMS)
    assert not runner.is_c("A") and all(runner.is_c(a) for a in ms.STEALTH_ARMS)


def test_every_stealthlab_arm_has_the_background_survey_off_and_arm_a_has_nothing_of_it(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "LOCAL", tmp_path)
    monkeypatch.setattr(runner, "c_home", lambda: tmp_path / "home")
    for arm in ms.STEALTH_ARMS:
        env = runner.arm_env(arm, {"token": "t", "gc": "g", "zai": "z"})
        assert env["STEALTHLAB_AUTO_SURVEY"] == "off", arm
    assert "STEALTHLAB_AUTO_SURVEY" not in runner.arm_env("A", {})


def test_the_survey_runs_once_in_the_arms_that_keep_facts_and_never_in_c0(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        (tmp_path / ".stealth").mkdir(exist_ok=True)
        (tmp_path / ".stealth" / "claims.md").write_text("CLAIM|a|b|c|d|e|f" + chr(10) + "CLAIM|g|h|i|j|k|l" + chr(10), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.setup_survey("C0", tmp_path, {}) is None and runner.setup_survey("A", tmp_path, {}) is None
    assert calls == []
    first = runner.setup_survey("C-R", tmp_path, {})
    assert first["facts"] == 2 and first["seconds"] >= 0 and first["uses_a_model"] is False and first["cost_usd"] == 0.0
    assert "survey" in calls[0] and "--fill" not in calls[0], "the survey is local and uses no model"
    assert runner.setup_survey("C-R", tmp_path, {}) is None and len(calls) == 1, "a resume does not run it twice"
    runner.wipe_stealth(tmp_path, "C-R")
    assert (tmp_path / ".stealth" / ".series_survey.json").exists(), "the record of the survey survives the wipe"


def test_the_runner_refuses_series_only_arms_without_a_series(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runner.py", "--run", "r", "--tasks", "ds1000:1", "--arms", "A,C-R"])
    with pytest.raises(SystemExit, match="only with --series"):
        runner.main()


# ---------------------------------------------------------------- the container: the router's state

def make(tmp_path, arm, **kw):
    holder = {}
    rec = Recorder(lambda: holder["c"])
    c = ms.Container(spec(arms=[arm], unit_arm=arm, **kw), "mini", sh=rec, vol=tmp_path / "vol", app=tmp_path / "app",
                     environ=ENV_SUB, commit=lambda: None, sleep=lambda s: None, health=lambda: True)
    holder["c"] = c
    c.rec = rec
    return c


def resets(c):
    return [x for x in c.rec.calls if "reset.sql" in x["cmd"]]


@pytest.mark.parametrize("arm,expect", [("C0", True), ("C-R", True), ("C-L", True), ("C", False), ("A", False)])
def test_the_router_is_reset_before_each_issue_for_c0_c_r_and_c_l_only(tmp_path, fake_series, arm, expect):
    c = make(tmp_path, arm, max_issues=2)
    c.run()
    n = len(resets(c))
    assert n == (2 if expect else 0), f"{arm}: {n} resets for 2 issues"
    if expect:
        cmds = [x["cmd"] for x in c.rec.calls]
        first_reset = next(i for i, t in enumerate(cmds) if "reset.sql" in t)
        first_run = next(i for i, t in enumerate(cmds) if "runner.py" in t)
        baseline = next(i for i, t in enumerate(cmds) if "--data-only" in t)
        assert baseline < first_reset < first_run, "the baseline is taken right after setup, the reset comes before the issue"
        assert "db_baseline.sql.gz" in resets(c)[0]["cmd"]
        sql = (c.out / "reset.sql").read_text(encoding="utf-8")
        assert "TRUNCATE TABLE" in sql and "schema_migrations" in sql, "every table but the migrations ledger"


def test_the_reset_covers_the_routers_tables_and_says_so(tmp_path, fake_series):
    c = make(tmp_path, "C-R", max_issues=1)
    c.run()
    scope = (c.out / "reset_scope.txt").read_text(encoding="utf-8").splitlines()
    assert all(t in scope for t in ms.ROUTER_TABLES) and "schema_migrations" not in scope[1:]
    assert "router tables included" in scope[0]


def test_a_database_without_the_routers_tables_stops_before_any_session(tmp_path, fake_series):
    c = make(tmp_path, "C0", max_issues=1)
    orig = c.rec.__call__

    def no_router(argv, **kw):
        text = argv if isinstance(argv, str) else " ".join(map(str, argv))
        if "pg_tables" in text and "-At" in text:
            return Proc(0, out="goals")
        return orig(argv, **kw)

    c.sh = no_router
    with pytest.raises(RuntimeError, match="router's tables"):
        c.run()
    assert not [x for x in c.rec.calls if "runner.py" in x["cmd"]]


def test_a_resume_keeps_the_first_baseline(tmp_path, fake_series):
    c = make(tmp_path, "C-L", max_issues=1)
    c.out.mkdir(parents=True)
    (c.out / "db_baseline.sql.gz").write_bytes(b"first")
    c.run()
    assert not [x for x in c.rec.calls if "--data-only" in x["cmd"]], "every issue of the run starts from the same state"


def test_c_and_the_resetting_arms_cannot_share_one_backend():
    assert any("cannot share one backend" in p for p in ms.validate_spec(spec(arms=["C", "C0"], split_arms=False)))
    assert ms.validate_spec(spec(arms=["C", "C0"], split_arms=True)) == []


# ---------------------------------------------------------------- the plan and the blind mode

def test_the_plan_shows_each_arms_persistence(fake_series):
    text = ms.plan_text(spec(arms=list(ms.ARMS)), {"mini": ms.series_tasks(spec(), "mini")})
    for arm in ms.ARMS:
        assert f"arm {arm}:" in text
    assert "router state RESET" in text and "all KEPT (it learns)" in text


def test_a_blind_run_computes_and_prints_nothing_that_compares_arms(tmp_path, fake_series):
    c = make(tmp_path, "C", max_issues=1, blind=True)
    out = c.run()
    assert "report" not in out and not [x for x in c.rec.calls if "report.py" in x["cmd"]]
    assert not (c.out / "report.txt").exists()
    assert c.environ["ABTEST_BLIND"] == "1"
    text = ms.plan_text(spec(blind=True, arms=list(ms.ARMS)), {"mini": []})
    assert "BLIND" in text


def test_a_blind_finish_merges_raw_results_on_the_volume_and_makes_no_report(tmp_path, fake_series):
    c = ms.Container(spec(blind=True, arms=["A", "C"]), "mini", sh=Recorder(lambda: c), vol=tmp_path / "vol", app=tmp_path / "app",
                     environ=ENV_SUB, commit=lambda: None, sleep=lambda s: None, health=lambda: True)
    for arm in ("A", "C"):
        d = c.run_dir / arm / "results"
        d.mkdir(parents=True)
        (d / "long-mini-1t.jsonl").write_text(json.dumps({"task_id": "t", "arm": arm, "status": "done", "resolved": arm == "C"}) + chr(10), encoding="utf-8")
    out = c.finish()
    assert out == {"repo": "mini", "run": "long-mini-1t", "rows": 2, "arms": ["A", "C"], "arms_without_results": [], "blind": True}
    assert not (c.run_dir / "report.txt").exists()
