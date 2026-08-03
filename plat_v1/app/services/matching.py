"""
Hybrid retrieval over task nodes.

Vector search plus Postgres full-text, fused with Reciprocal Rank Fusion.
RRF rather than a weighted sum of raw scores because cosine distance and
ts_rank live on incomparable scales -- summing them needs an arbitrary
normalisation that silently changes behaviour as either distribution shifts.
RRF only reads rank position, so it is immune to that.

Every read filters on `t_invalid IS NULL`, and the indexes backing these
queries are partial on the same predicate. Both halves are needed: the filter
gives correct results, the partial index keeps superseded rows out of the
HNSW proximity graph so recall doesn't decay as versions accumulate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import asyncpg

from app.config import settings
from app.models.task import TaskNode
from app.services.embeddings import Embedder, to_pgvector

log = logging.getLogger(__name__)

# The constant from the original RRF paper, and the usual default. Damps the
# dominance of a single rank-1 hit.
RRF_K = 60


@dataclass
class Match:
    task: TaskNode
    score: float
    matched_by: list[str] = field(default_factory=list)


class TaskMatcher:
    _STRICT_TSQUERY = "plainto_tsquery('english', $1)"
    # The same lexemes ORed. Rewrites plainto_tsquery's own quoted output, so
    # caller text never reaches to_tsquery unescaped. NULLIF guards the
    # all-stopwords case, where to_tsquery('') raises.
    _LOOSE_TSQUERY = (
        "to_tsquery('english', NULLIF(regexp_replace("
        "plainto_tsquery('english', $1)::text, ' & ', ' | ', 'g'), ''))"
    )

    def __init__(self, pool: asyncpg.Pool, embedder: Optional[Embedder] = None):
        self._pool = pool
        self._embedder = embedder or Embedder()

    async def _vector_search(self, query: str, limit: int) -> list[tuple[UUID, int]]:
        vector = await self._embedder.embed_one(query, input_type="query")
        # SET LOCAL inside an explicit transaction: behind a connection pooler
        # the database-level default applies at backend startup and the
        # backends outlive it, so a session-level SET reaches whichever
        # backend happened to receive it and no others.
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")
            rows = await conn.fetch(
                """
                SELECT id FROM task_nodes
                WHERE embedding IS NOT NULL AND t_invalid IS NULL
                ORDER BY embedding <=> $1::vector ASC
                LIMIT $2
                """,
                to_pgvector(vector),
                limit,
            )
        return [(r["id"], i) for i, r in enumerate(rows)]

    async def _run_lexical(self, query: str, limit: int, tsquery: str) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            f"""
            SELECT id,
                   ts_rank(to_tsvector('english', name || ' ' || COALESCE(description,'')),
                           {tsquery}) AS rank
            FROM task_nodes
            WHERE t_invalid IS NULL
              AND to_tsvector('english', name || ' ' || COALESCE(description,'')) @@ {tsquery}
            ORDER BY rank DESC
            LIMIT $2
            """,
            query,
            limit,
        )

    async def _lexical_search(self, query: str, limit: int) -> list[tuple[UUID, int]]:
        """
        AND first, then retry ORed.

        plainto_tsquery ANDs every term, so "turn these invoices into a
        spreadsheet" needs every one of those lexemes in a single task node
        and matches nothing. Right precision when vectors cover the fuzzy
        half; a hard zero when embeddings are unavailable.
        """
        rows = await self._run_lexical(query, limit, self._STRICT_TSQUERY)
        if not rows:
            rows = await self._run_lexical(query, limit, self._LOOSE_TSQUERY)
            if rows:
                log.info("lexical AND empty for %r; OR matched %d", query, len(rows))
        return [(r["id"], i) for i, r in enumerate(rows)]

    async def search(self, query: str, top_k: int = 5) -> list[Match]:
        vector_hits: list[tuple[UUID, int]] = []
        try:
            vector_hits = await self._vector_search(query, top_k * 2)
        except Exception as exc:  # noqa: BLE001
            # Degrade to lexical rather than failing the request. Logged at
            # error because silently halved retrieval quality is worse than a
            # visible failure -- it just looks like the graph is empty.
            log.error("vector search unavailable, falling back to lexical only: %s", exc)

        lexical_hits = await self._lexical_search(query, top_k * 2)

        scores: dict[UUID, float] = {}
        matched: dict[UUID, list[str]] = {}
        for hits, label in ((vector_hits, "semantic"), (lexical_hits, "keyword")):
            for task_id, rank in hits:
                scores[task_id] = scores.get(task_id, 0.0) + 1.0 / (RRF_K + rank + 1)
                matched.setdefault(task_id, []).append(label)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        if not ranked:
            return []

        # One query for every hit, not one per hit. The equivalent path in
        # backend_v2 originally made ~31 serial round trips per question.
        tasks = await self.hydrate([task_id for task_id, _ in ranked])
        return [
            Match(task=tasks[task_id], score=score, matched_by=matched[task_id])
            for task_id, score in ranked
            if task_id in tasks
        ]

    async def hydrate(self, ids: list[UUID]) -> dict[UUID, TaskNode]:
        if not ids:
            return {}
        rows = await self._pool.fetch(
            """
            SELECT id, name, description, kind, input_schema, output_schema,
                   success_criteria, cache_key, version, provenance, t_valid
            FROM task_nodes
            WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL
            """,
            ids,
        )
        return {r["id"]: TaskNode.from_row(r) for r in rows}
