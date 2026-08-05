"""
Agent Store search (AGENT_STORE_PLAN.md, stage 3).

Generalizes the same lexical + vector + RRF-fusion pattern already
proven in HybridRetriever, pointed at `agents` instead of `task_nodes`/
`knowledge_nodes`. Kept as its own function rather than bolted onto
HybridRetriever directly: an agent search result is a different shape
(name, source, runnable, review_state) from a graph-context result, and
conflating the two return types would make both callers worse.

Only `review_state = 'approved'` agents are ever returned. An
`ingested` or `rejected` agent surfacing in a public search would defeat
the entire point of the review gate built in stages 1 and 2.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from app.services.access import AccessScope, visibility_predicate
from app.services.embeddings import Embedder, to_pgvector

log = logging.getLogger(__name__)

RRF_K = 60  # same constant, same reasoning as HybridRetriever


@dataclass
class AgentSearchResult:
    id: UUID
    name: str
    description: str
    source: str
    execution_mode: str
    runnable: bool


async def _vector_search(
    pool: asyncpg.Pool, query_vec: list[float], scope: AccessScope, limit: int
) -> list[tuple[UUID, int]]:
    """Returns (id, rank). Only rank matters downstream, for RRF."""
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)
    vec = to_pgvector(query_vec)
    rows = await pool.fetch(
        f"SELECT id FROM agents "
        f"WHERE review_state = 'approved' AND t_invalid IS NULL AND {vis_sql} "
        f"AND embedding IS NOT NULL "
        f"ORDER BY embedding <=> $1::vector ASC LIMIT $2",
        vec, limit, *vis_params,
    )
    return [(r["id"], i) for i, r in enumerate(rows)]


async def _lexical_search(
    pool: asyncpg.Pool, query: str, scope: AccessScope, limit: int
) -> list[tuple[UUID, int]]:
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)
    rows = await pool.fetch(
        f"""
        WITH parsed_query AS (
            -- Same fix as HybridRetriever's lexical search: plainto_tsquery
            -- ANDs every word together, which fails almost any real
            -- multi-word search. Rebuilt as OR, same reasoning, same fix,
            -- verified once already for this exact failure mode.
            SELECT to_tsquery(
                'english',
                regexp_replace(plainto_tsquery('english', $1)::text, ' & ', ' | ', 'g')
            ) AS q
        )
        SELECT id FROM agents
        WHERE review_state = 'approved' AND t_invalid IS NULL AND {vis_sql}
          AND to_tsvector('english', name || ' ' || description)
              @@ (SELECT q FROM parsed_query)
        ORDER BY ts_rank(to_tsvector('english', name || ' ' || description),
                          (SELECT q FROM parsed_query)) DESC
        LIMIT $2
        """,
        query, limit, *vis_params,
    )
    return [(r["id"], i) for i, r in enumerate(rows)]


async def search_agents(
    pool: asyncpg.Pool,
    query: Optional[str] = None,
    scope: Optional[AccessScope] = None,
    limit: int = 20,
    embedder: Optional[Embedder] = None,
) -> list[AgentSearchResult]:
    """
    `query=None` (or empty) returns the browse listing, newest first --
    the "browse" half of "search/browse UI". A real query fuses vector
    and lexical entrypoints via Reciprocal Rank Fusion, same reasoning
    as HybridRetriever: cosine distance and ts_rank live on incomparable
    scales, so only rank position is combined, never the raw scores.

    Vector search degrades gracefully, not silently: an embedding
    failure (no key configured, provider outage) falls back to
    lexical-only and is logged, matching HybridRetriever's own behavior,
    since a public search endpoint failing outright because one signal
    is unavailable would be a worse outcome than a slightly weaker
    ranking.
    """
    scope = scope or AccessScope.anonymous()

    if not query or not query.strip():
        vis_sql, vis_params = visibility_predicate(scope, param_index=1)
        rows = await pool.fetch(
            f"SELECT id, name, description, source::text AS source, "
            f"execution_mode::text AS execution_mode, runnable "
            f"FROM agents "
            f"WHERE review_state = 'approved' AND t_invalid IS NULL AND {vis_sql} "
            f"ORDER BY t_created DESC LIMIT ${len(vis_params) + 1}",
            *vis_params, limit,
        )
        return [AgentSearchResult(**dict(r)) for r in rows]

    vector_hits: list[tuple[UUID, int]] = []
    try:
        embedder = embedder or Embedder()
        query_vec = await embedder.embed_one(query, input_type="query")
        vector_hits = await _vector_search(pool, query_vec, scope, limit * 2)
    except Exception:  # noqa: BLE001
        log.error(
            "agent vector search unavailable, falling back to lexical only", exc_info=True
        )

    lexical_hits = await _lexical_search(pool, query, scope, limit * 2)

    scores: dict[UUID, float] = {}
    for hits in (vector_hits, lexical_hits):
        for agent_id, rank in hits:
            scores[agent_id] = scores.get(agent_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked_ids = [aid for aid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:limit]
    if not ranked_ids:
        return []

    rows = await pool.fetch(
        "SELECT id, name, description, source::text AS source, "
        "execution_mode::text AS execution_mode, runnable "
        "FROM agents WHERE id = ANY($1::uuid[])",
        ranked_ids,
    )
    by_id = {r["id"]: AgentSearchResult(**dict(r)) for r in rows}
    return [by_id[aid] for aid in ranked_ids if aid in by_id]
