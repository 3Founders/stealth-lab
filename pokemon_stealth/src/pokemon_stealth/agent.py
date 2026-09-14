"""
Model-independent agent interface (STEP 11) + the four experiment
conditions (STEP 12).

Every condition gets a FRESH LLM conversation each call (STEP 22) -- there
is no hidden chat history anywhere in this module. The only things that can
differ between conditions are what's assembled into THIS call's prompt:

- FRESH: mission + current state + short local action-history summary only.
- RAW_HISTORY: FRESH + verbatim excerpts of past run records for this
  mission (unstructured, unranked -- contrast case against STEALTH's
  curated retrieval).
- NOTES: FRESH + a flat, unstructured per-mission notes file (deterministic
  one-line-per-run summaries, no LLM extraction, no schema, no dedup).
- STEALTH: FRESH + retrieved Claims/Procedures/Failures from knowledge.py
  (ranked, deduped, evidence-backed).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError, field_validator

from pokemon_stealth.knowledge import Claim, Failure, Procedure
from pokemon_stealth.llm import Agent, LLMResponse, Usage
from pokemon_stealth.state import GameState


class Condition(str, Enum):
    FRESH = "fresh"
    RAW_HISTORY = "raw_history"
    NOTES = "notes"
    STEALTH = "stealth"


# --- action schema (STEP 11) ------------------------------------------------

_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "move": ("direction",),
    "interact": (),
    "advance_dialogue": (),
    "open_menu": (),
    "close_menu": (),
    "select_menu_item": ("index",),
    "choose_move": ("move_index",),
    "switch_pokemon": ("index",),
    "run_from_battle": (),
    "use_item": ("bag_index",),
    "heal_at_pokemon_center": (),
    "save_game": (),
}


class AgentDecision(BaseModel):
    action: dict[str, Any]
    reason: str
    knowledge_refs: list[str] = []
    confidence: Optional[float] = None
    plan_update: Optional[str] = None

    @field_validator("action")
    @classmethod
    def _validate_action(cls, v: dict[str, Any]) -> dict[str, Any]:
        action_type = v.get("type")
        if action_type not in _REQUIRED_PARAMS:
            raise ValueError(f"unknown action type '{action_type}', expected one of {sorted(_REQUIRED_PARAMS)}")
        for param in _REQUIRED_PARAMS[action_type]:
            if param not in v:
                raise ValueError(f"action '{action_type}' missing required param '{param}'")
        return v


@dataclass
class AgentObservation:
    game_state: GameState
    mission_objective: str
    action_history_summary: list[str]
    recent_failures: list[str]
    retrieved_claims: list[Claim] = field(default_factory=list)
    retrieved_procedures: list[Procedure] = field(default_factory=list)
    retrieved_failure_notes: list[Failure] = field(default_factory=list)
    raw_history_excerpts: list[str] = field(default_factory=list)
    notes_text: str = ""


SYSTEM_PROMPT = """You control a Pokemon Red agent via a small set of macro actions.
Respond with ONLY a single JSON object, no prose outside it, matching:
{"action": {"type": "<action_type>", ...params}, "reason": "<short reason>", "knowledge_refs": ["<ids used, if any>"], "confidence": <0..1>}

