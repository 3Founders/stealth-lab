"""A second, independent judgment for low-confidence Goal hierarchy placements (2026-09-29).

Placement accepts a SPECIALIZES edge only when the identity judge is at least `goal_abstraction_minimum_confidence`
(0.9) sure; everything below became a `proposed` edge plus a human review item. At ingestion scale nobody reviews
them: on a 75-Goal test 363 of 367 edges stayed proposed and the hierarchy (and everything that reads only accepted
edges -- hierarchical retrieval, benchmark transfer, recommender pooling) stayed flat.

This module asks a second model the same question independently, without showing it the first verdict:

    agree   (same relation, confidence >= AGREE_CONFIDENCE)                 -> accept
    dissent (unrelated / overlapping only, confidence >= DISSENT_CONFIDENCE) -> reject
    anything else, or the second judge unavailable                           -> review (unchanged behaviour)

Two independent judgments that agree are the acceptance rule; only disagreements reach a human. The structural
checks (scope, visibility, cycles, redundancy) still run inside `persist_goal_relation` for every accepted edge.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

AGREE_CONFIDENCE = 0.8
DISSENT_CONFIDENCE = 0.8
SECOND_JUDGE_OP = "goal_relation_second_judge"
RELATIONS = ("specializes", "generalizes", "same", "overlapping", "unrelated")

_PROMPT = """You judge how two software goals relate. Reply with ONE JSON object:
{"relation": "specializes|generalizes|same|overlapping|unrelated", "confidence": 0.0-1.0, "reason": "one sentence"}

A "specializes" B: A is a narrower case of B -- achieving A is one way of achieving B, and B covers more than A.
A "generalizes" B: the reverse.
"same": they ask for the same outcome.
"overlapping": they share some work but neither contains the other.
"unrelated": neither.
Judge the outcomes the goals ask for, not shared words. Be strict: only say specializes/generalizes when the
containment is clear."""


@dataclass(frozen=True)
class SecondOpinion:
    decision: str            # accept | reject | review
    relation: Optional[str]
    confidence: Optional[float]
    reason: str
    model: Optional[str]


def decide(first_relation: str, relation: Optional[str], confidence: Optional[float]) -> str:
    if relation is None or confidence is None:
        return "review"
    if relation == first_relation and confidence >= AGREE_CONFIDENCE:
        return "accept"
    if relation in ("unrelated", "overlapping") and confidence >= DISSENT_CONFIDENCE:
        return "reject"
    return "review"


def _model() -> Optional[str]:
    from app.config import settings

    return (os.environ.get("GOAL_RELATION_SECOND_JUDGE_MODEL") or settings.goal_relation_second_judge_model
            or os.environ.get("INGEST_MODEL") or settings.ingest_model
            or settings.trajectory_extraction_strong_model or None)


def _client() -> Optional[Any]:
    """Vertex for a `google/...` model that IS the configured VERTEX_MODEL (the core wrapper answers with that model
    only), General Compute otherwise; None when the chosen provider is not configured."""
    from app.config import settings

    model = _model() or ""
    if model.startswith("google/"):
        if model != settings.vertex_model or not settings.vertex_project:
            return None
        from app.services.ingestion_jobs import _vertex_oauth_client

        return _vertex_oauth_client()
    if not settings.general_compute_api_key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url,
                  max_retries=2, timeout=60.0)


def _describe(goal: Mapping[str, Any]) -> str:
    name = str(goal.get("canonical_name") or goal.get("name") or "")
    desc = str(goal.get("description") or goal.get("short_description") or "")[:600]
    return f"{name}\n{desc}".strip()


async def second_opinion(specific: Mapping[str, Any], abstract: Mapping[str, Any], *, first_relation: str,
                         client: Any = None, model: Optional[str] = None) -> SecondOpinion:
    """Ask whether `specific` relates to `abstract` as the first judge said. Never raises: an unavailable judge is
    a `review` decision, exactly what happens without this module."""
    from app.services import ingest_budget
    from app.services.llm_json import parse_json_object
    from app.utils.aio import run_blocking

    model = model or _model()
    client = client if client is not None else _client()
    if client is None or not model:
        return SecondOpinion("review", None, None, "second judge not configured", None)
    try:
        await ingest_budget.guard(SECOND_JUDGE_OP)
        resp = await run_blocking(
            client.chat.completions.create, model=model, temperature=0, max_tokens=200,
            messages=[{"role": "system", "content": _PROMPT},
                      {"role": "user", "content": f"A:\n{_describe(specific)}\n\nB:\n{_describe(abstract)}"}])
        await ingest_budget.record_completion(model, SECOND_JUDGE_OP, getattr(resp, "usage", None))
        parsed = parse_json_object((resp.choices[0].message.content or "").strip()) or {}
    except ingest_budget.BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 -- unavailable second judge = human review, as before
        log.warning("goal relation second judge unavailable: %r", exc)
        return SecondOpinion("review", None, None, f"second judge unavailable: {type(exc).__name__}", model)
    relation = str(parsed.get("relation") or "").strip().lower()
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    if relation not in RELATIONS or confidence is None or not 0.0 <= confidence <= 1.0:
        return SecondOpinion("review", None, None, "second judge reply unusable", model)
    return SecondOpinion(decide(first_relation, relation, confidence), relation, confidence,
                         str(parsed.get("reason") or "")[:300], model)
