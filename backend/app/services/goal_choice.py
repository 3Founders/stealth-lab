"""Canonical "which Goal is this request about?" choice, shared by every caller.

Extracted from the MCP `find_ways` tool so the REST API (`GET /v1/goals/choose`)
and the MCP `submit_way` flow answer with the exact same three honest outcomes
-- "resolved", "ambiguous", "no_match" -- from the exact same code. Two copies
would drift; agents would see different Goals depending on the door they used.

Goal choice goes through `retrieval_service`: hybrid candidates -> contextual
JEV/NLI judgment of each against the query AND any request-scoped repo facts ->
bounded, judged hierarchy expansion. `choose_goal` returns None when no
semantic judge answered; the caller then decides its own fallback and must say
so (find_ways falls back to the lexical re-ranker and labels it).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Two judged "matches" closer than this in confidence are a tie: report
# "ambiguous" rather than picking one.
CONFIDENCE_MARGIN = 0.1


def judged_goal_candidate(hit: Any) -> dict:
    candidate = {
        "goal": {"id": hit.id, "canonical_name": hit.name},
        "score": hit.confidence,
        "rationale": f"judge relation={hit.relation} confidence={hit.confidence}",
    }
    if getattr(hit, "hierarchy", None) is not None:
        candidate["hierarchy"] = hit.hierarchy
    return candidate


async def choose_goal(
    pool: Any, query: str, facts: list, *, scope: Any, embedder: Any, top_k: int,
) -> Optional[tuple[str, Optional[dict], dict]]:
    """Returns (outcome, selected_goal, payload), or None when no semantic judge
    answered. `facts` are request-scoped repo facts ({claim_id, statement});
    they only enter the judge's context text and are never stored."""
    from app.services import retrieval_service as rs

    cfg = rs.RetrievalConfig()
    meta = rs.RetrievalMeta()
    judge = rs.default_judge()
    local_claims = [{"id": f["claim_id"], "statement": f["statement"]} for f in facts]
    try:
        ctx = await rs.build_query_context(query, local_claims, embedder=None, cfg=cfg)
        found = await rs.search_goals(pool, ctx, scope=scope, embedder=embedder, judge=judge, cfg=cfg, meta=meta)
        routed = await rs._route_goal_candidates(
            pool, found, scope=scope, cfg=cfg, meta=meta, ctx=ctx, judge=judge,
        )
        # Flat and hierarchy candidates compete on the same judged verdicts: a
        # neighbour the judge calls a firm match can be the chosen Goal.
        found = rs.combine_goal_resolution(found, routed, cfg)
    except Exception:  # noqa: BLE001 -- the caller's fallback still answers; the reply records it
        log.warning("canonical goal choice failed; caller must fall back", exc_info=True)
        return None
    if found.resolution == "unjudged":
        return None
    judgment = {
        "mode": "contextual",
        "resolution": found.resolution,
        "providers": list(meta.providers),
        "degraded_reasons": list(meta.degraded_reasons),
        "local_claim_ids": ctx.claim_ids,
        "hierarchy": meta.goal_routing,
        "related_goals": [
            judged_goal_candidate(hit) for hit in routed if getattr(hit, "hierarchy", None) is not None
        ],
    }
    resolved = list(found.resolved)
    if found.resolution == "matches" and resolved:
        top = resolved[0]
        runner_up = resolved[1] if len(resolved) > 1 else None
        if runner_up is None or (top.confidence or 0) - (runner_up.confidence or 0) >= CONFIDENCE_MARGIN:
            return "resolved", {"id": top.id, "canonical_name": top.name}, {"goal_judgment": judgment}
        return "ambiguous", None, {
            "goal_judgment": judgment,
            "candidates": [judged_goal_candidate(hit) for hit in resolved[:top_k]],
            "rationale": "two or more Goals match this request about equally well -- pick one or rephrase",
        }
    if found.resolution == "partial" and resolved:
        return "ambiguous", None, {
            "goal_judgment": judgment,
            "candidates": [judged_goal_candidate(hit) for hit in resolved[:top_k]],
            "rationale": "known Goals only partly cover this request (broader, narrower or overlapping)",
        }
    return "no_match", None, {
        "goal_judgment": judgment,
        "candidates": [],
        "proposed_goal": {"canonical_name": query.strip()[:200], "description": None, "scope_type": "global",
                          "scope_entity_id": None},
        "rationale": "no known Goal matches this request in context -- propose a new Goal for review",
    }
