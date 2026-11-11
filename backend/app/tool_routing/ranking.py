"""The materialized per-Goal tool ranking (db/200, docs/goal_router.md P3-03).

The P3-03 rank is a function of slow-moving evidence:

    trust_lower_bound + exploration_bonus - cost_penalty - latency_penalty - risk_penalty

Recomputing it on every request cost 6 queries per Goal (trust, lifecycle, trials, policies, listings,
latency). This module computes it once per Goal and stores the ordered rows; the router reads one row
per card. The only per-request part left is the trial overlay (a trial card appears inside its share),
which the router applies after the read.

    await refresh(pool)                       # every tool Goal
    await refresh(pool, goal="web")           # one Goal
    rows = await stored(pool, [goal_id, ...]) # {goal_id: [{card_id, rank, rank_score, inputs}]}

A Goal with no stored rows falls back to the live ranker in goal_router._rank.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

# A Goal's candidate pool: the same cap the live ranker uses.
CANDIDATES_PER_GOAL = 40


async def stored(pool: Any, goal_ids: list[str]) -> dict[str, list[dict]]:
    """{goal_id: ordered rows} for the given Goals; a Goal with no rows is absent."""
    if not goal_ids:
        return {}
    rows = await pool.fetch(
        """
        SELECT goal_id::text AS goal_id, card_id, rank, rank_score, inputs
        FROM goal_tool_rankings
        WHERE goal_id = ANY($1::uuid[])
        ORDER BY goal_id, rank
        """,
        list(goal_ids),
    )
    out: dict[str, list[dict]] = {}
    for row in rows:
        inputs = row["inputs"]
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs or "{}")
            except ValueError:
                inputs = {}
        out.setdefault(str(row["goal_id"]), []).append({
            "card_id": str(row["card_id"]),
            "rank": int(row["rank"]),
            "rank_score": float(row["rank_score"] or 0),
            "inputs": inputs or {},
        })
    return out


def tools(stored_rows: list[dict], candidates: list[Any], *, limit: Optional[int] = None) -> list[Any]:
    """Order the candidate ToolHits by the stored ranking and attach rank_score/rank_inputs.

    A stored card that is no longer in the candidate list (unmapped since the refresh) is skipped;
    a candidate without a stored row is left out of the ranked prefix.
    """
    from app.tool_routing.goal_router import MAX_TOOLS_PER_GOAL

    by_id = {c.card_id: c for c in candidates}
    out: list[Any] = []
    for row in stored_rows:
        card = by_id.get(str(row["card_id"]))
        if card is None:
            continue
        card.rank_score = float(row["rank_score"])
        card.rank_inputs = dict(row["inputs"] or {})
        out.append(card)
    return out[: (limit or MAX_TOOLS_PER_GOAL)]


async def refresh(pool: Any, *, goal: Optional[str] = None, trust_refresh: bool = True,
                  concurrency: int = 6) -> int:
    """Recompute and store the ranking for every tool Goal (or one). -> rows written.

    `goal` is a Goal uuid or a canonical-name substring. The trust view is refreshed first unless
    `trust_refresh=False`, so a refresh after new runs sees the new posterior.
    """
    from app.tool_routing import trust
    from app.tool_routing.goal_router import ToolHit, _rank

    if trust_refresh:
        try:
            await trust.refresh(pool)
        except Exception:  # noqa: BLE001 -- no view yet: rank with what is there
            pass

    goals = await pool.fetch(
        """
        SELECT id::text AS id
        FROM goals
        WHERE provenance = 'tool_goal_taxonomy' AND t_invalid IS NULL AND status = 'active'
          AND ($1::text IS NULL OR id::text = $1 OR canonical_name ILIKE '%' || $1 || '%')
        ORDER BY canonical_name
        """,
        goal,
    )
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(goal_id: str) -> int:
        async with sem:
            rows = await pool.fetch(
                """
                SELECT t.id, t.name, t.server, t.title, t.description, t.source, cg.score
                FROM tool_card_goals cg
                JOIN tool_cards t ON t.id = cg.card_id
                WHERE cg.goal_id = $1::uuid
                ORDER BY cg.score DESC, NULLIF(t.extra->>'stars', '')::int DESC NULLS LAST, t.id
                LIMIT $2
                """,
                goal_id, CANDIDATES_PER_GOAL,
            )
            cards = [
                ToolHit(card_id=str(r["id"]), name=str(r["name"] or ""), server=str(r["server"] or ""),
                        title=str(r["title"] or ""), description=str(r["description"] or ""),
                        source=str(r["source"] or ""), score=float(r["score"] or 0))
                for r in rows
            ]
            if not cards:
                return 0
            ranked = await _rank(pool, goal_id, cards, owner_id=None, request_id=None)
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM goal_tool_rankings WHERE goal_id = $1::uuid", goal_id)
                    await conn.executemany(
                        """
                        INSERT INTO goal_tool_rankings (goal_id, card_id, rank, rank_score, inputs, ranked_at)
                        VALUES ($1::uuid, $2, $3, $4, $5::jsonb, now())
                        """,
                        [(goal_id, c.card_id, i + 1, float(c.rank_score or 0),
                          json.dumps(c.rank_inputs or {})) for i, c in enumerate(ranked)],
                    )
            return len(ranked)

    counts = await asyncio.gather(*(one(str(g["id"])) for g in goals))
    return sum(counts)
