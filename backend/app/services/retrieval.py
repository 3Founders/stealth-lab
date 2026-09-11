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
from app.services.access import AccessScope, TenantScope, scope_predicates
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

# TMS readability (Band 2.7): relate_claims() flips a superseded/contradicted
# claim's properties->>'truth_state' to 'OUT' while DELIBERATELY leaving
# t_invalid NULL (claims.py -- the row must stay queryable as history). So the
# bi-temporal `t_invalid IS NULL` filter cannot catch it, and until this
# predicate existed truth_state was write-only: a claim the system had stopped
# believing kept surfacing in every retrieval path as if nothing had happened.
#
# IS DISTINCT FROM, never <>: the truth_state KEY is absent on every non-claim
# knowledge_node (and anything written before claims.py existed), where
# properties->>'truth_state' is NULL. `NULL <> 'OUT'` evaluates to NULL, which
# fails WHERE -- using <> here would hide EVERY ordinary node and blank the
# whole graph. Only an explicit 'OUT' hides a row.
#
# Applied to knowledge_nodes query legs ONLY: task_nodes has no properties
# column at all (db/01_ontology.sql), so there is nothing to filter on --
# composing the fragment into a task_nodes leg would be a SQL error, not a
# no-op. Graph expansion needs it too, not just direct search: the SUPERSEDES/
# CONTRADICTS edge that recorded the invalidation links the OUT claim directly
# to its live successor, so expansion would otherwise reintroduce through that
# very edge exactly what search just excluded.
NOT_TRUTH_STATE_OUT = "(properties->>'truth_state' IS DISTINCT FROM 'OUT')"


