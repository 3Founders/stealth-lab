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
from typing import Optional
from uuid import UUID

import asyncpg

from app.db.graph_store import GraphStore
from app.services.access import AccessScope, visibility_predicate
from app.services.embeddings import Embedder, to_pgvector

log = logging.getLogger(__name__)

# RRF's smoothing constant. 60 is the value from the original RRF paper
# and the common default; it damps the dominance of rank-1 hits.
RRF_K = 60

# A hierarchy-group node (e.g. a "collection of related instances" parent)
# exists to organize other nodes, not to be retrieved itself -- it has no
# instance of its own, so surfacing it as a hit is always a dead end for the
# caller. PARENT_OF is the custom edge type that marks that grouping
# relationship; excluding any node that is the SOURCE of a live PARENT_OF
# edge keeps group nodes out of both direct search and graph expansion.
_NOT_A_HIERARCHY_GROUP = (
    "NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL "
    "AND e.edge_type = 'OWNS' AND e.custom_edge_type = 'PARENT_OF' "
    "AND e.source_id = {table}.id AND e.source_table = '{table}')"
)


@dataclass
class RetrievedNode:
    id: UUID
    table: str
    name: str
    description: Optional[str]
    score: float
    matched_by: list[str] = field(default_factory=list)
    hops: int = 0  # 0 = matched directly, >0 = pulled in by graph expansion


@dataclass
class RetrievalResult:
    nodes: list[RetrievedNode]
    entrypoint_ids: list[UUID]

    def as_context(self) -> str:
        """Render for an LLM prompt, with ids so answers can cite them."""
        lines = []
        for n in self.nodes:
            kind = "task" if n.table == "task_nodes" else "knowledge"
            line = f"[{kind}:{n.id}] {n.name}"
            if n.description:
                line += f" — {n.description}"
            if n.hops > 0:
                line += f" (related, {n.hops} hop{'s' if n.hops > 1 else ''} away)"
            lines.append(line)
        return "\n".join(lines)


VALID_EMBEDDING_COLUMNS: tuple[str, ...] = ("embedding", "embedding_joint")
"""
The real, single source of truth for what embedding_column may be. Hoisted
out of HybridRetriever.__init__'s inline tuple literal (ticket 17,
memory-substrate map): the CI drift check (tests/test_schema_drift.py)
imports this exact constant rather than re-hardcoding the list a second
time, which would recreate the drift risk this check exists to catch.
"""


def fuse_rrf(
    ranked_lists: list[tuple[list[tuple[UUID, str, int]], str]],
    k: int = RRF_K,
) -> tuple[dict[tuple[UUID, str], float], dict[tuple[UUID, str], list[str]]]:
    """
    Pure Reciprocal Rank Fusion -- no I/O, extracted from retrieve()'s
    body (ticket 14: "the component is load-bearing with zero coverage
    ... property-based tests land before any extension") so the fusion
    arithmetic itself is directly testable against real invariants
    (monotonicity, idempotence, commutativity, stability -- see
    tests/test_retrieval_rrf_properties.py) without needing a live
    database. `retrieve()` below is now a thin caller of this function;
    behavior is unchanged, only factored so it can be tested at all.

    `ranked_lists`: each element is (hits, label) where hits is a list
    of (node_id, table, rank) -- rank is 0-based position within that
    one ranked list, exactly what _vector_search/_lexical_search already
    return. `label` tags which signal contributed (e.g. "semantic",
    "keyword"), carried through into the returned `matched` dict for
    RetrievedNode.matched_by.
    """
    scores: dict[tuple[UUID, str], float] = {}
    matched: dict[tuple[UUID, str], list[str]] = {}
    for hits, label in ranked_lists:
        for node_id, table, rank in hits:
            key = (node_id, table)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            matched.setdefault(key, []).append(label)
    return scores, matched


