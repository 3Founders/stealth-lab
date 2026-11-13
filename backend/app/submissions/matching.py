"""Is a submitted goal (or step) a goal kell already has? Search finds the candidates, JEV decides.

    1. exact      the same normalized name among public goals: that goal, no model call.
    2. candidates kell's goal identity search over PUBLIC goals (identity_resolution.generate_goal_candidates: full
                  text + the goal vectors, RRF-fused), the query embedded by the embedder that wrote the goal vectors.
    3. JEV        for each candidate, three typed questions in parallel -- is A the same goal as B, narrower, broader,
                  related or different? (JEV identity judgment; the same question ingestion asks):
                    the submitted name + description vs the candidate's name + description,
                    the submitted name vs the candidate's name,
                    the candidate's name vs the submitted name (the reverse; narrower and broader swap).

Decision (a vote, because JEV's confidence for the same pair drifts between calls -- 0.98 one hour, 0.54-0.71 the
next -- while its relation label is steady):
    duplicate            >= 2 of the 3 answers are "same" at confidence >= SAME_VOTE_MIN: that goal is reused.
    possible duplicate   one answer is "same": a new goal, with the candidate recorded so it can be merged later.
    narrower / broader   >= 2 answers agree: kept as a relation, proposed as a goal link when the step is approved.
Measured 2026-11-13 on 47 labelled pairs, twice: 18/20 duplicates caught, 0/27 different goals merged, the same both
times; JEV never answered "same" for two different goals (docs/goal_submission.md, Matching).

Hierarchy: when a goal a submission created is approved, `place_goal` runs kell's goal placement
(ingestion_jobs.handle_goal_abstraction_placement: nearby goals plus their parents and children, cycle and redundancy
checks, a second model below the threshold) with `VotingJEV` as its judge, so a special case or an abstraction of an
existing goal becomes an accepted SPECIALIZES link that search walks (docs/search.md). A link needs >= 2 of 3 answers
agreeing at mean confidence >= PLACE_MIN: measured 2026-11-13 on 35 pairs, 21/22 links right, 0 wrong.

JEV only: when it cannot answer (not configured, down, or no `identity` capability), the goal is new and the decision
is `judge_unavailable` -- a duplicate can be merged later, a wrong merge sends people to a Way that does something
else. `match_goal` -> {"goal": {id, canonical_name, home_shard_id} | None, "decision": "...", "candidates": [...],
"relations": [...], "reason": "..."}.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

log = logging.getLogger(__name__)

TOP_N = 6              # candidates JEV is asked about (3 questions each)
SAME_VOTE_MIN = 0.5    # a "same" answer counts as a vote at this confidence or above
CONCURRENCY = 12       # JEV calls in flight for one goal
PLACE_MIN = 0.7        # a narrower/broader link is accepted at this mean confidence (below: the second judge)
PLACE_CANDIDATES = 20  # goals placement weighs (search hits plus their parents and children)
PLACE_TIMEOUT_S = 90
_FLIP = {"specializes": "generalizes", "generalizes": "specializes"}

_JUDGE: Any = None


def jev_judge() -> Any:
    """The semantic judge with JEV as its only provider (no fallback model guesses identity)."""
    global _JUDGE
    if _JUDGE is None:
        from app.services.identity_resolution import default_judge
        from app.services.semantic.chain import SemanticJudge

        chain = default_judge()
        _JUDGE = SemanticJudge([p for p in chain.providers if p.name == "jev"], chain.policy)
    return _JUDGE


async def _exact(pool: Any, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        """SELECT goal_id::text AS id, canonical_name, home_shard_id FROM goal_search_index
           WHERE normalize_goal_name(canonical_name) = normalize_goal_name($1) AND visibility = 'public'
             AND status IN ('active', 'candidate') AND COALESCE(scope_type, 'global') = 'global'
           ORDER BY (status = 'active') DESC, has_procedures DESC, t_created LIMIT 1""", name)
    return dict(row) if row else None


async def _embed(pool: Any, text: str) -> tuple[Optional[list[float]], Optional[str]]:
    if os.environ.get("SUBMISSION_GOAL_VECTORS", "on").lower() == "off":
        return None, None
    try:
        from app.ingestion.handlers import Dependencies  # the ingestion pipeline's embedder: the one that wrote the goal vectors

        vec, meta = await Dependencies.get_embedder(pool).embed_one_with_metadata(text, input_type="query")
        return list(vec), meta.model_id
    except Exception as exc:  # noqa: BLE001 -- no embedder here: full text alone
        log.info("goal match: no query vector (%s)", type(exc).__name__)
        return None, None


def _goal(c: Any) -> dict:
    return {"id": c.id, "canonical_name": c.name, "home_shard_id": c.home_shard_id}


def _brief(c: Any) -> dict:
    return {"id": c.id, "name": c.name, "relation": c.relation, "confidence": c.confidence}


async def vote(judge: Any, a_name: str, a_text: str, b_name: str, b_text: str,
               gate: Optional[asyncio.Semaphore] = None) -> Optional[dict]:
    """JEV asked three ways about A against B -> {relation, confidence, same_votes, via}, or None when it never
    answered. relation: "same" (>= 2 answers "same" at >= SAME_VOTE_MIN), "specializes" / "generalizes" (>= 2 agree;
    confidence = their mean), "possible_same" (one "same"), else the most common other answer."""
    gate = gate or asyncio.Semaphore(CONCURRENCY)

    async def ask(x: str, y: str, flip: bool = False) -> Optional[tuple[str, float, str]]:
        async with gate:
            r = await judge.judge_identity("goal", x, y)
        if not (r.ok and isinstance(r.value, dict)):
            return None
        rel = _FLIP.get(r.value["relation"], r.value["relation"]) if flip else r.value["relation"]
        return rel, float(r.value["confidence"]), f"{r.provider}:{r.model}"

    got = [g for g in await asyncio.gather(ask(a_text, b_text), ask(a_name, b_name), ask(b_name, a_name, True)) if g]
    if not got:
        return None
    via = got[0][2]
    same = [c for r, c, _ in got if r == "same"]
    votes = sum(1 for c in same if c >= SAME_VOTE_MIN)
    if votes >= 2:
        return {"relation": "same", "confidence": round(sum(same) / len(same), 2), "same_votes": votes, "via": via}
    for rel in ("specializes", "generalizes"):
        agree = [c for r, c, _ in got if r == rel]
        if len(agree) >= 2:
            return {"relation": rel, "confidence": round(sum(agree) / len(agree), 2), "same_votes": votes, "via": via}
    if same:
        return {"relation": "possible_same", "confidence": round(max(same), 2), "same_votes": votes, "via": via}
    rels = [r for r, _c, _v in got]
    top = max(set(rels), key=rels.count)
    return {"relation": top, "confidence": round(max(c for r, c, _ in got if r == top), 2), "same_votes": 0,
            "via": via}


def _name(text: str) -> str:
    return text.split(": ", 1)[0]


class VotingJEV:
    """The judge interface goal placement calls (`judge_identity_batch`), answered by the three-way JEV vote per
    candidate. A "possible_same" is reported as "related": placement never links or merges on it."""

    def __init__(self, judge: Any = None):
        self._judge = judge if judge is not None else jev_judge()
        self.providers = getattr(self._judge, "providers", [])
        self.chain_id = f"{getattr(self._judge, 'chain_id', 'jev')}+vote3"   # recorded with each identity decision

    async def judge_identity(self, kind: str, a: str, b: str) -> Any:
        return await self._judge.judge_identity(kind, a, b)

    async def judge_identity_batch(self, kind: str, a: str, candidates: list) -> Any:
        gate = asyncio.Semaphore(CONCURRENCY)
        verdicts = await asyncio.gather(*[vote(self._judge, _name(a), a, _name(c), c, gate) for c in candidates])
        ok = all(v is not None for v in verdicts)
        value = [{"relation": "related" if v["relation"] == "possible_same" else v["relation"],
                  "confidence": v["confidence"]} for v in verdicts] if ok else None
        provider, _, model = next((v["via"] for v in verdicts if v), "jev:").partition(":")
        return type("VoteResult", (), {"ok": ok, "value": value, "provider": provider, "model": model + "+vote3",
                                       "reason": None if ok else "JEV did not answer", "attempts": None})()


async def place_goal(pool: Any, goal_id: str) -> dict:
    """Put a newly public goal into kell's goal hierarchy (see the module doc). Best-effort: a failure is logged and
    returned, never raised -- the hierarchy is never required for retrieval."""
    from app.services import search_projection
    from app.services.identity_resolution import goal_abstraction_placement_key
    from app.services.ingestion_jobs import handle_goal_abstraction_placement
    from app.services.shards import pools_for

    try:
        async with pool.acquire() as conn:            # placement reads the projection: bring it up to date first
            async with conn.transaction():
                await search_projection.project_object(conn, "goal", goal_id, pools=pools_for(pool))
        payload = {"goal_id": goal_id, "judge_mode": "model", "candidate_limit": PLACE_CANDIDATES,
                   "neighbor_seed_limit": 5, "neighbor_limit": PLACE_CANDIDATES, "minimum_confidence": PLACE_MIN,
                   "idempotency_key": goal_abstraction_placement_key(goal_id),
                   "_job": {"scope_type": "global", "visibility": "public"}}
        return await asyncio.wait_for(handle_goal_abstraction_placement(pool, payload, judge=VotingJEV()),
                                      PLACE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        log.warning("goal %s placement failed (%s)", goal_id, type(exc).__name__, exc_info=True)
        return {"error": type(exc).__name__}


async def match_goal(pool: Any, name: str, description: str = "", *, judge: Any = None) -> dict:
    exact = await _exact(pool, name)
    if exact is not None:
        return {"goal": exact, "decision": "exact", "candidates": [], "relations": [], "reason": "the same name"}
    from app.services.identity_resolution import generate_goal_candidates

    text = f"{name}: {description}" if description else name
    try:
        vec, model_id = await _embed(pool, text)
        cands, _n_fts, _n_vec = await generate_goal_candidates(
            pool, text, scope_type="global", scope_entity_id=None, embedding=vec, embedding_model=model_id,
            top_n=TOP_N, visibility="public")
    except Exception as exc:  # noqa: BLE001 -- search failing must not block a submission: a new goal
        log.warning("goal match: search failed (%s)", type(exc).__name__)
        return {"goal": None, "decision": "search_failed", "candidates": [], "relations": [],
                "reason": type(exc).__name__}
    if not cands:
        return {"goal": None, "decision": "no_candidates", "candidates": [], "relations": [],
                "reason": "vectors" if vec else "full text only"}
    names = [c.name for c in cands]
    judge = judge if judge is not None else jev_judge()
    gate = asyncio.Semaphore(CONCURRENCY)
    verdicts = await asyncio.gather(*[vote(judge, name, text, c.name, c.text, gate) for c in cands])
    if not any(verdicts):
        return {"goal": None, "decision": "judge_unavailable", "candidates": names, "relations": [],
                "reason": "JEV did not answer"}
    via = next(v["via"] for v in verdicts if v)
    best, possible, relations = None, None, []
    for c, v in zip(cands, verdicts):
        if v is None:
            continue
        c.relation, c.confidence = v["relation"], v["confidence"]
        if v["relation"] == "same":
            if best is None or (v["same_votes"], v["confidence"]) > best[0]:
                best = ((v["same_votes"], v["confidence"]), c)
        elif v["relation"] == "possible_same":
            c.relation = "same"
            possible = possible or c
        elif v["relation"] in _FLIP:
            relations.append(_brief(c))
    if best is not None:
        (votes, conf), c = best
        return {"goal": _goal(c), "decision": f"same:{via}", "candidates": names, "relations": [],
                "reason": f"JEV: the same goal as {c.name!r} ({votes} of 3 answers, {conf:.2f})"}
    if possible is not None:
        return {"goal": None, "decision": f"possible_duplicate:{via}", "candidates": names, "relations": relations,
                "possible_duplicate": _brief(possible),
                "reason": f"JEV: maybe the same goal as {possible.name!r} (1 of 3 answers); kept new, merge later if so"}
    return {"goal": None, "decision": f"new:{via}", "candidates": names, "relations": relations,
            "reason": "JEV: none of the candidates is the same goal"}