Valid action types and their params:
- move: {"direction": "up|down|left|right", "steps": <int, optional>}
- interact: {}
- advance_dialogue: {}
- open_menu: {}
- close_menu: {}
- select_menu_item: {"index": <int>}
- choose_move: {"move_index": <0-3>}
- switch_pokemon: {"index": <0-5>}
- run_from_battle: {}
- use_item: {"bag_index": <int>}
- heal_at_pokemon_center: {}
- save_game: {}
"""


def _build_user_prompt(obs: AgentObservation, condition: Condition) -> str:
    parts = [
        f"MISSION: {obs.mission_objective}",
        f"CURRENT STATE: {json.dumps(obs.game_state.to_llm_dict())}",
    ]
    if obs.action_history_summary:
        parts.append("RECENT ACTIONS: " + " | ".join(obs.action_history_summary[-8:]))
    if obs.recent_failures:
        parts.append("RECENT FAILURES THIS RUN: " + " | ".join(obs.recent_failures[-5:]))

    if condition == Condition.RAW_HISTORY and obs.raw_history_excerpts:
        parts.append("PAST RUN RECORDS (raw, unranked):\n" + "\n---\n".join(obs.raw_history_excerpts))
    elif condition == Condition.NOTES and obs.notes_text:
        parts.append("PERSISTENT NOTES:\n" + obs.notes_text)
    elif condition == Condition.STEALTH:
        if obs.retrieved_claims:
            parts.append(
                "RETRIEVED CLAIMS:\n"
                + "\n".join(f"[{c.id}] {c.statement}" for c in obs.retrieved_claims)
            )
        if obs.retrieved_procedures:
            parts.append(
                "RETRIEVED PROCEDURES:\n"
                + "\n".join(
                    f"[{p.id}] {p.title} -- steps: {'; '.join(p.method)}" for p in obs.retrieved_procedures
                )
            )
        if obs.retrieved_failure_notes:
            parts.append(
                "KNOWN FAILURE MODES:\n"
                + "\n".join(f"[{f.id}] {f.description}" for f in obs.retrieved_failure_notes)
            )

    parts.append("Choose exactly one action now.")
    return "\n\n".join(parts)


def parse_decision(text: str) -> AgentDecision:
    """Raises on malformed output -- callers must count this as a failed
    LLM call (metrics.py), never silently retry with a fabricated action."""
    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object found in model output: {text!r}")
    payload = json.loads(stripped[start : end + 1])
    return AgentDecision.model_validate(payload)


@dataclass
class DecisionOutcome:
    decision: Optional[AgentDecision]
    usage: Usage
    model_id: str
    raw_text: str
    parse_error: Optional[str] = None


async def decide(agent: Agent, obs: AgentObservation, condition: Condition, max_tokens: int = 500) -> DecisionOutcome:
    user_prompt = _build_user_prompt(obs, condition)
    response: LLMResponse = await agent.respond(SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens)
    try:
        decision = parse_decision(response.text)
        return DecisionOutcome(decision=decision, usage=response.usage, model_id=response.model_id, raw_text=response.text)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        return DecisionOutcome(
            decision=None, usage=response.usage, model_id=response.model_id,
            raw_text=response.text, parse_error=str(exc),
        )


# --- condition-specific context assembly -----------------------------------

class NotesStore:
    """
    Deliberately dumb persistence for the NOTES baseline condition (STEP
    12C): one appended plain-text line per completed run, no structure, no
    retrieval, no dedup. This exists ONLY to prove Stealth beats "just
    notes" -- it must stay structurally simpler than knowledge.py.
    """

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, mission_id: str) -> Path:
        return self.root / f"{mission_id}.md"

    def read(self, mission_id: str, max_lines: int = 30) -> str:
        path = self._path(mission_id)
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-max_lines:])

    def append(self, mission_id: str, line: str) -> None:
        path = self._path(mission_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")


def load_raw_history(runs_dir: Path, mission_id: str, max_runs: int = 5) -> list[str]:
    """
    Verbatim excerpts of the last N raw run traces for this mission -- no
    ranking, no filtering by relevance, no dedup. Reads from the
    RAW_HISTORY condition's OWN runs directory (kept separate from
    stealth/runs/, which is STEALTH-condition evidence) so this baseline
    can't accidentally read STEALTH's curated data.
    """
    if not runs_dir.is_dir():
        return []
    paths = sorted(runs_dir.glob(f"{mission_id}__*.md"))
    excerpts = []
    for path in paths[-max_runs:]:
        excerpts.append(path.read_text(encoding="utf-8")[:1500])  # cap length -- STEP 24
    return excerpts
