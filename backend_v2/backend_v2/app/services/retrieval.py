"""
Hybrid retrieval (V1 item #3), implementing the pattern the original
architecture specified but never built: hybrid entrypoint selection
(embedding + lexical) followed by graph traversal outward -- never an
exhaustive graph walk.

Why hybrid rather than pure vector: embeddings match on meaning but miss
exact identifiers. A query naming a specific policy or tool by name is
better served by lexical match; a query describing a problem in the
user's own words is better served by vectors. Neither alone covers both.

Fusion uses Reciprocal Rank Fusion (RRF) rather than a weighted sum of
raw scores. Cosine similarity and ts_rank are on incomparable scales, so
summing them requires an arbitrary normalization that quietly changes
behavior as either distribution shifts. RRF only uses rank position, so
it's robust to that entirely.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID

import asyncpg

from app.config import settings
from app.db.graph_store import GraphStore
from app.services.access import AccessScope, visibility_predicate
from app.services.embeddings import Embedder, to_pgvector

log = logging.getLogger(__name__)

# RRF's smoothing constant. 60 is the value from the original RRF paper
# and the common default; it damps the dominance of rank-1 hits.
RRF_K = 60


@dataclass
class RetrievedNode:
    id: UUID
    table: str
    name: str
    description: Optional[str]
    score: float
    matched_by: list[str] = field(default_factory=list)
    hops: int = 0  # 0 = matched directly, >0 = pulled in by graph expansion
    t_valid: Optional[datetime] = None
    t_invalid: Optional[datetime] = None


@dataclass
class RetrievalResult:
    nodes: list[RetrievedNode]
    entrypoint_ids: list[UUID]

    def as_context(self) -> str:
        """
        Render for an LLM prompt, with ids so answers can cite them.

        Validity dates included: without them the graph is bi-temporal but
        the model can't answer "when did this change".
        """
        lines = []
        for n in self.nodes:
            kind = "task" if n.table == "task_nodes" else "knowledge"
            line = f"[{kind}:{n.id}] {n.name}"
            if n.description:
                line += f" — {n.description}"
            if n.t_valid:
                line += f" (in effect since {n.t_valid.date().isoformat()}"
                if n.t_invalid:
                    line += f", superseded {n.t_invalid.date().isoformat()}"
                line += ")"
            if n.hops > 0:
                line += f" (related, {n.hops} hop{'s' if n.hops > 1 else ''} away)"
            lines.append(line)
        return "\n".join(lines)


class HybridRetriever:
    _STRICT_TSQUERY = "plainto_tsquery('english', $1)"
    # Same lexemes ORed. Rewrites plainto_tsquery's own quoted output, so
    # user text never reaches to_tsquery unescaped. NULLIF guards the
    # all-stopword case, where to_tsquery('') would raise.
    _LOOSE_TSQUERY = (
        "to_tsquery('english', NULLIF(regexp_replace("
        "plainto_tsquery('english', $1)::text, ' & ', ' | ', 'g'), ''))"
    )

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: Optional[Embedder] = None,
        scope: Optional[AccessScope] = None,
    ):
        self._pool = pool
        self._embedder = embedder or Embedder()
        self._scope = scope or AccessScope.unrestricted()
        # The graph store inherits the same scope -- retrieval that
        # filtered its entrypoints but then expanded through unscoped
        # traversal would leak exactly what the filter prevented.
        self._graph = GraphStore(pool, scope=self._scope)

    async def _vector_search(self, query_vec: list[float], limit: int) -> list[tuple[UUID, str, int]]:
        """
        Returns (id, table, rank). Only rank matters downstream, for RRF.

        SET LOCAL in an explicit transaction because the database-level
        default only applies at backend startup, and a pooler's backends
        outlive it -- measured returning the stale value on 2 of 3 acquires.
        """
        vis_sql, vis_params = visibility_predicate(self._scope, param_index=3)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")
            rows = await conn.fetch(
                f"""
                SELECT id, tbl FROM (
                    SELECT id, 'task_nodes' AS tbl, embedding <=> $1::vector AS dist
                    FROM task_nodes
                    WHERE embedding IS NOT NULL AND t_invalid IS NULL AND {vis_sql}
                    UNION ALL
                    SELECT id, 'knowledge_nodes' AS tbl, embedding <=> $1::vector AS dist
                    FROM knowledge_nodes
                    WHERE embedding IS NOT NULL AND t_invalid IS NULL AND {vis_sql}
                ) combined
                ORDER BY dist ASC LIMIT $2
                """,
                to_pgvector(query_vec), limit, *vis_params,
            )
        return [(r["id"], r["tbl"], i) for i, r in enumerate(rows)]

    async def _run_lexical(
        self, query: str, limit: int, tsquery: str
    ) -> list[asyncpg.Record]:
        vis_sql, vis_params = visibility_predicate(self._scope, param_index=3)
        return await self._pool.fetch(
            f"""
            SELECT id, tbl FROM (
                SELECT id, 'task_nodes' AS tbl,
                       ts_rank(to_tsvector('english', name || ' ' || COALESCE(description,'')),
                               {tsquery}) AS rank
                FROM task_nodes
                WHERE t_invalid IS NULL AND {vis_sql}
                  AND to_tsvector('english', name || ' ' || COALESCE(description,''))
                      @@ {tsquery}
                UNION ALL
                SELECT id, 'knowledge_nodes' AS tbl,
                       ts_rank(to_tsvector('english', name), {tsquery}) AS rank
                FROM knowledge_nodes
                WHERE t_invalid IS NULL AND {vis_sql}
                  AND to_tsvector('english', name) @@ {tsquery}
            ) combined
            ORDER BY rank DESC LIMIT $2
            """,
            query, limit, *vis_params,
        )

    async def _lexical_search(self, query: str, limit: int) -> list[tuple[UUID, str, int]]:
        """
        AND first, then retry ORed.

        plainto_tsquery ANDs every term, so "what does the extraction step
        depend on?" needs all of 'extract' & 'step' & 'depend' in one node
        and matches nothing. Right precision when vectors cover the fuzzy
        half; a hard zero when embeddings are missing.
        """
        rows = await self._run_lexical(query, limit, self._STRICT_TSQUERY)
        if not rows:
            rows = await self._run_lexical(query, limit, self._LOOSE_TSQUERY)
            if rows:
                log.info("lexical AND empty for %r; OR matched %d", query, len(rows))
        return [(r["id"], r["tbl"], i) for i, r in enumerate(rows)]

    async def retrieve(
        self,
        query: str,
        top_k: int = 6,
        expand_depth: int = 1,
        max_context_nodes: int = 25,
    ) -> RetrievalResult:
        """
        Hybrid entrypoints, then bounded graph expansion.

        `expand_depth` defaults to 1, not 2: expansion exists to pull in
        directly-relevant neighbours, and at depth 2 a well-connected node
        drags in most of the graph, diluting the context rather than
        enriching it.
        """
        vector_hits: list[tuple[UUID, str, int]] = []
        try:
            query_vec = await self._embedder.embed_one(query, input_type="query")
            vector_hits = await self._vector_search(query_vec, top_k * 2)
        except Exception as exc:  # noqa: BLE001
            # Degrade to lexical-only rather than failing the whole query.
            # Logged loudly because silently-halved retrieval quality is
            # worse than an error nobody sees.
            log.error("vector search unavailable, falling back to lexical only: %s", exc)

        lexical_hits = await self._lexical_search(query, top_k * 2)

        # Reciprocal Rank Fusion
        scores: dict[tuple[UUID, str], float] = {}
        matched: dict[tuple[UUID, str], list[str]] = {}
        for hits, label in ((vector_hits, "semantic"), (lexical_hits, "keyword")):
            for node_id, table, rank in hits:
                key = (node_id, table)
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
                matched.setdefault(key, []).append(label)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        if not ranked:
            return RetrievalResult(nodes=[], entrypoint_ids=[])

        entrypoints = [(node_id, table) for (node_id, table), _ in ranked]
        found: dict[UUID, RetrievedNode] = {}

        # One query per table, not one per node: the previous shape was
        # ~30 serial round trips per question.
        for table in ("task_nodes", "knowledge_nodes"):
            ids = [nid for nid, t in entrypoints if t == table]
            for node_id, row in (await self._fetch_nodes(table, ids)).items():
                found[node_id] = RetrievedNode(
                    id=row["id"], table=table, name=row["name"],
                    description=row["description"], score=scores[(node_id, table)],
                    matched_by=matched[(node_id, table)], hops=0,
                    t_valid=row["t_valid"], t_invalid=row["t_invalid"],
                )

        # Expand outward from entrypoints -- the same traversal the graph
        # visualization uses, so both features stay consistent.
        neighbours: dict[str, set[UUID]] = {"task_nodes": set(), "knowledge_nodes": set()}
        for table in ("task_nodes", "knowledge_nodes"):
            ids = [nid for nid, t in entrypoints if t == table]
            if not ids:
                continue
            edges = await self._graph.traverse_from(ids, table, max_depth=expand_depth)
            for e in edges:
                for nid, ntable in ((e.source_id, e.source_table), (e.target_id, e.target_table)):
                    if nid not in found:
                        neighbours[ntable].add(nid)

        # Cap applied before hydrating, so survivors don't depend on edge
        # iteration order.
        budget = max_context_nodes - len(found)
        for ntable, ids in neighbours.items():
            if budget <= 0:
                break
            selected = list(ids)[:budget]
            for node_id, row in (await self._fetch_nodes(ntable, selected)).items():
                found[node_id] = RetrievedNode(
                    id=row["id"], table=ntable, name=row["name"],
                    description=row["description"], score=0.0,
                    matched_by=["graph"], hops=1,
                    t_valid=row["t_valid"], t_invalid=row["t_invalid"],
                )
            budget = max_context_nodes - len(found)

        nodes = sorted(found.values(), key=lambda n: (n.hops, -n.score))
        return RetrievalResult(nodes=nodes, entrypoint_ids=[nid for nid, _ in entrypoints])

    async def _fetch_nodes(
        self, table: str, ids: list[UUID]
    ) -> dict[UUID, asyncpg.Record]:
        """Hydrate many nodes of one table in a single round trip."""
        if not ids:
            return {}
        vis_sql, vis_params = visibility_predicate(self._scope, param_index=2)
        description = "description" if table == "task_nodes" else "NULL AS description"
        rows = await self._pool.fetch(
            f"SELECT id, name, {description}, t_valid, t_invalid FROM {table} "
            f"WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL AND {vis_sql}",
            ids, *vis_params,
        )
        return {r["id"]: r for r in rows}
