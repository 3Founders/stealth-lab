"""Goal-first routing: the task (Goal) is the routing key, not the tool.

A request is matched to a canonical Goal first (exact name, alias, full-text, optional vector),
then the Goal's live Procedures ("ways") and the tool cards mapped to that Goal are returned.
Tools are implementations; the Goal is what a person searches for.

The Goal index is sharded: `goal_search_index` / `goal_names` live on the control database and
the canonical Goal row lives on its home shard. This module uses the existing search path
(`app.services.goals.search_goals`, which hydrates from the home shard) and `shards.home_pool`
for the ways, so it works with the real deployment instead of assuming every Goal is on K000.

    from app.tool_routing.goal_router import route_goal
    hits = await route_goal(pool, "check what the page looks like after login")

Each hit is a GoalHit: goal fields, the ways (procedures) that achieve it, and the tool cards
mapped to it by `tool_card_goals` (db/186_tool_card_goals.sql).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.services.access import AccessScope
from app.services.goals import normalize_goal_name, search_goals
from app.services.shards import HOME_SHARD, ShardUnavailable, pools_for

RRF_K = 60
EXACT_SCORE = 1.0
ALIAS_SCORE = 0.95
DEFAULT_LIMIT = 5
MAX_WAYS_PER_GOAL = 5
MAX_TOOLS_PER_GOAL = 8


@dataclass
class ToolHit:
    card_id: str
    name: str
    server: str
    title: str
    description: str
    source: str
    score: float


@dataclass
class WayHit:
    procedure_id: str
    name: str
    steps: list[Any]
    preconditions: list[Any]
    verification_state: str
    availability: str


@dataclass
class GoalHit:
    goal_id: str
    canonical_name: str
    description: str
    status: str
    score: float
    matched_by: str
    home_shard: str
    ways: list[WayHit] = field(default_factory=list)
    tools: list[ToolHit] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


def _hit(row: Any, score: float, matched_by: str) -> dict[str, Any]:
    return {
        "goal_id": str(row["id"]),
        "canonical_name": str(row["canonical_name"] or ""),
        "description": str(row.get("description") or row.get("short_description") or ""),
        "status": str(row.get("status") or ""),
        "score": float(score),
        "matched_by": matched_by,
        "home_shard": str(row.get("home_shard_id") or HOME_SHARD),
    }


def _merge(out: dict[str, dict[str, Any]], row: Any, score: float, matched_by: str) -> None:
    hit = _hit(row, score, matched_by)
    current = out.get(hit["goal_id"])
    if current is None or hit["score"] > current["score"]:
        out[hit["goal_id"]] = hit
    elif current["matched_by"] == "fts" and matched_by == "vector":
        current["matched_by"] = "fts+vector"


async def _exact(pool: Any, normalized: str, limit: int) -> list[Any]:
    """Exact name from the cross-shard `goal_names` registry, hydrated from the home shard."""
    rows = await pool.fetch(
        """
        SELECT goal_id::text AS id, home_shard_id, canonical_name
        FROM goal_names
        WHERE normalize_goal_name(canonical_name) = $1
        LIMIT $2
        """,
        normalized, limit,
    )
    if not rows:
        return []
    pools = pools_for(pool)
    out: list[Any] = []
    for row in rows:
        shard = str(row["home_shard_id"] or HOME_SHARD)
        try:
            owner = pool if shard == HOME_SHARD else await pools.get(shard)
            goal = await owner.fetchrow(
                """
                SELECT id::text AS id, canonical_name, description, status, home_shard_id
                FROM goals
                WHERE id = $1::uuid AND t_invalid IS NULL
                """,
                row["id"],
            )
        except ShardUnavailable:
            goal = None
        if goal is not None:
            out.append(goal)
    return out


async def _search(pool: Any, query: str, limit: int, query_embedding: Optional[Sequence[float]],
                  scope: AccessScope) -> list[Any]:
    """The existing goal search: lexical + optional vector, hydrated from the home shard."""
    return await search_goals(
        pool,
        query_text=query,
        query_embedding=list(query_embedding) if query_embedding is not None else None,
        scope=scope,
        limit=limit,
        require_procedures=False,
    )


async def _k000_full_text(pool: Any, query: str, limit: int) -> list[Any]:
    """OR full-text over the Goals that live on the control database itself (K000).

    `search_goals` uses the global search group; a deployment without its members configured
    (a local checkout, or before the group is backfilled) still has K000 Goals, including the
    25 tool-goal taxonomy Goals, and this keeps them searchable. The canonical name is weighted
    above the description so "Search the web" outranks a longer Goal that only shares "search".
    """
    from app.services.identity_resolution import fts_or_query

    q = fts_or_query(query)
    if not q:
        return []
    return await pool.fetch(
        """
        WITH d AS (
            SELECT id, canonical_name, description, status, home_shard_id,
                   setweight(to_tsvector('english', canonical_name), 'A')
                   || setweight(to_tsvector('english', COALESCE(description, '')), 'B') AS doc
            FROM goals
            WHERE t_invalid IS NULL
              AND status IN ('active', 'candidate')
              AND home_shard_id = $3
        )
        SELECT id::text AS id, canonical_name, description, status, home_shard_id,
               ts_rank_cd(doc, to_tsquery('english', $1)) AS rank
        FROM d
        WHERE doc @@ to_tsquery('english', $1)
        ORDER BY rank DESC
        LIMIT $2
        """,
        q, limit, HOME_SHARD,
    )


async def _ways(pool: Any, hits: Sequence[dict[str, Any]]) -> dict[str, list[WayHit]]:
    if not hits:
        return {}
    pools = pools_for(pool)
    out: dict[str, list[WayHit]] = {}
    for hit in hits:
        shard = str(hit.get("home_shard") or HOME_SHARD)
        try:
            owner = pool if shard == HOME_SHARD else await pools.get(shard)
            rows = await owner.fetch(
                """
                SELECT id::text AS id, name, steps, preconditions,
                       verification_state::text AS verification_state,
                       availability::text AS availability
                FROM procedures
                WHERE achieves_goal_id = $1::uuid
                  AND t_invalid IS NULL
                  AND availability = 'active'
                ORDER BY t_created DESC
                LIMIT $2
                """,
                hit["goal_id"], MAX_WAYS_PER_GOAL,
            )
        except ShardUnavailable:
            rows = []
        out[hit["goal_id"]] = [
            WayHit(
                procedure_id=str(row["id"]),
                name=str(row["name"] or ""),
                steps=list(row["steps"] or []),
                preconditions=list(row["preconditions"] or []),
                verification_state=str(row["verification_state"] or ""),
                availability=str(row["availability"] or ""),
            )
            for row in rows
        ]
    return out


async def _tools(pool: Any, goal_ids: Sequence[str]) -> dict[str, list[ToolHit]]:
    if not goal_ids:
        return {}
    try:
        rows = await pool.fetch(
            """
            SELECT g.goal_id::text AS goal_id, t.id, t.name, t.server, t.title, t.description,
                   t.source, g.score
            FROM tool_card_goals g
            JOIN tool_cards t ON t.id = g.card_id
            WHERE g.goal_id = ANY($1::uuid[])
            ORDER BY g.score DESC, NULLIF(t.extra->>'stars', '')::int DESC NULLS LAST
            """,
            list(goal_ids),
        )
    except Exception:  # noqa: BLE001 -- an older database without db/186: goals still work
        return {}
    out: dict[str, list[ToolHit]] = {}
    for row in rows:
        bucket = out.setdefault(str(row["goal_id"]), [])
        if len(bucket) >= MAX_TOOLS_PER_GOAL:
            continue
        bucket.append(ToolHit(
            card_id=str(row["id"]),
            name=str(row["name"] or ""),
            server=str(row["server"] or ""),
            title=str(row["title"] or ""),
            description=str(row["description"] or ""),
            source=str(row["source"] or ""),
            score=float(row["score"] or 0.0),
        ))
    return out


async def route_goal(
    pool: Any,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    query_embedding: Optional[Sequence[float]] = None,
    scope: Optional[AccessScope] = None,
    include_ways: bool = True,
    include_tools: bool = True,
    explain: bool = False,
) -> list[GoalHit]:
    """Match a task to Goals, then attach their ways and mapped tools.

    `query_embedding` is optional: without it the router uses exact/alias + full-text only.
    Pass the same embedding model that populated `goal_search_index.embedding` (the search
    group records the model), or the vector leg will not be comparable.

    `explain=True` fills `GoalHit.trace` with one entry per leg that returned the Goal:
    `{leg, rank, score, name}`. It is for the recommend_goal CLI and costs nothing when off.
    """
    text = " ".join(str(query or "").split())[:2000]
    if not text:
        return []
    scope = scope or AccessScope.unrestricted()

    found: dict[str, dict[str, Any]] = {}
    traces: dict[str, list[dict[str, Any]]] = {}

    def note(row: Any, leg: str, rank: int, score: float) -> None:
        if explain:
            traces.setdefault(str(row["id"]), []).append({
                "leg": leg,
                "rank": rank,
                "score": float(score),
                "name": str(row["canonical_name"] or ""),
            })

    normalized = normalize_goal_name(text)
    for rank, row in enumerate(await _exact(pool, normalized, max(limit * 2, 10))):
        exact = normalize_goal_name(str(row["canonical_name"])) == normalized
        score = EXACT_SCORE if exact else ALIAS_SCORE
        _merge(found, row, score, "exact" if exact else "alias")
        note(row, "exact" if exact else "alias", rank, score)

    for rank, row in enumerate(await _k000_full_text(pool, text, max(limit * 4, 20))):
        score = 1.25 / (RRF_K + rank + 1)
        _merge(found, row, score, "fts")
        note(row, "k000_fts", rank, score)

    search_leg = "fts+vector" if query_embedding is not None else "fts"
    for rank, row in enumerate(await _search(pool, text, max(limit * 4, 20), query_embedding, scope)):
        score = 1.0 / (RRF_K + rank + 1)
        _merge(found, row, score, search_leg)
        note(row, search_leg, rank, score)

    ranked = sorted(found.values(), key=lambda h: (-h["score"], h["canonical_name"]))[: max(1, int(limit))]
    if not ranked:
        return []

    ways = await _ways(pool, ranked) if include_ways else {}
    tools = await _tools(pool, [h["goal_id"] for h in ranked]) if include_tools else {}

    return [
        GoalHit(
            goal_id=h["goal_id"],
            canonical_name=h["canonical_name"],
            description=h["description"],
            status=h["status"],
            score=h["score"],
            matched_by=h["matched_by"],
            home_shard=h["home_shard"],
            ways=ways.get(h["goal_id"], []),
            tools=tools.get(h["goal_id"], []),
            trace=traces.get(h["goal_id"], []) if explain else [],
        )
        for h in ranked
    ]
