"""
Only actions._events() is tested offline -- it's a pure function over two
GameState snapshots. Every other function in actions.py needs a real
PokemonEnv/PyBoy and is covered by test_emulator_actions_e2e.py instead.
"""
from __future__ import annotations

from pokemon_stealth.actions import _events
from pokemon_stealth.state import BattleState, GameState, PartyMon


def _state(**overrides) -> GameState:
    base = dict(map_id=0, position=(1, 1), party=[], badges=[], money=0, frame_count=0)
    base.update(overrides)
    return GameState(**base)


def test_events_detects_map_change():
    before = _state(map_id=0)
    after = _state(map_id=1)
    assert "entered_map:1" in _events(before, after)


def test_events_detects_badge_gain():
    before = _state(badges=[])
    after = _state(badges=["boulder"])
    assert "badge_obtained:boulder" in _events(before, after)


def test_events_detects_battle_start_and_end():
    before = _state(battle=None)
    after = _state(battle=BattleState(opponent_species_id=1, opponent_hp=10, opponent_max_hp=10))
    assert "battle_started" in _events(before, after)
    assert "battle_ended" in _events(after, before)


def test_events_detects_party_fainted_and_damage():
    mon_before = PartyMon(species_id=1, level=5, hp=10, max_hp=10)
    mon_after_damaged = PartyMon(species_id=1, level=5, hp=4, max_hp=10)
    mon_after_fainted = PartyMon(species_id=1, level=5, hp=0, max_hp=10)

    events_damage = _events(_state(party=[mon_before]), _state(party=[mon_after_damaged]))
    assert "party_slot_0_took_damage:6" in events_damage

    events_faint = _events(_state(party=[mon_before]), _state(party=[mon_after_fainted]))
    assert "party_slot_0_fainted" in events_faint


def test_events_detects_opponent_damage():
    before = _state(battle=BattleState(opponent_species_id=1, opponent_hp=20, opponent_max_hp=20))
    after = _state(battle=BattleState(opponent_species_id=1, opponent_hp=12, opponent_max_hp=20))
    assert "opponent_took_damage:8" in _events(before, after)


def test_events_empty_when_nothing_changed():
    state = _state()
    assert _events(state, state) == []