def _belief_filter(table: str) -> str:
    """
    The TMS-readability WHERE fragment for one node table, or '' for tables
    that carry no belief state (task_nodes has no properties column to
    filter on). Callers splice the result in so that '' reproduces the
    pre-2.7 SQL byte-for-byte.
    """
    return NOT_TRUTH_STATE_OUT if table == "knowledge_nodes" else ""


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
        tenant_scope: Optional[TenantScope] = None,
    ):
        if embedding_column not in VALID_EMBEDDING_COLUMNS:
            raise ValueError(
                f"embedding_column must be one of {VALID_EMBEDDING_COLUMNS}, "
                f"got {embedding_column!r}"
            )
        self._pool = pool
        self._embedder = embedder or Embedder()
        self._scope = scope or AccessScope.unrestricted()
        # WAVE-3 tenancy adoption (cross-lane request #2): task_nodes and
        # knowledge_nodes are tenant-bearing, so every predicate build in
        # this class goes through scope_predicates() -- both axes, one
        # call. Default unrestricted() renders the visible literal TRUE.
        self._tenant_scope = tenant_scope or TenantScope.unrestricted()
        # The graph store inherits the same scopes -- retrieval that
        # filtered its entrypoints but then expanded through unscoped
        # traversal would leak exactly what the filter prevented.
        self._graph = GraphStore(
            pool, scope=self._scope, tenant_scope=self._tenant_scope
        )
        self._emb = embedding_column
        # embedding_joint only exists on task_nodes -- knowledge_nodes has no
        # alt column, so a caller asking for the joint embedding is
        # implicitly restricted there rather than erroring.
        self._tables = tuple(t for t in tables if t == "task_nodes") \
            if embedding_column != "embedding" else tables

    async def _vector_search(self, query_vec: list[float], limit: int) -> list[tuple[UUID, str, int]]:
        """Returns (id, table, rank). Only rank matters downstream, for RRF."""
        vec = to_pgvector(query_vec)
        scope_sql, scope_params, _ = scope_predicates(
            self._scope, self._tenant_scope, param_index=3
        )
        legs = []
        for table in self._tables:
            not_group = _NOT_A_HIERARCHY_GROUP.format(table=table)
            believed = "" if table == "task_nodes" else f"AND {NOT_TRUTH_STATE_OUT} "
            legs.append(
                f"SELECT id, '{table}' AS tbl, {self._emb} <=> $1::vector AS dist "
                f"FROM {table} "
                f"WHERE {self._emb} IS NOT NULL AND t_invalid IS NULL {believed}"
                f"AND {scope_sql} "
                f"AND {not_group}"
            )
        rows = await self._pool.fetch(
            f"""
            SELECT id, tbl FROM ({" UNION ALL ".join(legs)}) combined
            ORDER BY dist ASC LIMIT $2
            """,
            vec, limit, *scope_params,
        )
        return [(r["id"], r["tbl"], i) for i, r in enumerate(rows)]

    async def _lexical_search(self, query: str, limit: int) -> list[tuple[UUID, str, int]]:
        scope_sql, scope_params, _ = scope_predicates(
            self._scope, self._tenant_scope, param_index=3
        )
        legs = []
        if "task_nodes" in self._tables:
            not_group = _NOT_A_HIERARCHY_GROUP.format(table="task_nodes")
            legs.append(
                f"""
                SELECT id, 'task_nodes' AS tbl,
                       ts_rank(to_tsvector('english', name || ' ' || COALESCE(description,'')),
                               (SELECT q FROM parsed_query)) AS rank
                FROM task_nodes
                WHERE t_invalid IS NULL AND {scope_sql} AND {not_group}
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
                WHERE t_invalid IS NULL AND {NOT_TRUTH_STATE_OUT}
                  AND {scope_sql} AND {not_group}
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
            query, limit, *scope_params,
        )
        return [(r["id"], r["tbl"], i) for i, r in enumerate(rows)]

    async def retrieve(
        self,
        query: str,
        top_k: int = 6,
        expand_depth: int = 1,
        max_context_nodes: int = 25,
        query_vec: Optional[list[float]] = None,
        coarse_route_table: Optional[str] = None,
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

        `coarse_route_table`: B37's "coarse domain/topic routing" stage,
        opt-in and additive (default `None` -- every existing caller's
        behavior is byte-for-byte unchanged). When given (must be one of
        `self._tables`), calls `hierarchy.coarse_route` ONCE to find which
        top-level branch of that table's own hierarchy tree the query
        belongs to, then restricts THIS retrieve() call's candidate hits
        for that one table to real leaves under that branch, before RRF
        fusion -- the other table in `self._tables`, if any, is left
        unrestricted. `coarse_route` itself returns `None` (never a
        fabricated routing decision) when no real hierarchy exists yet
        for that table -- in that case this is a complete no-op, exactly
        today's flat behavior.
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

        if coarse_route_table is not None and coarse_route_table in self._tables:
            # B37 STRICT CLOSURE (index-staleness-safe narrowing):
            # `coarse_route_safe_exclusions` answers "which real leaves
            # are CONFIRMED to belong to a different branch than the
            # query matched" -- never "which leaves are IN the matched
            # branch". A real hierarchy built at some point in the past
            # is, by construction, potentially stale relative to rows
            # created since; excluding a hit only when it is positively
            # known to belong elsewhere keeps every hit the index simply
            # hasn't absorbed yet (a brand new row is never a member of
            # ANY branch, so it is never excluded) -- confirmed load-
            # bearing by test_relevant_claims_e2e.py's own "must find the
            # claim it was just given" contract against this table's real,
            # only-partially-clustered production corpus.
            from app.services.hierarchy import coarse_route_safe_exclusions
            excluded_ids = await coarse_route_safe_exclusions(
                self._pool, coarse_route_table, query,
                scope=self._scope, embedder=self._embedder, tenant_scope=self._tenant_scope,
            )
            if excluded_ids:
                excluded = {UUID(i) for i in excluded_ids}
                vector_hits = [
                    h for h in vector_hits if h[1] != coarse_route_table or h[0] not in excluded
                ]
                lexical_hits = [
                    h for h in lexical_hits if h[1] != coarse_route_table or h[0] not in excluded
                ]

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
            hydrate_sql, hydrate_params, _ = scope_predicates(
                self._scope, self._tenant_scope, param_index=2
            )
            belief = "" if table == "task_nodes" else f"AND {NOT_TRUTH_STATE_OUT} "
            row = await self._pool.fetchrow(
                f"SELECT id, name, {'description' if table == 'task_nodes' else 'NULL AS description'} "
                f"FROM {table} WHERE id = $1 {belief}AND {hydrate_sql}", node_id, *hydrate_params,
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
                    exp_sql, exp_params, _ = scope_predicates(
                        self._scope, self._tenant_scope, param_index=2
                    )
                    # Same belief filter as direct search -- without it the
                    # SUPERSEDES/CONTRADICTS edge itself would walk expansion
                    # straight back to the OUT claim search just excluded.
                    belief_frag = _belief_filter(ntable)
                    believed = f"AND {belief_frag} " if belief_frag else ""
                    row = await self._pool.fetchrow(
                        f"SELECT id, name, "
                        f"{'description' if ntable == 'task_nodes' else 'NULL AS description'} "
                        f"FROM {ntable} WHERE id = $1 AND t_invalid IS NULL {believed}"
                        f"AND {exp_sql} "
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
