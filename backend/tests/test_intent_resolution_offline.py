"""
DB-free coverage for app.execution.intent_resolution -- Prompt 2 Sec
1-3's fuzzy-input -> Goal resolution pipeline. `search_goals` is
monkeypatched per test since its own real lexical+semantic SQL is
already covered by test_goals_offline.py/test_goals_api_offline.py
(peer "ingestion"'s tests); these tests prove normalize_intent's honest
LLM/fallback discipline and resolve_intent's own re-ranking/decision
logic in isolation.
"""
from __future__ import annotations

import asyncio
import json

import app.execution.intent_resolution as ir


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# normalize_intent
# ---------------------------------------------------------------------


def test_normalize_intent_empty_input_is_honest_fallback():
    result = _run(ir.normalize_intent(""))
    assert result.used_fallback is True
    assert result.outcome == ""


def test_normalize_intent_no_client_falls_back_to_raw_text():
    result = _run(ir.normalize_intent("make checkout faster"))
    assert result.used_fallback is True
    assert result.outcome == "make checkout faster"
    assert "no LLM client" in result.rationale


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeClient:
    def __init__(self, content=None, raise_exc=None):
        self._content = content
        self._raise_exc = raise_exc
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return _FakeResponse(self._content)


def test_normalize_intent_with_client_parses_real_structured_response():
    payload = {
        "outcome": "reduce checkout request latency", "object": "checkout service",
        "action": "improve_performance", "constraints": [], "verification": "measure request latency",
        "entities": [], "uncertainty": [], "alternative_interpretations": [],
    }
    client = _FakeClient(content=json.dumps(payload))
    result = _run(ir.normalize_intent("make checkout faster", client=client))
    assert result.used_fallback is False
    assert result.outcome == "reduce checkout request latency"
    assert result.object == "checkout service"
    assert result.action == "improve_performance"


def test_normalize_intent_client_call_failure_is_honest_fallback():
    client = _FakeClient(raise_exc=RuntimeError("transport down"))
    result = _run(ir.normalize_intent("deploy it", client=client))
    assert result.used_fallback is True
    assert result.outcome == "deploy it"
    assert "LLM call failed" in result.rationale


def test_normalize_intent_malformed_response_is_honest_fallback():
    client = _FakeClient(content="not json at all")
    result = _run(ir.normalize_intent("fix the checkout thing", client=client))
    assert result.used_fallback is True
    assert result.outcome == "fix the checkout thing"
    assert "did not parse" in result.rationale


def test_normalize_intent_captures_ambiguity_and_alternatives():
    payload = {
        "outcome": "improve performance", "object": "", "action": "improve_performance",
        "constraints": [], "verification": "", "entities": [],
        "uncertainty": ["which service 'it' refers to"],
        "alternative_interpretations": [
            {"outcome": "improve build performance", "object": "CI pipeline"},
            {"outcome": "improve API performance", "object": "checkout service"},
        ],
    }
    client = _FakeClient(content=json.dumps(payload))
    result = _run(ir.normalize_intent("make it faster", client=client))
    assert result.uncertainty == ["which service 'it' refers to"]
    assert len(result.alternative_interpretations) == 2


# ---------------------------------------------------------------------
# resolve_intent
# ---------------------------------------------------------------------


def _goal(id_, name, *, status="active", scope_type="global", scope_entity_id=None, description=None):
    return {
        "id": id_, "canonical_name": name, "description": description, "status": status,
        "scope_type": scope_type, "scope_entity_id": scope_entity_id,
    }


def test_resolve_intent_empty_input_is_no_match():
    result = _run(ir.resolve_intent(None, ""))
    assert result.outcome == "no_match"


def test_resolve_intent_resolves_a_single_strong_candidate(monkeypatch):
    async def fake_search_goals(pool, **kwargs):
        assert kwargs["query_text"] == "reduce checkout request latency"
        return [_goal("G-1", "reduce checkout request latency")]

    monkeypatch.setattr(ir, "search_goals", fake_search_goals)
    payload = {
        "outcome": "reduce checkout request latency", "object": "checkout service",
        "action": "improve_performance", "constraints": [], "verification": "",
        "entities": [], "uncertainty": [], "alternative_interpretations": [],
    }
    client = _FakeClient(content=json.dumps(payload))
    result = _run(ir.resolve_intent(None, "make checkout faster", client=client))
    assert result.outcome == "resolved"
    assert result.selected_goal["id"] == "G-1"


def test_resolve_intent_no_match_when_nothing_clears_the_floor(monkeypatch):
    async def fake_search_goals(pool, **kwargs):
        return []

    monkeypatch.setattr(ir, "search_goals", fake_search_goals)
    result = _run(ir.resolve_intent(None, "do the thing with the widget"))
    assert result.outcome == "no_match"
    assert result.proposed_goal is not None
    assert result.proposed_goal["canonical_name"]


def test_resolve_intent_is_ambiguous_when_top_candidates_are_close(monkeypatch):
    async def fake_search_goals(pool, **kwargs):
        # Equal lexical overlap; G-2's scope match offsets its worse
        # fusion position enough to land within the ambiguity margin.
        return [
            _goal("G-1", "improve build performance", scope_type="global"),
            _goal("G-2", "improve api performance", scope_type="repo", scope_entity_id="r1"),
        ]

    monkeypatch.setattr(ir, "search_goals", fake_search_goals)
    result = _run(ir.resolve_intent(
        None, "improve performance", context={"scope_type": "repo", "scope_entity_id": "r1"},
    ))
    assert result.outcome == "ambiguous"
    assert result.selected_goal is None
    assert len(result.candidates) == 2


def test_resolve_intent_ranking_prefers_lexical_overlap_and_scope(monkeypatch):
    async def fake_search_goals(pool, **kwargs):
        # search_goals's own fused order puts the WORSE match first --
        # re-ranking must still be able to prefer the better real match.
        return [
            _goal("G-1", "improve build performance", scope_type="global"),
            _goal("G-2", "reduce checkout request latency", scope_type="repo", scope_entity_id="repo-42"),
        ]

    monkeypatch.setattr(ir, "search_goals", fake_search_goals)
    payload = {
        "outcome": "reduce checkout request latency", "object": "checkout service",
        "action": "improve_performance", "constraints": [], "verification": "",
        "entities": [], "uncertainty": [], "alternative_interpretations": [],
    }
    client = _FakeClient(content=json.dumps(payload))
    result = _run(ir.resolve_intent(
        None, "make checkout faster", client=client,
        context={"scope_type": "repo", "scope_entity_id": "repo-42"},
    ))
    assert result.candidates[0].goal["id"] == "G-2"
    assert result.candidates[0].scope_match == 1.0


def test_resolve_intent_threads_embedder_for_semantic_leg(monkeypatch):
    captured = {}

    async def fake_search_goals(pool, **kwargs):
        captured.update(kwargs)
        return [_goal("G-1", "reduce checkout request latency")]

    class _FakeEmbedder:
        async def embed_one_with_metadata(self, text, input_type=None):
            captured["embed_text"] = text
            return [0.1, 0.2, 0.3], {}

    monkeypatch.setattr(ir, "search_goals", fake_search_goals)
    result = _run(ir.resolve_intent(None, "make checkout faster", embedder=_FakeEmbedder()))
    assert captured["query_embedding"] == [0.1, 0.2, 0.3]
    assert result.outcome == "resolved"
