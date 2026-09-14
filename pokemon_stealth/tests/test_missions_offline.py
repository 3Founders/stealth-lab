from __future__ import annotations

from pokemon_stealth.missions import MISSION_BROCK, MISSION_STARTER, MISSION_VIRIDIAN, get_mission
from pokemon_stealth.state import GameState, PartyMon


def _state(**overrides) -> GameState:
    base = dict(map_id=0, position=(1, 1), party=[], badges=[], money=0, frame_count=0)
    base.update(overrides)
    return GameState(**base)


def test_get_mission_unknown_raises():
    import pytest

    with pytest.raises(KeyError):
        get_mission("nonexistent")


def test_starter_success_needs_a_party_member():
    assert MISSION_STARTER.success_condition(_state(party=[])) is False
    mon = PartyMon(species_id=7, level=5, hp=20, max_hp=20)
    assert MISSION_STARTER.success_condition(_state(party=[mon])) is True


def test_viridian_success_is_map_based():
    assert MISSION_VIRIDIAN.success_condition(_state(map_id=0)) is False
    assert MISSION_VIRIDIAN.success_condition(_state(map_id=1)) is True


def test_brock_success_requires_boulder_badge():
    assert MISSION_BROCK.success_condition(_state(badges=[])) is False
    assert MISSION_BROCK.success_condition(_state(badges=["boulder"])) is True


def test_failure_condition_detects_party_wipe():
    fainted = PartyMon(species_id=7, level=5, hp=0, max_hp=20)
    assert MISSION_STARTER.failure_condition(_state(party=[fainted])) == "party_wiped"


def test_failure_condition_none_when_party_alive():
    alive = PartyMon(species_id=7, level=5, hp=10, max_hp=20)
    assert MISSION_STARTER.failure_condition(_state(party=[alive])) is None
