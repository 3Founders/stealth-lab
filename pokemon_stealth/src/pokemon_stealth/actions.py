"""
Macro actions. Deliberately a SMALL, reliable set (per spec STEP 7): no
free-form pathfinding, no fragile multi-screen menu chains beyond what can
be verified against real state. Every action returns an ActionResult with
before/after GameState so callers (and metrics.py) can tell what actually
happened without re-deriving it.

`success` is Optional[bool] -- None means "cannot be verified from RAM",
which is reported honestly rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pokemon_stealth.emulator import PokemonEnv
from pokemon_stealth.state import GameState, extract_state

DIRECTIONS = {"up", "down", "left", "right"}


@dataclass
class ActionResult:
    action_type: str
    success: Optional[bool]
    frames_used: int
    before_state: GameState
    after_state: GameState
    notable_events: list[str]
    failure_reason: Optional[str] = None


def _events(before: GameState, after: GameState) -> list[str]:
    events: list[str] = []
    if before.map_id != after.map_id:
        events.append(f"entered_map:{after.map_name or after.map_id}")
    if before.badges != after.badges:
        gained = set(after.badges) - set(before.badges)
        if gained:
            events.append(f"badge_obtained:{','.join(sorted(gained))}")
    if before.battle is None and after.battle is not None:
        events.append("battle_started")
    if before.battle is not None and after.battle is None:
        events.append("battle_ended")
    for i, (b_mon, a_mon) in enumerate(zip(before.party, after.party)):
        if b_mon.hp > 0 and a_mon.hp == 0:
            events.append(f"party_slot_{i}_fainted")
        if a_mon.hp < b_mon.hp:
            events.append(f"party_slot_{i}_took_damage:{b_mon.hp - a_mon.hp}")
    if before.battle and after.battle and after.battle.opponent_hp is not None:
        if before.battle.opponent_hp and after.battle.opponent_hp < before.battle.opponent_hp:
            events.append(f"opponent_took_damage:{before.battle.opponent_hp - after.battle.opponent_hp}")
    return events


def _run(env: PokemonEnv, action_type: str, button_seq: list[tuple[str, int]]) -> ActionResult:
    before = extract_state(env)
    frames_before = env.frame_count
    for button, hold in button_seq:
        env.press(button, hold_frames=hold)
    after = extract_state(env)
    frames_used = env.frame_count - frames_before
    events = _events(before, after)
    return ActionResult(
        action_type=action_type,
        success=None,  # caller (higher-level wrapper) fills in when it knows
        frames_used=frames_used,
        before_state=before,
        after_state=after,
        notable_events=events,
        failure_reason=None,
    )


def move(env: PokemonEnv, direction: str, steps: int = 1) -> ActionResult:
    direction = direction.lower()
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got '{direction}'")
    before = extract_state(env)
    for _ in range(steps):
        env.press(direction, hold_frames=10)
    after = extract_state(env)
    moved = after.position != before.position or after.map_id != before.map_id
    reason = None if moved else "blocked_movement"
    return ActionResult(
        action_type=f"move:{direction}",
        success=moved,
        frames_used=env.frame_count - before.frame_count,
        before_state=before,
        after_state=after,
        notable_events=_events(before, after) + ([] if moved else ["blocked_movement"]),
        failure_reason=reason,
    )


def interact(env: PokemonEnv) -> ActionResult:
    """Press A facing whatever is in front (sign, NPC, item)."""
    return _run(env, "interact", [("a", 10)])


def advance_dialogue(env: PokemonEnv, presses: int = 1) -> ActionResult:
    return _run(env, "advance_dialogue", [("a", 8)] * presses)


def open_menu(env: PokemonEnv) -> ActionResult:
    return _run(env, "open_menu", [("start", 10)])


def close_menu(env: PokemonEnv) -> ActionResult:
    return _run(env, "close_menu", [("b", 10)])


def select_menu_item(env: PokemonEnv, index: int) -> ActionResult:
    """Moves down `index` times in the current menu, then confirms with A."""
    seq = [("down", 6)] * index + [("a", 10)]
    return _run(env, f"select_menu_item:{index}", seq)


def choose_move(env: PokemonEnv, move_index: int) -> ActionResult:
    """
    Battle-menu move selection by slot (0-3). No move-name table is shipped
    (would require a static Gen-1 move list this harness hasn't verified),
    so callers select by index; agent.py surfaces move ids from GameState
    for the LLM to reason over.
    """
    if not 0 <= move_index <= 3:
        raise ValueError("move_index must be 0-3")
    before = extract_state(env)
    seq = [("down", 6)] * move_index + [("a", 10)]
    for button, hold in seq:
        env.press(button, hold_frames=hold)
    after = extract_state(env)
    return ActionResult(
        action_type=f"choose_move:{move_index}",
        success=None,
        frames_used=env.frame_count - before.frame_count,
        before_state=before,
        after_state=after,
        notable_events=_events(before, after),
    )


def switch_pokemon(env: PokemonEnv, index: int) -> ActionResult:
    if not 0 <= index <= 5:
        raise ValueError("index must be 0-5")
    seq = [("down", 6)] * index + [("a", 10)]
    return _run(env, f"switch_pokemon:{index}", seq)


def run_from_battle(env: PokemonEnv) -> ActionResult:
    """Standard Gen-1 battle-menu RUN option is the 4th slot (index 3)."""
    before = extract_state(env)
    seq = [("down", 6)] * 3 + [("a", 10)]
    for button, hold in seq:
        env.press(button, hold_frames=hold)
    after = extract_state(env)
    fled = before.battle is not None and after.battle is None
    return ActionResult(
        action_type="run_from_battle",
        success=fled,
        frames_used=env.frame_count - before.frame_count,
        before_state=before,
        after_state=after,
        notable_events=_events(before, after),
        failure_reason=None if fled else "could_not_confirm_flee",
    )


def use_item(env: PokemonEnv, bag_index: int) -> ActionResult:
    """
    Selects an item by BAG SLOT INDEX, not name -- no item-name table is
    shipped yet (memory_map.py's BAG_ITEMS_START is MEDIUM confidence and
    undecoded into names here). agent.py must read raw item ids from
    GameState if it wants to reason about which slot is which.
    """
    seq = [("down", 6)] * bag_index + [("a", 10), ("a", 10)]
    return _run(env, f"use_item:{bag_index}", seq)


def heal_at_pokemon_center(env: PokemonEnv) -> ActionResult:
    """
    Assumes the agent is already standing in front of the Nurse (no
    autonomous pathfinding -- per STEP 7, navigation stays out of V1's
    action set). Interacts, advances the confirmation dialogue, and waits
    out the heal animation.
    """
    before = extract_state(env)
    env.press("a", hold_frames=10)  # talk to Nurse
    env.tick(20)
    env.press("a", hold_frames=10)  # confirm heal
    env.tick(180)  # heal animation -- real games run this in real time; skip via ticking
    env.press("a", hold_frames=10)  # dismiss "thank you" dialogue
    after = extract_state(env)
    healed = all(m.hp == m.max_hp for m in after.party) and len(after.party) == len(before.party)
    return ActionResult(
        action_type="heal_at_pokemon_center",
        success=healed if after.party else None,
        frames_used=env.frame_count - before.frame_count,
        before_state=before,
        after_state=after,
        notable_events=_events(before, after),
        failure_reason=None if healed else "party_not_fully_healed_after_sequence",
    )


def save_game(env: PokemonEnv) -> ActionResult:
    """
    Best-effort SAVE menu sequence (Start -> SAVE -> confirm YES). There is
    no confidently-known RAM flag to verify a save actually completed, so
    success is always None here -- this is reported honestly rather than
    assumed. Use save_state() checkpoints (emulator.py) for anything the
    experiment needs to rely on.
    """
    before = extract_state(env)
    env.press("start", hold_frames=10)
    env.tick(10)
    env.press("down", hold_frames=6)  # SAVE is typically the 2nd option
    env.press("a", hold_frames=10)
    env.tick(20)
    env.press("a", hold_frames=10)  # confirm YES
    env.tick(60)
    after = extract_state(env)
    return ActionResult(
        action_type="save_game",
        success=None,
        frames_used=env.frame_count - before.frame_count,
        before_state=before,
        after_state=after,
        notable_events=_events(before, after),
        failure_reason="save_completion_not_RAM_verifiable",
    )
