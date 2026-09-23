"""
Offline (no real DB, no real network) thin-wrapper tests for the two
new meta-harness/execu.md Sec 27 MCP tools: explain_goal_route and
compile_goal. Do NOT re-test resolve_goal/flatten_goal_tree's own logic
(covered by test_goal_resolution_offline.py / test_goal_compiler_offline.py);
these test the wrapper layer: JSON parsing/validation, GoalResolutionError
-> REFUSED translation, and correct pass-through of the underlying
function's result.
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


# ---------------------------------------------------------------------
# explain_goal_route
# ---------------------------------------------------------------------


def test_explain_goal_route_rejects_malformed_json():
    ctx = FakeContext()
    result = _run(srv.explain_goal_route(goal_id="G-1", ctx=ctx, current_scope_json="{not json"))
    assert result.startswith("REFUSED:")


def test_explain_goal_route_translates_resolution_error_to_refused(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        raise GoalResolutionError(f"root goal_id {goal_id!r} does not exist or is not live")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    result = _run(srv.explain_goal_route(goal_id="missing", ctx=ctx))
    assert result.startswith("REFUSED:")
    assert "missing" in result


def test_explain_goal_route_returns_full_tree_and_threads_scope(monkeypatch):
    captured = {}

    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        captured["goal_id"] = goal_id
        captured["context"] = context
        captured["max_depth"] = max_depth
        return _fake_tree()

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    raw = _run(srv.explain_goal_route(
        goal_id="G-1", ctx=ctx, current_scope_json='{"repo": ["r"]}', max_depth=3,
    ))
    result = json.loads(raw)
    assert result["chosen"] == "step"
    assert result["step"]["binding"]["kind"] == "command"
    assert captured["goal_id"] == "G-1"
    assert captured["context"] == {"current_scope": {"repo": ["r"]}}
    assert captured["max_depth"] == 3


# ---------------------------------------------------------------------
# compile_goal
# ---------------------------------------------------------------------


def test_compile_goal_rejects_malformed_json():
    ctx = FakeContext()
    result = _run(srv.compile_goal(goal_id="G-1", ctx=ctx, current_scope_json="[[["))
    assert result.startswith("REFUSED:")


def test_compile_goal_translates_resolution_error_to_refused(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        raise GoalResolutionError("root goal_id not found")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    result = _run(srv.compile_goal(goal_id="missing", ctx=ctx))
    assert result.startswith("REFUSED:")


def test_compile_goal_returns_tree_and_flattened_nodes(monkeypatch):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree()

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    raw = _run(srv.compile_goal(goal_id="G-1", ctx=ctx))
    result = json.loads(raw)
    assert result["tree"]["chosen"] == "step"
    assert len(result["nodes"]) == 1
    node = result["nodes"][0]
    assert node["kind"] == "step"
    assert node["step_order"] == 0
    assert node["executor"] == "deterministic"
    assert node["deps"] == []


def test_compile_goal_with_workspace_root_writes_a_real_planned_goal_run_md(monkeypatch, tmp_path):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree()

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    ws = str(tmp_path)
    _run(srv.compile_goal(goal_id="G-1", ctx=ctx, workspace_root=ws))

    goal_run_path = tmp_path / ".stealth" / "goal_run.md"
    assert goal_run_path.exists()
    content = goal_run_path.read_text()
    assert "GOAL_RUN|-|planned" in content
    assert "GOAL_NODE|G-1|step|planned|do the thing|binding=-" in content


def test_compile_goal_without_workspace_root_writes_nothing(monkeypatch, tmp_path):
    async def fake_resolve(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree()

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve)
    ctx = FakeContext()
    _run(srv.compile_goal(goal_id="G-1", ctx=ctx))
    assert not (tmp_path / ".stealth").exists()
