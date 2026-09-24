"""
Offline (no real DB, no real network) wrapper tests for `find_ways` -- the v1
"search Goal + Procedure, return the knowledge" entry point. Composes
`resolve_intent` + `resolve_goal` + `goal_tree_to_knowledge`; these prove the
wrapper (outcome routing, JSON shape, param threading) and that it never
compiles, executes, or writes files -- the planner agent does that
(final_architecture.md).
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv
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
    step = {"order": 0, "goal": "run make", "binding": {"kind": "command", "command": "make"},
            "expected_outcome": "build/ exists"}
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name="do the thing", depth=0, chosen="procedure",
        procedure={"id": "V-1", "procedure_id": "P-1", "name": "make it", "version": 2, "steps": [step]},
        children=[ResolvedGoalNode(goal_id=f"{goal_id}#s0", goal_name="run make", depth=1, chosen="step",
                                   step={**step, "procedure_id": "P-1"})],
        rationale="selected procedure 'make it'",
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
    result = _run(srv.find_ways(query="do the thing", ctx=FakeContext(), use_llm=False, semantic=False))
    assert result.startswith("REFUSED:")


# ---------------------------------------------------------------------
# the three honest search outcomes
# ---------------------------------------------------------------------


def test_find_ways_ambiguous_outcome_returns_candidates_and_stops(monkeypatch):
    _patch_resolve_intent(monkeypatch, _ambiguous_intent())
    result = json.loads(_run(srv.find_ways(query="do a thing", ctx=FakeContext(), use_llm=False, semantic=False)))
    assert result["outcome"] == "ambiguous"
    assert len(result["candidates"]) == 2
    assert result["candidates"][0]["goal"]["id"] == "G-1"


def test_find_ways_no_match_outcome_returns_proposed_goal_and_stops(monkeypatch):
    _patch_resolve_intent(monkeypatch, _no_match_intent())
    raw = _run(srv.find_ways(query="something nobody has", ctx=FakeContext(), use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "no_match"
    assert result["proposed_goal"]["canonical_name"] == "something nobody has"


def test_find_ways_resolved_returns_knowledge_not_a_compiled_plan(monkeypatch):
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    result = json.loads(_run(srv.find_ways(query="do the thing", ctx=FakeContext(), use_llm=False, semantic=False)))
    assert result["outcome"] == "resolved"
    assert result["goal"] == {"goal_id": "G-1", "name": "do the thing", "check": None}
    [proc] = result["procedures"]
    assert (proc["procedure_id"], proc["version_id"], proc["name"]) == ("P-1", "V-1", "make it")
    [step] = proc["steps"]
    assert step["kind"] == "action" and step["do"] == "run make" and step["check"] == "build/ exists"
    # the planner compiles: no node ids, no execution result, no run file
    for gone in ("nodes", "executed", "execution_outcome", "node_results", "tree"):
        assert gone not in result
    assert "plan_and_run" in result["next"]


def test_find_ways_no_longer_takes_execute_or_workspace_root():
    import inspect

    params = inspect.signature(srv.find_ways).parameters
    assert "execute" not in params and "workspace_root" not in params
    assert "repo_claims" in params


def test_find_ways_writes_nothing_to_disk(monkeypatch, tmp_path):
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.chdir(tmp_path)
    _run(srv.find_ways(query="do the thing", ctx=FakeContext(), use_llm=False, semantic=False))
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------
# param threading / access
# ---------------------------------------------------------------------


def test_find_ways_semantic_true_constructs_and_threads_a_real_embedder(monkeypatch):
    captured = {}

    async def fake_resolve_intent(pool, query, *, context=None, client=None, embedder=None, scope=None,
                                   tenant_scope=None, status=None, top_k=5):
        captured["intent_embedder"] = embedder
        return _resolved_intent()

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        captured["goal_embedder"] = embedder
        return _fake_tree(goal_id)

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    _run(srv.find_ways(query="do the thing", ctx=FakeContext(), semantic=True, use_llm=False))
    assert captured["intent_embedder"] is not None
    assert captured["goal_embedder"] is not None


def test_find_ways_is_classified_read_in_the_tool_scope_table():
    assert srv._TOOL_SCOPES["find_ways"] == srv._acx.RETRIEVAL_READ


def test_find_ways_allowed_for_a_read_only_token(monkeypatch):
    from mcp.server.auth.provider import AccessToken

    read_only = AccessToken(token="anon", client_id="anon", scopes=["stealthlab:tools", srv._acx.RETRIEVAL_READ])
    monkeypatch.setattr(srv, "get_access_token", lambda: read_only)
    _patch_resolve_intent(monkeypatch, _resolved_intent())

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        return _fake_tree(goal_id)

    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    result = json.loads(_run(srv.find_ways(query="do the thing", ctx=FakeContext(), use_llm=False, semantic=False)))
    assert result["outcome"] == "resolved"


def test_find_ways_use_llm_false_never_constructs_a_client(monkeypatch):
    captured = {}

    async def fake_resolve_intent(pool, query, *, context=None, client=None, embedder=None, scope=None,
                                   tenant_scope=None, status=None, top_k=5):
        captured["client"] = client
        return _no_match_intent()

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    _run(srv.find_ways(query="do the thing", ctx=FakeContext(), use_llm=False, semantic=False))
    assert captured["client"] is None


def test_server_side_compile_and_execute_tools_are_gone():
    for name in ("compile_goal", "execute_goal", "estimate_goal_cost", "get_goal_run_status",
                 "list_goal_artifacts", "get_goal_artifact"):
        assert not hasattr(srv, name), name
        assert name not in srv._TOOL_SCOPES, name
