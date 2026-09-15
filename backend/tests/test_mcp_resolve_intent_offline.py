"""
Offline (no real DB, no real network) thin-wrapper tests for the
resolve_intent MCP tool (Prompt 2 Sec 1-3/14). Do NOT re-test
normalize_intent/resolve_intent's own logic (covered by
test_intent_resolution_offline.py); these test the wrapper layer only:
correct pass-through of the underlying IntentResolution, and that a
missing/failing LLM client degrades honestly rather than crashing the
tool.
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv
from app.execution.intent_resolution import GoalCandidate, IntentResolution, NormalizedIntent


def _run(coro):
    return asyncio.run(coro)


class FakeRequestContext:
    def __init__(self, pool=None):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


def _fake_resolution(outcome="resolved"):
    normalized = NormalizedIntent(
        raw_input="make checkout faster", outcome="reduce checkout request latency",
        object="checkout service", action="improve_performance", used_fallback=False,
        rationale="normalized via LLM extraction",
    )
    goal = {"id": "G-1", "canonical_name": "reduce checkout request latency", "status": "active"}
    candidate = GoalCandidate(
        goal=goal, score=0.9, lexical_overlap=1.0, scope_match=0.5, status_score=1.0,
        fusion_position_score=1.0, rationale="lexical_overlap=1.00",
    )
    if outcome == "resolved":
        return IntentResolution(
            raw_input="make checkout faster", normalized=normalized, outcome="resolved",
            selected_goal=goal, candidates=[candidate], rationale="top candidate cleared the floor",
        )
    if outcome == "ambiguous":
        return IntentResolution(
            raw_input="make it faster", normalized=normalized, outcome="ambiguous",
            candidates=[candidate], rationale="too close to call",
        )
    return IntentResolution(
        raw_input="do the widget thing", normalized=normalized, outcome="no_match",
        proposed_goal={"canonical_name": "do the widget thing", "scope_type": "global"},
        rationale="no existing goal cleared the floor",
    )


def test_resolve_intent_returns_resolved_outcome_and_selected_goal(monkeypatch):
    async def fake_resolve_intent(pool, user_input, **kwargs):
        return _fake_resolution("resolved")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    ctx = FakeContext()
    raw = _run(srv.resolve_intent(user_input="make checkout faster", ctx=ctx, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "resolved"
    assert result["selected_goal"]["id"] == "G-1"
    assert result["normalized"]["object"] == "checkout service"
    assert len(result["candidates"]) == 1


def test_resolve_intent_returns_ambiguous_outcome_with_no_selected_goal(monkeypatch):
    async def fake_resolve_intent(pool, user_input, **kwargs):
        return _fake_resolution("ambiguous")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    ctx = FakeContext()
    raw = _run(srv.resolve_intent(user_input="make it faster", ctx=ctx, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "ambiguous"
    assert result["selected_goal"] is None
    assert len(result["candidates"]) == 1


def test_resolve_intent_returns_no_match_with_proposed_goal(monkeypatch):
    async def fake_resolve_intent(pool, user_input, **kwargs):
        return _fake_resolution("no_match")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    ctx = FakeContext()
    raw = _run(srv.resolve_intent(user_input="do the widget thing", ctx=ctx, use_llm=False, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "no_match"
    assert result["proposed_goal"]["canonical_name"] == "do the widget thing"
    assert result["selected_goal"] is None


def test_resolve_intent_degrades_honestly_when_llm_key_is_not_configured(monkeypatch):
    """settings.require('general_compute_api_key') raising (no .env key
    configured) must not crash the tool -- it degrades to client=None,
    exactly like every other honest-fallback path in this codebase."""
    captured = {}

    async def fake_resolve_intent(pool, user_input, **kwargs):
        captured["client"] = kwargs.get("client")
        return _fake_resolution("resolved")

    def fake_require(self, key):
        raise RuntimeError(f"{key} is not configured")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    monkeypatch.setattr(type(srv.settings), "require", fake_require)
    ctx = FakeContext()
    raw = _run(srv.resolve_intent(user_input="make checkout faster", ctx=ctx, use_llm=True, semantic=False))
    result = json.loads(raw)
    assert result["outcome"] == "resolved"
    assert captured["client"] is None


def test_resolve_intent_threads_scope_and_status_into_context(monkeypatch):
    captured = {}

    async def fake_resolve_intent(pool, user_input, **kwargs):
        captured.update(kwargs)
        return _fake_resolution("resolved")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve_intent)
    ctx = FakeContext()
    _run(srv.resolve_intent(
        user_input="make checkout faster", ctx=ctx, scope_type="repo", scope_entity_id="r1",
        status="active", use_llm=False, semantic=False,
    ))
    assert captured["context"] == {"scope_type": "repo", "scope_entity_id": "r1"}
    assert captured["status"] == "active"
