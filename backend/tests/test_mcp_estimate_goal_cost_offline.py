"""
Offline (no real DB, no real network) thin-wrapper tests for the
estimate_goal_cost MCP tool (execu.md Sec 13/27). Do NOT re-test
resolve_goal/estimate_goal_cost's own logic (covered by
test_goal_resolution_offline.py / test_goal_cost_offline.py); these test
the wrapper layer only: JSON parsing/validation, GoalResolutionError ->
REFUSED translation, and correct pass-through of the underlying
CostEstimate.
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv
from app.execution.goal_resolution import GoalResolutionError, ResolvedGoalNode


def _run(coro):
    return asyncio.run(coro)


class FakeRequestContext:
    def __init__(self, pool=None):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


def _fake_tree():
    return ResolvedGoalNode(
        goal_id="G-1", goal_name="do the thing", depth=0, chosen="step",
        step={"order": 0, "procedure_id": "P-1", "binding": {"kind": "command", "command": "make"}},
        rationale="chosen impl",
    )


def test_estimate_goal_cost_rejects_malformed_json():
    ctx = FakeContext()
    result = _run(srv.estimate_goal_cost(goal_id="G-1", ctx=ctx, current_scope_json="{not json"))
    assert result.startswith("REFUSED:")


def test_estimate_goal_cost_translates_resolution_error_to_refused(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        raise GoalResolutionError(f"root goal_id {goal_id!r} does not exist or is not live")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    result = _run(srv.estimate_goal_cost(goal_id="missing", ctx=ctx))
    assert result.startswith("REFUSED:")
    assert "missing" in result


def test_estimate_goal_cost_returns_tree_and_honest_zero_data_cost(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree()

    async def fake_stats(pool, procedure_id, step_order):
        from app.execution.execution_telemetry import StepExecutionStats
        return StepExecutionStats(
            procedure_id=procedure_id, step_order=step_order, sample_count=0, success_count=0,
            success_rate=None, mean_wall_seconds=None, mean_prompt_tokens=None,
            mean_completion_tokens=None,
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_cost.step_execution_stats", fake_stats)
    ctx = FakeContext()
    raw = _run(srv.estimate_goal_cost(goal_id="G-1", ctx=ctx))
    result = json.loads(raw)
    assert result["tree"]["chosen"] == "step"
    cost = result["cost"]
    assert cost["confidence"] == "none"
    assert cost["sample_count"] == 0
    assert cost["monetary_cost_usd"] is None
    assert "no recorded executions" in cost["basis"]


def test_estimate_goal_cost_passes_through_real_empirical_aggregation(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree()

    async def fake_stats(pool, procedure_id, step_order):
        from app.execution.execution_telemetry import StepExecutionStats
        return StepExecutionStats(
            procedure_id=procedure_id, step_order=step_order, sample_count=5, success_count=5,
            success_rate=1.0, mean_wall_seconds=2.0, mean_prompt_tokens=None,
            mean_completion_tokens=None,
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_cost.step_execution_stats", fake_stats)
    ctx = FakeContext()
    raw = _run(srv.estimate_goal_cost(goal_id="G-1", ctx=ctx, current_scope_json='{"repo": ["r"]}', max_depth=3))
    result = json.loads(raw)
    cost = result["cost"]
    assert cost["confidence"] == "empirical"
    assert cost["sample_count"] == 5
    assert cost["success_rate"] == 1.0
    assert cost["expected_attempts"] == 1.0
    assert cost["expected_wall_seconds"] == 2.0
