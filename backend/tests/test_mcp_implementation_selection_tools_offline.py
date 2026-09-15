"""
Offline (no real DB, no real network) thin-wrapper tests for the two new
meta-harness Sec 32 MCP tools: list_implementations_for_goal and
explain_implementation_selection. Same philosophy as
test_mcp_six_tool_surface_offline.py -- these do NOT re-test
list_implementations_by_goal/select_implementation_for_goal's own
decision logic (covered by test_implementation_selection_offline.py and
test_implementation_selection_e2e.py); they test the wrapper layer:
JSON parsing/validation, status-sentinel handling, and correct
pass-through of the underlying function's result.
"""
from __future__ import annotations

import json

import app.mcp_server.server as srv
from app.execution.implementation_selection import RankedImplementation, RequirementCheck, ScoreComponent, SelectionResult


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


# ---------------------------------------------------------------------
# list_implementations_for_goal
# ---------------------------------------------------------------------


def test_list_implementations_for_goal_rejects_unknown_status():
    import asyncio
    ctx = FakeContext()
    result = asyncio.run(srv.list_implementations_for_goal(goal="code_generation", ctx=ctx, status="bogus"))
    assert result.startswith("REFUSED:")
    assert "unknown status" in result


def test_list_implementations_for_goal_passes_through_real_rows(monkeypatch):
    import asyncio

    captured = {}

    async def fake_list(pool, goal, *, scope, status="active", limit=50):
        captured["goal"] = goal
        captured["status"] = status
        return [{"id": "impl-1", "name": "regenerate-api", "kind": "deterministic"}]

    monkeypatch.setattr(
        "app.execution.implementation_registry.list_implementations_by_goal", fake_list,
    )
    ctx = FakeContext()
    raw = asyncio.run(srv.list_implementations_for_goal(goal="artifact_regeneration", ctx=ctx))
    result = json.loads(raw)
    assert result == [{"id": "impl-1", "name": "regenerate-api", "kind": "deterministic"}]
    assert captured["goal"] == "artifact_regeneration"
    assert captured["status"] == "active"


def test_list_implementations_for_goal_status_all_means_no_filter(monkeypatch):
    import asyncio

    captured = {}

    async def fake_list(pool, goal, *, scope, status="active", limit=50):
        captured["status"] = status
        return []

    monkeypatch.setattr(
        "app.execution.implementation_registry.list_implementations_by_goal", fake_list,
    )
    ctx = FakeContext()
    asyncio.run(srv.list_implementations_for_goal(goal="verification", ctx=ctx, status="all"))
    assert captured["status"] is None


# ---------------------------------------------------------------------
# explain_implementation_selection
# ---------------------------------------------------------------------


def test_explain_implementation_selection_rejects_malformed_json():
    import asyncio
    ctx = FakeContext()
    result = asyncio.run(srv.explain_implementation_selection(
        goal="code_generation", ctx=ctx, allowed_execution_locations_json="{not json",
    ))
    assert result.startswith("REFUSED:")


def test_explain_implementation_selection_threads_context_and_returns_full_trace(monkeypatch):
    import asyncio

    captured = {}

    async def fake_select(pool, goal, *, context=None, scope, weights=None):
        captured["goal"] = goal
        captured["context"] = context
        return SelectionResult(
            goal=goal,
            candidates_considered=[{"id": "impl-1"}, {"id": "impl-2"}],
            ranked=[
                RankedImplementation(
                    implementation={"id": "impl-1", "name": "local-tool", "kind": "tool"},
                    eligible=True,
                    checks=[RequirementCheck("lifecycle", "SATISFIABLE", "status='active'")],
                    score=1.5,
                    components=[ScoreComponent("verified", 1.0, 1.5)],
                ),
                RankedImplementation(
                    implementation={"id": "impl-2", "name": "third-party", "kind": "api"},
                    eligible=False,
                    checks=[RequirementCheck("privacy", "HARD_FALSE", "third_party forbidden")],
                    score=None,
                    components=[],
                ),
            ],
            chosen={"id": "impl-1", "name": "local-tool", "kind": "tool"},
            rationale="chosen 'local-tool' among 1 eligible / 2 considered",
        )

    monkeypatch.setattr(
        "app.execution.implementation_selection.select_implementation_for_goal", fake_select,
    )
    ctx = FakeContext()
    raw = asyncio.run(srv.explain_implementation_selection(
        goal="code_generation", ctx=ctx, privacy_policy="no_third_party",
        required_scope_type="workspace", allowed_execution_locations_json='["stealth_hosted"]',
    ))
    result = json.loads(raw)

    assert captured["goal"] == "code_generation"
    assert captured["context"] == {
        "privacy_policy": "no_third_party",
        "required_scope_type": "workspace",
        "allowed_execution_locations": ["stealth_hosted"],
    }

    assert result["candidates_considered"] == 2
    assert result["chosen"]["id"] == "impl-1"
    assert "local-tool" in result["rationale"]
    assert len(result["ranked"]) == 2
    winner = result["ranked"][0]
    assert winner["implementation_id"] == "impl-1"
    assert winner["eligible"] is True
    assert winner["score"] == 1.5
    assert winner["components"][0]["name"] == "verified"
    loser = result["ranked"][1]
    assert loser["eligible"] is False
    assert loser["rejection_reasons"] == ["privacy: third_party forbidden"]


def test_explain_implementation_selection_no_context_flags_means_empty_context(monkeypatch):
    import asyncio

    captured = {}

    async def fake_select(pool, goal, *, context=None, scope, weights=None):
        captured["context"] = context
        return SelectionResult(goal=goal, candidates_considered=[], ranked=[], chosen=None, rationale="none")

    monkeypatch.setattr(
        "app.execution.implementation_selection.select_implementation_for_goal", fake_select,
    )
    ctx = FakeContext()
    asyncio.run(srv.explain_implementation_selection(goal="test_execution", ctx=ctx))
    assert captured["context"] == {}
