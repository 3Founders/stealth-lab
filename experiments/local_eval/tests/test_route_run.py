"""Offline tests for the routing arms (route_run.py): no agent, no test runner, no network -- fakes for both.
The planner test runs the product's real recommender on the bundled prior (backend/app/routing)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))

import route_run as rr  # noqa: E402

GOOD = {"status": "resolved", "resolved": True, "f2p": [2, 2], "p2p": [10, 10]}
REGRESSION_ONLY = {"status": "unresolved", "resolved": False, "f2p": [0, 2], "p2p": [10, 10]}   # check passes, wrong
BROKEN = {"status": "unresolved", "resolved": False, "f2p": [0, 2], "p2p": [7, 10]}
TASK = {"instance_id": "o__r-1", "repo": "o/r", "created_at": "2025-01-01"}


def test_check_kinds():
    assert rr.check_passed(GOOD, "oracle") and not rr.check_passed(REGRESSION_ONLY, "oracle")
    assert rr.check_passed(REGRESSION_ONLY, "regression") and not rr.check_passed(BROKEN, "regression")
    assert not rr.check_passed(REGRESSION_ONLY, "partial") and rr.check_passed(GOOD, "partial")
    assert not rr.check_passed({"status": "patch_failed", "p2p": [1, 1]}, "regression")
    assert not rr.check_passed({"status": "empty_patch"}, "regression")
    with pytest.raises(ValueError):
        rr.check_passed(GOOD, "vibes")


def _exec(costs):
    calls = []

    def execute(model, task):
        calls.append(model)
        return {"patch": f"diff by {model}", "tokens_in": 100, "tokens_out": 10, "cost_usd": costs[model], "seconds": 1}
    return execute, calls


def test_fixed_ladder_escalates_on_a_failed_check_and_pays_for_every_rung():
    execute, calls = _exec({"cheap": 0.1, "strong": 1.0})
    grades = {"diff by cheap": BROKEN, "diff by strong": GOOD}
    rec = rr.run_task(TASK, "NAIVE", {"policy": "fixed", "ladder": ["cheap", "strong"]}, execute=execute,
                      grade=lambda t, p: grades[p], check_kind="regression", scaffold="kel", max_rungs=3,
                      memory=rr.RepoMemory())
    assert calls == ["cheap", "strong"]
    assert rec["delivered_rung"] == 1 and rec["accepted"] and rec["resolved"] and not rec["wrong_accepted"]
    assert rec["cost_usd"] == pytest.approx(1.1)


def test_a_weak_check_can_accept_a_wrong_answer_and_it_is_counted():
    execute, calls = _exec({"cheap": 0.1, "strong": 1.0})
    rec = rr.run_task(TASK, "NAIVE", {"policy": "fixed", "ladder": ["cheap", "strong"]}, execute=execute,
                      grade=lambda t, p: REGRESSION_ONLY, check_kind="regression", scaffold="kel", max_rungs=3,
                      memory=rr.RepoMemory())
    assert calls == ["cheap"] and rec["accepted"] and not rec["resolved"] and rec["wrong_accepted"]


def test_ladder_runs_out_then_the_last_attempt_is_delivered_unaccepted():
    execute, _ = _exec({"cheap": 0.1, "strong": 1.0})
    rec = rr.run_task(TASK, "NAIVE", {"policy": "fixed", "ladder": ["cheap", "strong"]}, execute=execute,
                      grade=lambda t, p: BROKEN, check_kind="regression", scaffold="kel", max_rungs=3,
                      memory=rr.RepoMemory())
    assert rec["delivered_rung"] == 1 and not rec["accepted"] and not rec["resolved"]


def test_an_infrastructure_error_is_not_recorded_as_an_outcome():
    execute, _ = _exec({"cheap": 0.1})
    with pytest.raises(rr.Infra):
        rr.run_task(TASK, "CHEAP", {"policy": "fixed", "ladder": ["cheap"]}, execute=execute,
                    grade=lambda t, p: {"status": "error", "detail": "modal down"}, check_kind="regression",
                    scaffold="kel", max_rungs=3, memory=rr.RepoMemory())


def test_repo_memory_orders_by_created_at_and_feeds_later_tasks():
    mem = rr.RepoMemory()
    seen = []

    class FakePlanner:
        def plan(self, repo, attempts, local_obs, features=None):
            seen.append(list(local_obs))
            return {"ladder": ["cheap|kel"], "meets_target": True, "p_ok": {}, "excluded": []}

    execute, _ = _exec({"cheap": 0.1})
    tasks = [dict(TASK, instance_id="o__r-2", created_at="2025-02-01"), dict(TASK, instance_id="o__r-1")]
    out = rr.run_repo(tasks, arm="R80", arm_cfg={"policy": "route"}, execute=execute,
                      grade=lambda t, p: BROKEN, check_kind="regression", scaffold="kel", max_rungs=2,
                      memory=mem, planner=FakePlanner())
    assert [r["instance_id"] for r in out] == ["o__r-1", "o__r-2"]          # earlier task first
    assert seen[0] == [] and seen[2] == [{"unit": "cheap|kel", "n": 2, "ok": 0}]


def test_summary():
    rows = [{"resolved": True, "accepted": True, "wrong_accepted": False, "cost_usd": 1.0,
             "attempts": [{"model": "a"}]},
            {"resolved": False, "accepted": True, "wrong_accepted": True, "cost_usd": 3.0,
             "attempts": [{"model": "b"}, {"model": "c"}]}]
    s = rr.summarize(rows)
    assert s["resolved"] == 0.5 and s["wrong_accepted"] == 0.5 and s["mean_cost_usd"] == 2.0
    assert s["mean_attempts"] == 1.5 and s["first_rung_models"] == {"a": 1, "b": 1}


def test_the_real_planner_routes_and_escalates_on_the_bundled_prior():
    from app.routing import prior_bundle

    if not prior_bundle.available():
        pytest.skip("no bundled prior")
    p = rr.Planner(["gpt-oss-120b|kel", "claude-sonnet-4-5|kel"], reliability_target=0.7)
    first = p.plan("o/r", [], [])
    assert first["ladder"] and set(first["p_ok"]) == {"gpt-oss-120b|kel", "claude-sonnet-4-5|kel"}
    head = first["ladder"][0]
    after = p.plan("o/r", [{"unit": head, "accepted": False, "check_kind": "tests"}],
                   [{"unit": head, "n": 6, "ok": 0}])
    assert after["p_ok"][head] < first["p_ok"][head]                      # a failure here lowers that model
