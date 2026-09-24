from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone

import app.services.procedures as procedures

PROCEDURE_ROW_ID = "00000000-0000-0000-0000-000000000001"
PROCEDURE_STABLE_ID = "00000000-0000-0000-0000-000000000002"


def _run(coro):
    return asyncio.run(coro)


class _LifecyclePool:
    def __init__(
        self, *, successes=9, direct_goal_id="g-direct", solution_goal_ids=(),
        goals=None, state="candidate", context_keys_seen=None, solution_statuses=None,
    ):
        self.procedure = {
            "id": PROCEDURE_ROW_ID,
            "procedure_id": PROCEDURE_STABLE_ID,
            "version": 1,
            "achieves_goal_id": direct_goal_id,
            "verification_state": state,
            "availability": "active",
            "verification_stats": {
                "attempts": successes,
                "successes": successes,
                "context_keys_seen": list(
                    context_keys_seen
                    if context_keys_seen is not None
                    else ["ctx-0", "ctx-1", "ctx-2"][:successes]
                ),
                "distinct_contexts": len(
                    context_keys_seen
                    if context_keys_seen is not None
                    else ["ctx-0", "ctx-1", "ctx-2"][:successes]
                ),
                "consecutive_failures": 0,
            },
        }
        self.goals = goals or {}
        self.solution_goal_ids = list(solution_goal_ids)
        self.solution_statuses = list(solution_statuses) if solution_statuses is not None else None
        self.events = []
        self.solution_args = ()
        self.in_transaction = False

    async def fetchrow(self, sql, *args):
        normalized = " ".join(sql.split())
        if "SELECT * FROM procedures" in normalized:
            self.events.append("select_procedure")
            return deepcopy(self.procedure)
        if "INSERT INTO evidence" in normalized:
            self.events.append("insert_evidence")
            return {
                "id": args[0],
                "target_type": args[2],
                "target_id": args[3],
                "target_version": args[4],
                "outcome_status": args[10],
                "failure_class": args[12],
                "context_key": args[9],
                "independence_group": args[8],
            }
        if "UPDATE procedures" in normalized:
            self.events.append("update_procedure")
            self.procedure["verification_stats"] = deepcopy(args[1])
            self.procedure["verification_state"] = args[2]
            self.procedure["availability"] = args[3]
            return deepcopy(self.procedure)
        raise AssertionError(f"unexpected fetchrow: {normalized}")

    async def fetch(self, sql, *args):
        normalized = " ".join(sql.split())
        if "FROM solutions" in normalized:
            self.events.append("read_solutions")
            self.solution_args = args
            if self.solution_statuses is None:
                return [{"goal_id": goal_id} for goal_id in self.solution_goal_ids]
            rows = [
                (goal_id, status)
                for goal_id, status in zip(self.solution_goal_ids, self.solution_statuses)
            ]
            if "status = 'active'" not in normalized:
                return [{"goal_id": goal_id} for goal_id, _status in rows]
            return [{"goal_id": goal_id} for goal_id, status in rows if status == "active"]
        if "UPDATE goals" in normalized:
            self.events.append("update_goal_in_transaction")
            updated = []
            for goal_id in args[0]:
                goal = self.goals.get(goal_id)
                if goal is None or goal.get("t_invalid") is not None or goal.get("status") == "merged":
                    continue
                if goal["resolved_at"] is None:
                    goal["resolved_at"] = datetime(2026, 9, 24, tzinfo=timezone.utc)
                updated.append({"id": goal_id})
            return updated
        raise AssertionError(f"unexpected fetch: {normalized}")

    async def execute(self, sql, *args):
        self.events.append("update_goal_post_transaction")
        goal = self.goals.get(str(args[0]))
        if goal is not None and goal["resolved_at"] is None:
            goal["resolved_at"] = datetime(2026, 9, 24, 1, tzinfo=timezone.utc)
        return "UPDATE 1"


class _ControlPool:
    def __init__(self, solution_goal_ids):
        self.solution_goal_ids = list(solution_goal_ids)
        self.events = []

    async def fetch(self, sql, *args):
        self.events.append("read_solutions")
        return [{"goal_id": goal_id} for goal_id in self.solution_goal_ids]


def _install(monkeypatch, pool, *, control=None, goal_pool=None):
    control_pool = control or pool

    @asynccontextmanager
    async def fake_transaction(transaction_pool, scope):
        transaction_pool.in_transaction = True
        transaction_pool.events.append("begin")
        try:
            yield transaction_pool
        finally:
            transaction_pool.in_transaction = False
            transaction_pool.events.append("commit")

    async def fake_home_pool(candidate, object_type, object_id, *, by_row_id=False):
        if object_type == "procedure":
            return pool
        return goal_pool or pool

    async def fake_classify(conn, evidence):
        return None

    monkeypatch.setattr(procedures, "tenant_transaction", fake_transaction)
    monkeypatch.setattr("app.services.shards.home_pool", fake_home_pool)
    monkeypatch.setattr(procedures, "classify_and_route", fake_classify)
    return control_pool


def test_below_threshold_does_not_touch_linked_goals(monkeypatch):
    goals = {"g-direct": {"resolved_at": None, "t_invalid": None, "status": "active"}}
    pool = _LifecyclePool(successes=8, goals=goals, solution_goal_ids=["g-solution"])
    _install(monkeypatch, pool)

    result = _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=True, context_key="ctx-3",
    ))

    assert result["verification_state"] == "candidate"
    assert goals["g-direct"]["resolved_at"] is None
    assert "read_solutions" not in pool.events
    assert "update_goal_in_transaction" not in pool.events


