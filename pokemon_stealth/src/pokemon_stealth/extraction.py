"""
Post-run knowledge extraction (STEP 15). One LLM call after a run (not
per-action), fed a real trajectory summary + before/after GameState +
verified outcome. STEP 29 applies directly here: if the extraction call
fails or returns unparseable JSON, this returns zero items -- it never
fabricates a fallback claim/procedure to avoid an "empty" result.

Extraction is proposal-only. `persist_extraction` is what actually writes
to the Stealth store, and it routes every item through
KnowledgeStore's dedup (knowledge.py) before creating anything new.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from pokemon_stealth.knowledge import KnowledgeStore
from pokemon_stealth.llm import Agent
from pokemon_stealth.state import GameState

EXTRACTION_SYSTEM_PROMPT = """You extract DURABLE, REUSABLE knowledge from one Pokemon Red run.
Rules:
- Only include a claim/procedure/failure if it would help a DIFFERENT future run of this game.
- No trivial restatement of the mission itself (e.g. "reaching Pewter City is the goal").
- No hallucinated facts -- only what is directly supported by the trajectory/outcome given.
- Zero items in any category is a valid, expected answer if nothing durable happened.
- Respond with ONLY a JSON object: {"claims": [{"statement": str, "tags": [str]}],
  "procedures": [{"title": str, "applicability": [str], "method": [str], "verification": [str],
  "known_failures": [str], "tags": [str]}], "failures": [{"description": str, "tags": [str]}]}
"""


class ExtractedClaim(BaseModel):
    statement: str
    tags: list[str] = []


class ExtractedProcedure(BaseModel):
    title: str
    applicability: list[str] = []
    method: list[str] = []
    verification: list[str] = []
    known_failures: list[str] = []
    tags: list[str] = []


class ExtractedFailure(BaseModel):
    description: str
    tags: list[str] = []


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim] = []
    procedures: list[ExtractedProcedure] = []
    failures: list[ExtractedFailure] = []


@dataclass
class ExtractionOutcome:
    result: ExtractionResult
    parse_error: str | None
    input_tokens: int | None
    output_tokens: int | None


def _build_prompt(
    mission_id: str, trajectory_summary: list[str], before: GameState, after: GameState,
    success: bool, failure_reason: str | None,
) -> str:
    return json.dumps(
        {
            "mission": mission_id,
            "trajectory_summary": trajectory_summary[-40:],  # cap length -- STEP 24
            "before_state": before.to_llm_dict(),
            "after_state": after.to_llm_dict(),
            "outcome": {"success": success, "failure_reason": failure_reason},
        }
    )


async def extract_knowledge(
    agent: Agent, *, mission_id: str, trajectory_summary: list[str], before: GameState, after: GameState,
    success: bool, failure_reason: str | None, max_tokens: int = 1200,
) -> ExtractionOutcome:
    user_prompt = _build_prompt(mission_id, trajectory_summary, before, after, success, failure_reason)
    response = await agent.respond(EXTRACTION_SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens)
    try:
        text = response.text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in extraction response")
        payload = json.loads(text[start : end + 1])
        result = ExtractionResult.model_validate(payload)
        return ExtractionOutcome(
            result=result, parse_error=None,
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
        )
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        return ExtractionOutcome(
            result=ExtractionResult(), parse_error=str(exc),
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
        )


def persist_extraction(
    store: KnowledgeStore, result: ExtractionResult, *, run_id: str, scope: str = "pokemon-red",
) -> dict[str, list[str]]:
    """Returns the ids actually written/reused, split by kind -- metrics.py
    uses this to distinguish 'created' from 'reused' (STEP 19)."""
    written: dict[str, list[str]] = {"claims": [], "procedures": [], "failures": []}
    for c in result.claims:
        claim = store.write_claim(c.statement, scope=scope, tags=c.tags, evidence=[run_id])
        written["claims"].append(claim.id)
    for p in result.procedures:
        proc = store.write_procedure(
            p.title, applicability=p.applicability, method=p.method, verification=p.verification,
            known_failures=p.known_failures, scope=scope, tags=p.tags, evidence=[run_id],
        )
        written["procedures"].append(proc.id)
    for f in result.failures:
        fail = store.write_failure(f.description, scope=scope, tags=f.tags, evidence=[run_id])
        written["failures"].append(fail.id)
    return written
