"""
Offline (no real DB, no real network) thin-wrapper tests for the new
`find_ways` MCP tool -- the unified "search Goal + Procedure, compile a
DAG, run it" entry point. Composes `resolve_intent` + `resolve_goal` +
`flatten_goal_tree` + `execute_goal_tree`/`compiled_goal_to_run_md`
verbatim; these tests only prove the wrapper layer (outcome routing,
JSON shape, param threading), not those functions' own logic (already
covered by their own offline test files).
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv
from app.execution.goal_execution import GoalExecutionResult, GoalNodeExecutionResult, StepAttempt
from app.execution.goal_resolution import GoalResolutionError, ResolvedGoalNode
from app.execution.intent_resolution import GoalCandidate, IntentResolution, NormalizedIntent


def _run(coro):
    return asyncio.run(coro)


class FakeRequestContext:
    def __init__(self, pool=None):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


def _fake_tree(goal_id="G-1"):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name="do the thing", depth=0, chosen="step",
        step={"order": 0, "procedure_id": "P-1", "binding": {"kind": "command", "command": "make"}},
        rationale="chosen binding",
    )


def _resolved_intent(goal_id="G-1"):
    return IntentResolution(
        raw_input="do the thing", outcome="resolved",
        normalized=NormalizedIntent(raw_input="do the thing", outcome="do the thing", used_fallback=True),
        selected_goal={"id": goal_id, "canonical_name": "do the thing"},
    )


def _ambiguous_intent():
    return IntentResolution(
        raw_input="do a thing", outcome="ambiguous",
        normalized=NormalizedIntent(raw_input="do a thing", outcome="do a thing", used_fallback=True),
        candidates=[
            GoalCandidate(goal={"id": "G-1", "canonical_name": "do thing one"}, score=0.7,
                          lexical_overlap=0.5, scope_match=1.0, status_score=1.0,
                          fusion_position_score=1.0, rationale="close"),
            GoalCandidate(goal={"id": "G-2", "canonical_name": "do thing two"}, score=0.68,
                          lexical_overlap=0.5, scope_match=1.0, status_score=1.0,
                          fusion_position_score=0.9, rationale="also close"),
        ],
    )


def _no_match_intent():
    return IntentResolution(
        raw_input="something nobody has", outcome="no_match",
        normalized=NormalizedIntent(raw_input="something nobody has", outcome="something nobody has",
                                     used_fallback=True),
        proposed_goal={"canonical_name": "something nobody has", "scope": "global"},
    )


def _patch_resolve_intent(monkeypatch, result):
    async def fake(pool, query, *, context=None, client=None, embedder=None, scope=None,
                    tenant_scope=None, status=None, top_k=5):
        return result
    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake)


# ---------------------------------------------------------------------
# malformed input / resolution error
# ---------------------------------------------------------------------


def test_find_ways_rejects_malformed_json():
    ctx = FakeContext()
    result = _run(srv.find_ways(query="do the thing", ctx=ctx, current_scope_json="{not json",
                                 use_llm=False, semantic=False))
    assert result.startswith("REFUSED:")


def test_find_ways_translates_resolution_error_to_refused(monkeypatch):
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        raise GoalResolutionError(f"root goal_id {goal_id!r} does not exist or is not live")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    ctx = FakeContext()
    result = _run(srv.find_ways(query="do the thing", ctx=ctx, use_llm=False, semantic=False))
    assert result.startswith("REFUSED:")


# ---------------------------------------------------------------------
# the three honest search outcomes
# ---------------------------------------------------------------------


def test_find_ways_ambiguous_outcome_returns_candidates_and_stops(monkeypatch):
    _patch_resolve_intent(monkeypatch, _ambiguous_intent())
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="do a thing", ctx=ctx, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "ambiguous"
    assert len(result["candidates"]) == 2
    assert result["candidates"][0]["goal"]["id"] == "G-1"


def test_find_ways_no_match_outcome_returns_proposed_goal_and_stops(monkeypatch):
    _patch_resolve_intent(monkeypatch, _no_match_intent())
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="something nobody has", ctx=ctx, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "no_match"
    assert result["proposed_goal"]["canonical_name"] == "something nobody has"


def test_find_ways_resolved_outcome_proceeds_to_compile_and_execute(monkeypatch):
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(
            outcome="success",
            node_results={
                "G-1": GoalNodeExecutionResult(
                    goal_id="G-1", goal_name="do the thing", status="success",
                    used_binding_kind="command",
                    attempts=[StepAttempt(step_order=0, binding_kind="command", status="success", notes="ok")],
                ),
            },
        )

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="do the thing", ctx=ctx, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "resolved"
    assert result["goal_id"] == "G-1"
    assert result["executed"] is True
    assert result["execution_outcome"] == "success"
    assert result["node_results"]["G-1"]["status"] == "success"


# ---------------------------------------------------------------------
# execute=False -- plan-only, same honesty as compile_goal
# ---------------------------------------------------------------------


def test_find_ways_execute_false_compiles_only_never_runs(monkeypatch):
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    called = {"execute_tree": False}

    async def fake_execute_tree(*a, **k):
        called["execute_tree"] = True
        raise AssertionError("execute_goal_tree must not be called when execute=False")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="do the thing", ctx=ctx, execute=False, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["executed"] is False
    assert result["nodes"][0]["kind"] == "step"
    assert called["execute_tree"] is False


def test_find_ways_execute_false_with_workspace_root_writes_a_planned_goal_run_md(monkeypatch, tmp_path):
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    ctx = FakeContext()
    ws = str(tmp_path)
    _run(srv.find_ways(query="do the thing", ctx=ctx, execute=False, workspace_root=ws,
                        use_llm=False, semantic=False))
    goal_run_path = tmp_path / ".stealth" / "goal_run.md"
    assert goal_run_path.exists()
    assert "GOAL_RUN|-|planned" in goal_run_path.read_text()


# ---------------------------------------------------------------------
# param threading
# ---------------------------------------------------------------------


def test_find_ways_threads_workspace_root_into_execute_goal_tree(monkeypatch):
    _patch_resolve_intent(monkeypatch, _resolved_intent())
    captured = {}

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        captured["workspace_root"] = workspace_root
        return GoalExecutionResult(outcome="success", execution_id="E-1")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="do the thing", ctx=ctx, workspace_root="/tmp/ws",
                              use_llm=False, semantic=False))
    result = json.loads(raw)
    assert captured["workspace_root"] == "/tmp/ws"
    assert result["execution_id"] == "E-1"


def test_find_ways_semantic_true_constructs_and_threads_a_real_embedder(monkeypatch):
    captured = {}

    async def fake_resolve_intent(pool, query, *, context=None, client=None, embedder=None, scope=None,
                                   tenant_scope=None, status=None, top_k=5):
        captured["intent_embedder"] = embedder
        return _resolved_intent()

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        captured["goal_embedder"] = embedder
        return _fake_tree(goal_id)

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(outcome="success")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    _run(srv.find_ways(query="do the thing", ctx=ctx, semantic=True, use_llm=False))
    assert captured["intent_embedder"] is not None
    assert captured["goal_embedder"] is not None


def test_find_ways_is_classified_read_not_exec_in_the_tool_scope_table():
    """Plan-only calls (execute=False) must be free, same tier as
    compile_goal -- the blanket per-tool table must NOT gate this tool
    at all; execute=True is gated dynamically, in-function, instead."""
    assert srv._TOOL_SCOPES["find_ways"] == srv._acx.RETRIEVAL_READ


def test_find_ways_execute_true_denied_for_a_read_only_token(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    read_only = AccessToken(token="anon", client_id="anon", scopes=["stealthlab:tools", srv._acx.RETRIEVAL_READ])
    monkeypatch.setattr(srv, "get_access_token", lambda: read_only)
    _patch_resolve_intent(monkeypatch, _resolved_intent())
    ctx = FakeContext()
    try:
        _run(srv.find_ways(query="do the thing", ctx=ctx, execute=True, use_llm=False, semantic=False))
        assert False, "expected PermissionError"
    except PermissionError as exc:
        assert "find_ways" in str(exc) and "execute=True" in str(exc)


def test_find_ways_execute_false_allowed_for_a_read_only_token(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    read_only = AccessToken(token="anon", client_id="anon", scopes=["stealthlab:tools", srv._acx.RETRIEVAL_READ])
    monkeypatch.setattr(srv, "get_access_token", lambda: read_only)
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="do the thing", ctx=ctx, execute=False, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["executed"] is False  # no PermissionError raised


def test_find_ways_execute_true_allowed_for_a_token_with_exec_scope(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    full_token = AccessToken(token="real", client_id="real",
                              scopes=["stealthlab:tools", srv._acx.RETRIEVAL_READ, srv._acx.EXECUTION_RUN])
    monkeypatch.setattr(srv, "get_access_token", lambda: full_token)
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    async def fake_execute_tree(pool, tree, context, *, scope, workspace_root=None, execution_id=None):
        return GoalExecutionResult(outcome="success")

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.setattr("app.execution.goal_execution.execute_goal_tree", fake_execute_tree)
    ctx = FakeContext()
    raw = _run(srv.find_ways(query="do the thing", ctx=ctx, execute=True, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["executed"] is True


def test_find_ways_use_llm_false_never_constructs_a_client(monkeypatch):
    captured = {}

    async def fake_resolve_intent(pool, query, *, context=None, client=None, embedder=None, scope=None,
                                   tenant_scope=None, status=None, top_k=5):
        captured["client"] = client
        return _no_match_intent()

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    ctx = FakeContext()
    _run(srv.find_ways(query="do the thing", ctx=ctx, use_llm=False, semantic=False))
    assert captured["client"] is None