def test_blank_context_does_not_satisfy_the_distinct_context_bar(monkeypatch):
    for blank_context in (None, "   "):
        pool = _LifecyclePool(
            successes=9,
            context_keys_seen=["ctx-0", "ctx-1"],
        )
        _install(monkeypatch, pool)

        result = _run(procedures.record_execution_outcome(
            pool, procedure_row_id=PROCEDURE_ROW_ID,
            success=True, context_key=blank_context,
        ))

        assert result["verification_state"] == "candidate"
        assert result["verification_stats"]["successes"] == 10
        assert result["verification_stats"]["distinct_contexts"] == 2
        assert result["verification_stats"]["context_keys_seen"] == ["ctx-0", "ctx-1"]


def test_only_active_solutions_resolve_goals_but_direct_goal_still_does(monkeypatch):
    goals = {
        "g-direct": {"resolved_at": None, "t_invalid": None, "status": "active"},
        "g-proposed": {"resolved_at": None, "t_invalid": None, "status": "active"},
        "g-rejected": {"resolved_at": None, "t_invalid": None, "status": "active"},
        "g-withdrawn": {"resolved_at": None, "t_invalid": None, "status": "active"},
    }
    pool = _LifecyclePool(
        successes=9,
        goals=goals,
        solution_goal_ids=["g-proposed", "g-rejected", "g-withdrawn"],
        solution_statuses=["proposed", "rejected", "withdrawn"],
    )
    _install(monkeypatch, pool)

    result = _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=True, context_key="ctx-3",
    ))

    assert result["verification_state"] == "verified"
    assert goals["g-direct"]["resolved_at"] is not None
    assert goals["g-proposed"]["resolved_at"] is None
    assert goals["g-rejected"]["resolved_at"] is None
    assert goals["g-withdrawn"]["resolved_at"] is None


def test_exact_promotion_resolves_direct_and_solution_goals_in_the_procedure_transaction(monkeypatch):
    goals = {
        "g-direct": {"resolved_at": None, "t_invalid": None, "status": "active"},
        "g-solution-a": {"resolved_at": None, "t_invalid": None, "status": "active"},
        "g-solution-b": {"resolved_at": None, "t_invalid": None, "status": "active"},
    }
    pool = _LifecyclePool(
        successes=9,
        goals=goals,
        solution_goal_ids=["g-solution-a", "g-solution-b", "g-direct"],
    )
    _install(monkeypatch, pool)

    result = _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=True, context_key="ctx-3",
    ))

    assert result["verification_state"] == "verified"
    assert all(goal["resolved_at"] is not None for goal in goals.values())
    assert pool.solution_args == (PROCEDURE_STABLE_ID,)
    assert pool.events.index("update_procedure") < pool.events.index("update_goal_in_transaction")
    assert pool.events.index("update_goal_in_transaction") < pool.events.index("commit")
    assert pool.events.count("update_goal_in_transaction") == 1


def test_resolved_timestamp_is_preserved_across_later_success_failure_and_duplicate_calls(monkeypatch):
    original = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    goals = {"g-direct": {"resolved_at": original, "t_invalid": None, "status": "active"}}
    pool = _LifecyclePool(successes=9, goals=goals)
    _install(monkeypatch, pool)

    _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=True, context_key="ctx-3",
    ))
    _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=True, context_key="ctx-4",
    ))
    _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=False,
        context_key="ctx-5", failure_class="external_failure",
    ))

    assert goals["g-direct"]["resolved_at"] == original
    assert pool.events.count("update_goal_in_transaction") == 1
    assert pool.events.count("read_solutions") == 1


def test_self_reported_successes_never_resolve_goals(monkeypatch):
    goals = {"g-direct": {"resolved_at": None, "t_invalid": None, "status": "active"}}
    pool = _LifecyclePool(successes=20, goals=goals, solution_goal_ids=["g-solution"])
    _install(monkeypatch, pool)

    result = _run(procedures.record_execution_outcome(
        pool, procedure_row_id=PROCEDURE_ROW_ID, success=True,
        context_key="ctx-20", execution_verified=False,
    ))

    assert result["verification_state"] == "candidate"
    assert goals["g-direct"]["resolved_at"] is None
    assert "read_solutions" not in pool.events
    assert "update_goal_in_transaction" not in pool.events


def test_cross_shard_goal_resolution_is_routed_after_the_procedure_transaction(monkeypatch):
    procedure_pool = _LifecyclePool(
        successes=9, direct_goal_id="g-remote", goals={}, solution_goal_ids=["g-remote-solution"],
    )
    control_pool = _ControlPool(["g-remote-solution"])
    goal_pool = _LifecyclePool(goals={
        "g-remote": {"resolved_at": None, "t_invalid": None, "status": "active"},
        "g-remote-solution": {"resolved_at": None, "t_invalid": None, "status": "active"},
    })
    _install(monkeypatch, procedure_pool, control=control_pool, goal_pool=goal_pool)

    result = _run(procedures.record_execution_outcome(
        control_pool, procedure_row_id=PROCEDURE_ROW_ID, success=True, context_key="ctx-3",
    ))

    assert result["verification_state"] == "verified"
    assert procedure_pool.events.index("update_procedure") < procedure_pool.events.index("update_goal_in_transaction")
    assert procedure_pool.events.index("update_goal_in_transaction") < procedure_pool.events.index("commit")
    assert "update_goal_post_transaction" not in procedure_pool.events
    assert goal_pool.events == ["update_goal_post_transaction", "update_goal_post_transaction"]
    assert all(
        goal["resolved_at"] is not None for goal in goal_pool.goals.values()
    )
