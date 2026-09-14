"""
The experiment runner (STEP 18): wires emulator + agent + knowledge +
mission into one run, producing a RunMetrics row.

Condition isolation (STEP 12/22/34) is enforced structurally here, not by
convention:
- Each condition reads/writes its OWN directory. FRESH touches no
  persistent store at all.
- Only the STEALTH condition ever calls extraction.py / writes Claims or
  Procedures. A baseline run can never accumulate structured knowledge.
- Every run starts a brand-new Agent call sequence -- no LLM conversation
  object is ever reused across runs (there is no such object in this
  codebase to begin with; each `agent.respond()` call is independently
  stateless, which IS the fresh-session guarantee).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pokemon_stealth import actions
from pokemon_stealth.agent import (
    AgentObservation,
    Condition,
    DecisionOutcome,
    NotesStore,
    decide,
    load_raw_history,
)
from pokemon_stealth.config import CHECKPOINTS_DIR, PROJECT_ROOT, RESULTS_DIR, STEALTH_DIR, rom_sha256
from pokemon_stealth.emulator import PokemonEnv
from pokemon_stealth.extraction import extract_knowledge, persist_extraction
from pokemon_stealth.knowledge import KnowledgeStore
from pokemon_stealth.llm import Agent, make_agent
from pokemon_stealth.metrics import RunMetrics, append_run
from pokemon_stealth.missions import Mission, get_mission
from pokemon_stealth.state import GameState, extract_state

RAW_HISTORY_ROOT = PROJECT_ROOT / "raw_history" / "runs"
NOTES_ROOT = PROJECT_ROOT / "notes_baseline"

_ACTION_DISPATCH = {
    "move": lambda env, a: actions.move(env, a["direction"], a.get("steps", 1)),
    "interact": lambda env, a: actions.interact(env),
    "advance_dialogue": lambda env, a: actions.advance_dialogue(env),
    "open_menu": lambda env, a: actions.open_menu(env),
    "close_menu": lambda env, a: actions.close_menu(env),
    "select_menu_item": lambda env, a: actions.select_menu_item(env, a["index"]),
    "choose_move": lambda env, a: actions.choose_move(env, a["move_index"]),
    "switch_pokemon": lambda env, a: actions.switch_pokemon(env, a["index"]),
    "run_from_battle": lambda env, a: actions.run_from_battle(env),
    "use_item": lambda env, a: actions.use_item(env, a["bag_index"]),
    "heal_at_pokemon_center": lambda env, a: actions.heal_at_pokemon_center(env),
    "save_game": lambda env, a: actions.save_game(env),
}


@dataclass
class RunConfig:
    mission_id: str
    condition: Condition
    model_id: str
    seed: Optional[int] = None
    headless: bool = True
    extraction_model_id: Optional[str] = None  # None -> reuse model_id (STEP 15's "strong model" is a config choice)
    max_decision_tokens: int = 500
    max_extraction_tokens: int = 1200
    results_dir: Path = RESULTS_DIR


def _checkpoint_path(name: str) -> Path:
    return CHECKPOINTS_DIR / f"{name}.state"


def run_one(config: RunConfig) -> RunMetrics:
    mission: Mission = get_mission(config.mission_id)
    run_id = f"{config.condition.value}-{config.mission_id}-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    env = PokemonEnv(headless=config.headless)
    env.start()
    checkpoint_path = _checkpoint_path(mission.checkpoint)
    if not checkpoint_path.is_file():
        env.close()
        raise FileNotFoundError(
            f"checkpoint '{mission.checkpoint}' not found at {checkpoint_path}. "
            f"Create it first: pokemon-stealth checkpoint create {mission.checkpoint}"
        )
    env.load_state(checkpoint_path)

    rom_hash = rom_sha256(env.rom_path)
    try:
        import importlib.metadata

        pyboy_version = importlib.metadata.version("pyboy")
    except Exception:
        pyboy_version = "unknown"

    agent_client: Agent = make_agent(config.model_id)
    stealth_store = KnowledgeStore(STEALTH_DIR)
    notes_store = NotesStore(NOTES_ROOT)
    RAW_HISTORY_ROOT.mkdir(parents=True, exist_ok=True)

    metrics = RunMetrics(
        run_id=run_id, condition=config.condition.value, model=config.model_id, mission=config.mission_id,
        seed=config.seed, rom_sha256=rom_hash, pyboy_version=str(pyboy_version), checkpoint=mission.checkpoint,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    action_history: list[str] = []
    recent_failures: list[str] = []
    trajectory_summary: list[str] = []
    final_state: GameState = extract_state(env)
    success: Optional[bool] = None
    failure_reason: Optional[str] = None

    # Snapshot existing knowledge ids so extraction can tell created vs reused.
    existing_ids = {
        "claims": {c.id for c in stealth_store.load_all_claims()},
        "procedures": {p.id for p in stealth_store.load_all_procedures()},
        "failures": {f.id for f in stealth_store.load_all_failures()},
    }

    while True:
        elapsed = time.monotonic() - started
        if metrics.macro_actions >= mission.max_actions:
            success, failure_reason = False, "max_actions_exceeded"
            break
        if metrics.llm_calls >= mission.max_llm_calls:
            success, failure_reason = False, "max_llm_calls_exceeded"
            break
        if elapsed >= mission.max_wall_time_s:
            success, failure_reason = False, "max_wall_time_exceeded"
            break

        state = extract_state(env)
        obs = AgentObservation(
            game_state=state, mission_objective=mission.description,
            action_history_summary=action_history, recent_failures=recent_failures,
        )
        if config.condition == Condition.STEALTH:
            claims, procs, fails = stealth_store.retrieve(mission.description, tags=[mission.id])
            obs.retrieved_claims, obs.retrieved_procedures, obs.retrieved_failure_notes = claims, procs, fails
            metrics.claims_retrieved += len(claims)
            metrics.procedures_retrieved += len(procs)
            metrics.failures_retrieved += len(fails)
        elif config.condition == Condition.RAW_HISTORY:
            obs.raw_history_excerpts = load_raw_history(RAW_HISTORY_ROOT, mission.id)
        elif config.condition == Condition.NOTES:
            obs.notes_text = notes_store.read(mission.id)

        outcome: DecisionOutcome = _run_async(decide(agent_client, obs, config.condition, config.max_decision_tokens))
        metrics.llm_calls += 1
        metrics.input_tokens += outcome.usage.input_tokens or 0
        metrics.output_tokens += outcome.usage.output_tokens or 0
        call_cost = outcome.usage.estimated_cost_usd(config.model_id)
        if call_cost is not None:
            metrics.estimated_cost_usd += call_cost

        if outcome.decision is None:
            metrics.llm_parse_failures += 1
            recent_failures.append(f"llm_parse_error: {outcome.parse_error}")
            trajectory_summary.append(f"[LLM_PARSE_ERROR] {outcome.parse_error}")
            continue

        decision = outcome.decision
        dispatch = _ACTION_DISPATCH.get(decision.action.get("type"))
        if dispatch is None:
            recent_failures.append(f"unknown_action:{decision.action.get('type')}")
            continue

        result = dispatch(env, decision.action)
        metrics.macro_actions += 1
        metrics.emulator_frames = env.frame_count
        action_history.append(f"{result.action_type} -> success={result.success} events={result.notable_events}")
        trajectory_summary.append(
            f"action={result.action_type} reason={decision.reason!r} success={result.success} events={result.notable_events}"
        )
        if result.success is False:
            recent_failures.append(result.failure_reason or result.action_type)
        if "battle_started" in result.notable_events:
            metrics.battles_entered += 1
        # battles_lost is NOT tracked here: a battle_ended event alone can't
        # distinguish win/loss/flee from available state deltas (see README
        # limitations) -- left at 0 rather than guessed.
        if any(e.startswith("party_slot_") and e.endswith("_fainted") for e in result.notable_events):
            if all(m.hp <= 0 for m in result.after_state.party):
                metrics.party_wipes += 1

        final_state = result.after_state
        if mission.success_condition(final_state):
            success = True
            break
        fail = mission.failure_condition(final_state)
        if fail:
            success, failure_reason = False, fail
            break

    metrics.wall_time_s = time.monotonic() - started
    metrics.success = success
    metrics.failure_reason = failure_reason
    env.close()

    # --- condition-scoped persistence -------------------------------------
    if config.condition == Condition.STEALTH:
        run_record_id = stealth_store._next_id(stealth_store.runs_dir, "R")
        stealth_store.write_run_record(
            run_record_id,
            {"id": run_record_id, "mission": mission.id, "model": config.model_id, "success": success},
            "\n".join(trajectory_summary),
        )
        extraction_agent = (
            make_agent(config.extraction_model_id) if config.extraction_model_id else agent_client
        )
        extraction_outcome = _run_async(
            extract_knowledge(
                extraction_agent, mission_id=mission.id, trajectory_summary=trajectory_summary,
                before=final_state, after=final_state, success=bool(success), failure_reason=failure_reason,
                max_tokens=config.max_extraction_tokens,
            )
        )
        metrics.llm_calls += 1
        metrics.input_tokens += extraction_outcome.input_tokens or 0
        metrics.output_tokens += extraction_outcome.output_tokens or 0
        from pokemon_stealth.llm import Usage

        extraction_cost = Usage(
            input_tokens=extraction_outcome.input_tokens, output_tokens=extraction_outcome.output_tokens,
        ).estimated_cost_usd(extraction_agent.model_id)
        if extraction_cost is not None:
            metrics.estimated_cost_usd += extraction_cost
        written = persist_extraction(stealth_store, extraction_outcome.result, run_id=run_record_id)
        created = sum(1 for kind, ids in written.items() for i in ids if i not in existing_ids[kind])
        reused = sum(len(ids) for ids in written.values()) - created
        metrics.knowledge_items_created = created
        metrics.knowledge_items_reused = reused
    elif config.condition == Condition.RAW_HISTORY:
        trace_path = RAW_HISTORY_ROOT / f"{mission.id}__{run_id}.md"
        trace_path.write_text("\n".join(trajectory_summary), encoding="utf-8")
    elif config.condition == Condition.NOTES:
        notes_store.append(mission.id, f"run={run_id} success={success} reason={failure_reason} actions={metrics.macro_actions}")
    # FRESH: nothing persisted, by design.

    append_run(config.results_dir, metrics)
    return metrics


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)


def run_experiment(
    mission_id: str, conditions: list[str], runs: int, model_id: str, seed_start: int = 1,
    extraction_model_id: Optional[str] = None, headless: bool = True,
) -> list[RunMetrics]:
    results = []
    for condition_name in conditions:
        condition = Condition(condition_name)
        for i in range(runs):
            config = RunConfig(
                mission_id=mission_id, condition=condition, model_id=model_id, seed=seed_start + i,
                extraction_model_id=extraction_model_id, headless=headless,
            )
            results.append(run_one(config))
    return results
