"""
Bounded missions (STEP 9). Success/failure come ONLY from real GameState
(STEP 17) -- never from the LLM claiming it's done. `checkpoint` names the
savestate a run starts from; create it with `pokemon-stealth checkpoint
create <name>` before running the mission (STEP 10).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from pokemon_stealth.state import GameState

# Map ids per state.py's MAP_NAMES table.
PALLET_TOWN = 0
VIRIDIAN_CITY = 1
PEWTER_CITY = 2
PEWTER_GYM = 54


@dataclass(frozen=True)
class Mission:
    id: str
    description: str
    checkpoint: str  # name of the savestate to load before this mission runs
    success_condition: Callable[[GameState], bool]
    failure_condition: Callable[[GameState], Optional[str]]  # returns a reason, or None
    max_actions: int
    max_llm_calls: int
    max_wall_time_s: float


def _party_wiped(state: GameState) -> bool:
    return bool(state.party) and all(m.hp <= 0 for m in state.party)


MISSION_STARTER = Mission(
    id="starter",
    description="Pallet Town: obtain starter Pokemon (progression checkpoint: party_count >= 1).",
    checkpoint="S0_PALLET",
    success_condition=lambda s: len(s.party) >= 1,
    failure_condition=lambda s: "party_wiped" if _party_wiped(s) else None,
    max_actions=200,
    max_llm_calls=40,
    max_wall_time_s=600,
)

MISSION_VIRIDIAN = Mission(
    id="reach_viridian",
    description="Route 1: reach Viridian City from Pallet Town.",
    checkpoint="S0_PALLET",
    success_condition=lambda s: s.map_id == VIRIDIAN_CITY,
    failure_condition=lambda s: "party_wiped" if _party_wiped(s) else None,
    max_actions=400,
    max_llm_calls=80,
    max_wall_time_s=900,
)

MISSION_PEWTER = Mission(
    id="reach_pewter",
    description="Viridian -> Route 2 -> reach Pewter City.",
    checkpoint="S1_VIRIDIAN",
    success_condition=lambda s: s.map_id == PEWTER_CITY,
    failure_condition=lambda s: "party_wiped" if _party_wiped(s) else None,
    max_actions=600,
    max_llm_calls=120,
    max_wall_time_s=1200,
)

MISSION_BROCK = Mission(
    id="brock",
    description="Defeat Brock at Pewter Gym and obtain the Boulder Badge.",
    checkpoint="S3_BEFORE_BROCK",
    success_condition=lambda s: "boulder" in s.badges,
    failure_condition=(
        lambda s: "party_wiped" if _party_wiped(s) else None
    ),
    max_actions=300,
    max_llm_calls=60,
    max_wall_time_s=900,
)

MISSIONS: dict[str, Mission] = {
    m.id: m for m in [MISSION_STARTER, MISSION_VIRIDIAN, MISSION_PEWTER, MISSION_BROCK]
}


def get_mission(mission_id: str) -> Mission:
    if mission_id not in MISSIONS:
        raise KeyError(f"unknown mission '{mission_id}', known: {sorted(MISSIONS)}")
    return MISSIONS[mission_id]
