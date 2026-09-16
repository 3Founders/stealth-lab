"""
Offline (no real DB, no real network) thin-wrapper tests for the
execute_goal MCP tool (Prompt 2 Sec 7/9/10/14). Do NOT re-test
resolve_goal/execute_goal_tree's own logic (covered by
test_goal_resolution_offline.py / test_goal_execution_offline.py);
these test the wrapper layer only: JSON parsing/validation,
GoalResolutionError -> REFUSED translation, and correct pass-through of
the underlying GoalExecutionResult.
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv
from app.execution.goal_execution import (
    GoalExecutionResult, GoalNodeExecutionResult, ImplementationAttempt,
    ProcedureAttempt, ProcedureExecutionResult,
)
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
        goal_id="G-1", goal_name="do the thing", depth=0, chosen="implementation",
        implementation={"id": "I-1", "name": "impl", "kind": "deterministic"},
        rationale="chosen impl",
    )


def test_execute_goal_rejects_malformed_json():
    ctx = FakeContext()
    result = _run(srv.execute_goal(goal_id="G-1", ctx=ctx, current_scope_json="{not json"))
    assert result.startswith("REFUSED:")


def test_execute_goal_translates_resolution_error_to_refused(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        raise GoalResolutionError(f"root goal_id {goal_id!r} does not exist or is not live")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    result = _run(srv.execute_goal(goal_id="missing", ctx=ctx))
    assert result.startswith("REFUSED:")
    assert "missing" in result


def test_execute_goal_returns_success_outcome_and_attempts(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        return _fake_tree()

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(
            outcome="success",
            node_results={
                "G-1": GoalNodeExecutionResult(
                    goal_id="G-1", goal_name="do the thing", status="success",
                    attempts=[ImplementationAttempt(
                        implementation_id="I-1", implementation_name="impl", kind="deterministic",
                        status="success", notes="ok",
                    )],
                    used_implementation_id="I-1",
                ),
            },
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.execute_goal(goal_id="G-1", ctx=ctx))
    result = json.loads(raw)
    assert result["outcome"] == "success"
    assert result["node_results"]["G-1"]["used_implementation_id"] == "I-1"
    assert len(result["node_results"]["G-1"]["attempts"]) == 1
    assert result["unresolved_goal_names"] == []


def test_execute_goal_passes_through_verification_state_and_detail(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        return _fake_tree()

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(
            outcome="success",
            node_results={
                "G-1": GoalNodeExecutionResult(
                    goal_id="G-1", goal_name="do the thing", status="success",
                    attempts=[ImplementationAttempt(
                        implementation_id="I-1", implementation_name="impl", kind="deterministic",
                        status="success", notes="ok", verification_state="checked",
                        verification_detail="all expected output file(s) present",
                    )],
                    used_implementation_id="I-1",
                ),
            },
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.execute_goal(goal_id="G-1", ctx=ctx))
    result = json.loads(raw)
    attempt = result["node_results"]["G-1"]["attempts"][0]
    assert attempt["verification_state"] == "checked"
    assert attempt["verification_detail"] == "all expected output file(s) present"


def test_execute_goal_passes_through_procedure_results_and_human_intervention(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        return _fake_tree()

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(
            outcome="failure",
            procedure_results={
                "G-parent": ProcedureExecutionResult(
                    goal_id="G-parent", goal_name="p", status="failure",
                    attempts=[
                        ProcedureAttempt(procedure_id="P-1", procedure_name="strategy-a", status="failure"),
                        ProcedureAttempt(procedure_id="P-2", procedure_name="strategy-b", status="failure"),
                    ],
                    used_procedure_id=None, human_intervention_needed=True,
                ),
            },
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.execute_goal(goal_id="G-1", ctx=ctx))
    result = json.loads(raw)
    proc_result = result["procedure_results"]["G-parent"]
    assert proc_result["human_intervention_needed"] is True
    assert proc_result["used_procedure_id"] is None
    assert len(proc_result["attempts"]) == 2


def test_execute_goal_returns_needs_input_when_unresolved(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        return _fake_tree()

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(outcome="needs_input", unresolved_goal_names=["fix the flaky thing"])

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.execute_goal(goal_id="G-1", ctx=ctx))
    result = json.loads(raw)
    assert result["outcome"] == "needs_input"
    assert result["unresolved_goal_names"] == ["fix the flaky thing"]


def test_execute_goal_threads_workspace_root_and_execution_id_and_surfaces_result(monkeypatch):
    captured = {}

    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        return _fake_tree()

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        captured["workspace_root"] = workspace_root
        captured["execution_id"] = execution_id
        return GoalExecutionResult(
            outcome="success", execution_id="E-1",
            node_results={
                "G-1": GoalNodeExecutionResult(
                    goal_id="G-1", goal_name="do the thing", status="success",
                    used_implementation_id="I-1", resumed_from_journal=True,
                ),
            },
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.execute_goal(goal_id="G-1", ctx=ctx, workspace_root="/tmp/ws", execution_id="E-1"))
    result = json.loads(raw)
    assert captured["workspace_root"] == "/tmp/ws"
    assert captured["execution_id"] == "E-1"
    assert result["execution_id"] == "E-1"
    assert result["node_results"]["G-1"]["resumed_from_journal"] is True


def test_execute_goal_threads_scope_and_goal_id_into_context(monkeypatch):
    captured = {}

    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6):
        return _fake_tree()

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        captured["context"] = context
        return GoalExecutionResult(outcome="success")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    _run(srv.execute_goal(goal_id="G-1", ctx=ctx, current_scope_json='{"repo": ["r"]}'))
    assert captured["context"]["current_scope"] == {"repo": ["r"]}
    assert captured["context"]["goal_id"] == "G-1"