class HybridRetriever:
    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: Optional[Embedder] = None,
        scope: Optional[AccessScope] = None,
        embedding_column: str = "embedding",
        tables: tuple[str, ...] = ("task_nodes", "knowledge_nodes"),
    ):
        if embedding_column not in VALID_EMBEDDING_COLUMNS:
            raise ValueError(
                f"embedding_column must be one of {VALID_EMBEDDING_COLUMNS}, "
                f"got {embedding_column!r}"
            )
        self._pool = pool
        self._embedder = embedder or Embedder()
        self._scope = scope or AccessScope.unrestricted()
        # The graph store inherits the same scope -- retrieval that
        # filtered its entrypoints but then expanded through unscoped
        # traversal would leak exactly what the filter prevented.
        self._graph = GraphStore(pool, scope=self._scope)
        self._emb = embedding_column
        # embedding_joint only exists on task_nodes -- knowledge_nodes has no
        # alt column, so a caller asking for the joint embedding is
        # implicitly restricted there rather than erroring.
        self._tables = tuple(t for t in tables if t == "task_nodes") \
            if embedding_column != "embedding" else tables

    async def _vector_search(self, query_vec: list[float], limit: int) -> list[tuple[UUID, str, int]]:
        """Returns (id, table, rank). Only rank matters downstream, for RRF."""
        vec = to_pgvector(query_vec)
        vis_sql, vis_params = visibility_predicate(self._scope, param_index=3)
        legs = []
        for table in self._tables:
            not_group = _NOT_A_HIERARCHY_GROUP.format(table=table)
            legs.append(
                f"SELECT id, '{table}' AS tbl, {self._emb} <=> $1::vector AS dist "
                f"FROM {table} "
                f"WHERE {self._emb} IS NOT NULL AND t_invalid IS NULL AND {vis_sql} "
                f"AND {not_group}"
            )
        rows = await self._pool.fetch(
            f"""
            SELECT id, tbl FROM ({" UNION ALL ".join(legs)}) combined
            ORDER BY dist ASC LIMIT $2
            """,
            vec, limit, *vis_params,
        )
        return [(r["id"], r["tbl"], i) for i, r in enumerate(rows)]

    async def _lexical_search(self, query: str, limit: int) -> list[tuple[UUID, str, int]]:
        vis_sql, vis_params = visibility_predicate(self._scope, param_index=3)
        legs = []
        if "task_nodes" in self._tables:
            not_group = _NOT_A_HIERARCHY_GROUP.format(table="task_nodes")
            legs.append(
                f"""
                SELECT id, 'task_nodes' AS tbl,
                       ts_rank(to_tsvector('english', name || ' ' || COALESCE(description,'')),
                               (SELECT q FROM parsed_query)) AS rank
                FROM task_nodes
                WHERE t_invalid IS NULL AND {vis_sql} AND {not_group}
                  AND to_tsvector('english', name || ' ' || COALESCE(description,''))
                      @@ (SELECT q FROM parsed_query)
                """
            )
        if "knowledge_nodes" in self._tables:
            not_group = _NOT_A_HIERARCHY_GROUP.format(table="knowledge_nodes")
            legs.append(
                f"""
                SELECT id, 'knowledge_nodes' AS tbl,
                       ts_rank(to_tsvector('english', name), (SELECT q FROM parsed_query)) AS rank
                FROM knowledge_nodes
                WHERE t_invalid IS NULL AND {vis_sql} AND {not_group}
                  AND to_tsvector('english', name) @@ (SELECT q FROM parsed_query)
                """
            )
        rows = await self._pool.fetch(
            f"""
            WITH parsed_query AS (
                -- plainto_tsquery ANDs every word together ('extract' &
                -- 'step' & 'depend'), which requires ALL of a question's
                -- words to appear in the target text. That's nearly
                -- impossible for a natural-language question against a
                -- short node name -- verified live: this was returning
                -- zero matches even for an exact topical word overlap,
                -- because the two filler words in the question weren't
                -- also present. Rebuilding the same (already-stemmed,
                -- already-normalized) terms with OR instead of AND is
                -- the fix: match on ANY shared meaningful word.
                SELECT to_tsquery(
                    'english',
                    regexp_replace(plainto_tsquery('english', $1)::text, ' & ', ' | ', 'g')
                ) AS q
            )
            SELECT id, tbl FROM ({" UNION ALL ".join(legs)}) combined
            ORDER BY rank DESC LIMIT $2
            """,
            query, limit, *vis_params,
        )
        return [(r["id"], r["tbl"], i) for i, r in enumerate(rows)]

    async def retrieve(
        self,
        query: str,
        top_k: int = 6,
        expand_depth: int = 1,
        max_context_nodes: int = 25,
        query_vec: Optional[list[float]] = None,
    ) -> RetrievalResult:
        """
        Hybrid entrypoints, then bounded graph expansion.

        `expand_depth` defaults to 1, not 2: expansion exists to pull in
        directly-relevant neighbours, and at depth 2 a well-connected node
        drags in most of the graph, diluting the context rather than
        enriching it.

        `query_vec`: pass an already-computed embedding to skip embedding
        `query` again. decompose() embeds the problem text once and threads
        it through every reuse-check call site that would otherwise embed
        the identical string independently -- see decomposition.py.
        """
        vector_hits: list[tuple[UUID, str, int]] = []
        try:
            if query_vec is None:
                query_vec = await self._embedder.embed_one(query, input_type="query")
            vector_hits = await self._vector_search(query_vec, top_k * 2)
        except Exception as exc:  # noqa: BLE001
            # Degrade to lexical-only rather than failing the whole query.
            # Logged loudly because silently-halved retrieval quality is
            # worse than an error nobody sees.
            log.error("vector search unavailable, falling back to lexical only: %s", exc)

        lexical_hits = await self._lexical_search(query, top_k * 2)

        # Reciprocal Rank Fusion -- pure arithmetic, see fuse_rrf() above.
        scores, matched = fuse_rrf(
            [(vector_hits, "semantic"), (lexical_hits, "keyword")]
        )

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        if not ranked:
            return RetrievalResult(nodes=[], entrypoint_ids=[])

        entrypoints = [(node_id, table) for (node_id, table), _ in ranked]
        found: dict[UUID, RetrievedNode] = {}

        for (node_id, table), score in ranked:
            hydrate_sql, hydrate_params = visibility_predicate(self._scope, param_index=2)
            row = await self._pool.fetchrow(
                f"SELECT id, name, {'description' if table == 'task_nodes' else 'NULL AS description'} "
                f"FROM {table} WHERE id = $1 AND {hydrate_sql}", node_id, *hydrate_params,
            )
            if row:
                found[node_id] = RetrievedNode(
                    id=row["id"], table=table, name=row["name"],
                    description=row["description"], score=score,
                    matched_by=matched[(node_id, table)], hops=0,
                )

        # Expand outward from entrypoints -- the same traversal the graph
        # visualization uses, so both features stay consistent.
        for table in ("task_nodes", "knowledge_nodes"):
            ids = [nid for nid, t in entrypoints if t == table]
            if not ids:
                continue
            edges = await self._graph.traverse_from(ids, table, max_depth=expand_depth)
            for e in edges:
                # A PARENT_OF edge marks a hierarchy-group relationship, not
                # a topical relation -- riding it during expansion would pull
                # in every sibling under the same group node regardless of
                # relevance. Direct search already excludes group nodes
                # themselves (_NOT_A_HIERARCHY_GROUP above); this stops
                # expansion from reintroducing them, or their other
                # children, through the back door.
                if e.custom_edge_type == "PARENT_OF":
                    continue
                for nid, ntable in ((e.source_id, e.source_table), (e.target_id, e.target_table)):
                    if nid in found or len(found) >= max_context_nodes:
                        continue
                    not_group = _NOT_A_HIERARCHY_GROUP.format(table=ntable)
                    exp_sql, exp_params = visibility_predicate(self._scope, param_index=2)
                    row = await self._pool.fetchrow(
                        f"SELECT id, name, "
                        f"{'description' if ntable == 'task_nodes' else 'NULL AS description'} "
                        f"FROM {ntable} WHERE id = $1 AND t_invalid IS NULL AND {exp_sql} "
                        f"AND {not_group}",
                        nid, *exp_params,
                    )
                    if row:
                        found[nid] = RetrievedNode(
                            id=row["id"], table=ntable, name=row["name"],
                            description=row["description"], score=0.0,
                            matched_by=["graph"], hops=1,
                        )

        nodes = sorted(found.values(), key=lambda n: (n.hops, -n.score))
        return RetrievalResult(nodes=nodes, entrypoint_ids=[nid for nid, _ in entrypoints])
