"""
Structured game state. Clear separation of concerns:

- `RawState` -- exact bytes read from RAM this tick, nothing interpreted.
- `GameState` -- the interpreted, LLM-facing view. Any field that could not
  be decoded confidently is `None`, never a fabricated placeholder.

`extract_state(env)` is the single place that turns RawState -> GameState.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from pokemon_stealth import memory_map as mm
from pokemon_stealth.emulator import PokemonEnv

# Known map-id -> name table is intentionally small and honest: only maps
# this harness's missions actually need are named. An unmapped id still
# surfaces (map_id) instead of silently becoming "Unknown".
MAP_NAMES: dict[int, str] = {
    0: "Pallet Town",
    1: "Viridian City",
    2: "Pewter City",
    12: "Route 1",
    13: "Route 2",
    54: "Pewter Gym",
}


class PartyMon(BaseModel):
    species_id: int
    level: int
    hp: int
    max_hp: int
    status: Optional[str] = None
    moves: list[int] = []  # raw move ids -- no move-name table shipped yet


class BattleState(BaseModel):
    opponent_species_id: Optional[int] = None
    opponent_hp: Optional[int] = None
    opponent_max_hp: Optional[int] = None
    opponent_level: Optional[int] = None


class RawState(BaseModel):
    """Exact bytes, no interpretation. Kept for debugging/reproducibility."""

    map_id: int
    x: int
    y: int
    badges_byte: int
    money_raw: bytes
    party_count: int
    battle_type_byte: int

    model_config = ConfigDict(arbitrary_types_allowed=True)


class GameState(BaseModel):
    map_id: int
    map_name: Optional[str] = None
    position: tuple[int, int]
    facing: Optional[str] = None  # unavailable: no confident RAM address found (see memory_map.py)
    party: list[PartyMon]
    badges: list[str]
    money: int
    battle: Optional[BattleState] = None
    dialogue_active: Optional[bool] = None  # heuristic, see emulator-level frame-freeze detector
    frame_count: int

    def to_llm_dict(self) -> dict:
        """Compact JSON-serializable view for LLM prompts (STEP 5's example shape)."""
        d: dict = {
            "map": self.map_name or f"map_id:{self.map_id}",
            "position": list(self.position),
            "party": [
                {
                    "species_id": m.species_id,
                    "level": m.level,
                    "hp": m.hp,
                    "max_hp": m.max_hp,
                    "status": m.status,
                }
                for m in self.party
            ],
            "badges": self.badges,
            "money": self.money,
            "battle": (
                {
                    "opponent_species_id": self.battle.opponent_species_id,
                    "opponent_hp": self.battle.opponent_hp,
                    "opponent_max_hp": self.battle.opponent_max_hp,
                }
                if self.battle
                else None
            ),
        }
        if self.dialogue_active is not None:
            d["dialogue_active"] = self.dialogue_active
        return d


def _read_party(env: PokemonEnv, count: int) -> list[PartyMon]:
    mons: list[PartyMon] = []
    count = max(0, min(count, 6))
    for i in range(count):
        base = mm.PARTY_MON_STRUCT_START + i * mm.PARTY_MON_STRUCT_SIZE
        species = env.read_byte(base + mm.PMON_OFF_SPECIES)
        cur_hp = int.from_bytes(env.read_bytes(base + mm.PMON_OFF_CUR_HP, 2), "big")
        max_hp = int.from_bytes(env.read_bytes(base + mm.PMON_OFF_MAX_HP, 2), "big")
        level = env.read_byte(base + mm.PMON_OFF_LEVEL)
        status_byte = env.read_byte(base + mm.PMON_OFF_STATUS)
        moves_raw = env.read_bytes(base + mm.PMON_OFF_MOVES, 4)
        mons.append(
            PartyMon(
                species_id=species,
                level=level,
                hp=cur_hp,
                max_hp=max_hp,
                status=mm.decode_status(status_byte),
                moves=[m for m in moves_raw if m != 0],
            )
        )
    return mons


def read_raw_state(env: PokemonEnv) -> RawState:
    return RawState(
        map_id=env.read_byte(mm.CUR_MAP.address),
        x=env.read_byte(mm.X_COORD.address),
        y=env.read_byte(mm.Y_COORD.address),
        badges_byte=env.read_byte(mm.BADGES.address),
        money_raw=env.read(mm.MONEY),
        party_count=env.read_byte(mm.PARTY_COUNT.address),
        battle_type_byte=env.read_byte(mm.BATTLE_TYPE.address),
    )


def extract_state(env: PokemonEnv, dialogue_active: Optional[bool] = None) -> GameState:
    raw = read_raw_state(env)
    battle: Optional[BattleState] = None
    if raw.battle_type_byte != 0:
        opp_species = env.read_byte(mm.ENEMY_MON_SPECIES.address)
        opp_hp = int.from_bytes(env.read(mm.ENEMY_MON_CUR_HP), "big")
        # MEDIUM/UNVERIFIED-confidence fields (memory_map.py) -- surfaced
        # but callers should not over-trust max_hp/level precision.
        opp_max_hp = int.from_bytes(env.read(mm.ENEMY_MON_MAX_HP), "big")
        opp_level = env.read_byte(mm.ENEMY_MON_LEVEL.address)
        battle = BattleState(
            opponent_species_id=opp_species,
            opponent_hp=opp_hp,
            opponent_max_hp=opp_max_hp if opp_max_hp else None,
            opponent_level=opp_level if opp_level else None,
        )

    return GameState(
        map_id=raw.map_id,
        map_name=MAP_NAMES.get(raw.map_id),
        position=(raw.x, raw.y),
        facing=None,
        party=_read_party(env, raw.party_count),
        badges=mm.decode_badges(raw.badges_byte),
        money=mm.decode_bcd(raw.money_raw),
        battle=battle,
        dialogue_active=dialogue_active,
        frame_count=env.frame_count,
    )
