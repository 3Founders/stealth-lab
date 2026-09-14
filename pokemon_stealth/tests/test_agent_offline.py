from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from pokemon_stealth.agent import (
    AgentObservation,
    Condition,
    NotesStore,
    _build_user_prompt,
    decide,
    parse_decision,
)
from pokemon_stealth.knowledge import Claim
from pokemon_stealth.llm import MockAgent
from pokemon_stealth.state import GameState


def _state(**overrides) -> GameState:
    base = dict(map_id=0, position=(1, 1), party=[], badges=[], money=0, frame_count=0)
    base.update(overrides)
    return GameState(**base)


def test_parse_decision_valid_move():
    text = '{"action": {"type": "move", "direction": "up"}, "reason": "go north", "knowledge_refs": []}'
    decision = parse_decision(text)
    assert decision.action == {"type": "move", "direction": "up"}


def test_parse_decision_extracts_json_from_prose_wrapper():
    text = 'Sure, here is my choice:\n{"action": {"type": "interact"}, "reason": "talk"}\nHope that helps.'
    decision = parse_decision(text)
    assert decision.action["type"] == "interact"


def test_parse_decision_raises_on_no_json():
    with pytest.raises(ValueError):
        parse_decision("I choose to move up.")


def test_parse_decision_raises_on_unknown_action_type():
    with pytest.raises(ValidationError):
        parse_decision('{"action": {"type": "fly_to_moon"}, "reason": "x"}')


def test_parse_decision_raises_on_missing_required_param():
    with pytest.raises(ValidationError):
        parse_decision('{"action": {"type": "move"}, "reason": "x"}')  # missing "direction"


def test_build_user_prompt_stealth_includes_claims_and_procedures():
    obs = AgentObservation(
        game_state=_state(), mission_objective="Beat Brock", action_history_summary=[], recent_failures=[],
        retrieved_claims=[Claim(id="C-1", scope="pokemon-red", status="observed", evidence=[], tags=[], statement="Water beats Rock")],
    )
    prompt = _build_user_prompt(obs, Condition.STEALTH)
    assert "RETRIEVED CLAIMS" in prompt
    assert "Water beats Rock" in prompt


def test_build_user_prompt_fresh_excludes_all_knowledge_sections():
    obs = AgentObservation(
        game_state=_state(), mission_objective="Beat Brock", action_history_summary=[], recent_failures=[],
        retrieved_claims=[Claim(id="C-1", scope="pokemon-red", status="observed", evidence=[], tags=[], statement="Water beats Rock")],
        notes_text="some notes", raw_history_excerpts=["some history"],
    )
    prompt = _build_user_prompt(obs, Condition.FRESH)
    assert "RETRIEVED CLAIMS" not in prompt
    assert "PERSISTENT NOTES" not in prompt
    assert "PAST RUN RECORDS" not in prompt


def test_build_user_prompt_notes_only_shows_notes_not_stealth_data():
    obs = AgentObservation(
        game_state=_state(), mission_objective="Beat Brock", action_history_summary=[], recent_failures=[],
        retrieved_claims=[Claim(id="C-1", scope="pokemon-red", status="observed", evidence=[], tags=[], statement="Water beats Rock")],
        notes_text="run 1: got badge",
    )
    prompt = _build_user_prompt(obs, Condition.NOTES)
    assert "PERSISTENT NOTES" in prompt
    assert "RETRIEVED CLAIMS" not in prompt


def test_decide_returns_none_decision_on_malformed_model_output():
    agent = MockAgent(responses=["not json at all"])
    obs = AgentObservation(game_state=_state(), mission_objective="x", action_history_summary=[], recent_failures=[])
    outcome = asyncio.run(decide(agent, obs, Condition.FRESH))
    assert outcome.decision is None
    assert outcome.parse_error is not None


def test_decide_returns_valid_decision():
    agent = MockAgent(responses=['{"action": {"type": "interact"}, "reason": "talk to npc"}'])
    obs = AgentObservation(game_state=_state(), mission_objective="x", action_history_summary=[], recent_failures=[])
    outcome = asyncio.run(decide(agent, obs, Condition.FRESH))
    assert outcome.decision is not None
    assert outcome.decision.action["type"] == "interact"


def test_notes_store_append_and_read_roundtrip(tmp_path):
    store = NotesStore(tmp_path / "notes")
    store.append("brock", "run 1: failed, low HP")
    store.append("brock", "run 2: success")
    text = store.read("brock")
    assert "run 1: failed, low HP" in text
    assert "run 2: success" in text


def test_notes_store_read_missing_mission_returns_empty(tmp_path):
    store = NotesStore(tmp_path / "notes")
    assert store.read("never_run") == ""
