"""
MCP hardening B37: hierarchical retrieval indexes and index-freshness,
made real over `app/services/hierarchy.py`'s own pre-existing tree
primitive (`build_hierarchy_for_table`/`hierarchical_search`) -- the one
real hierarchical index this codebase has. Before this pass it was
built and query-time-traversable but never wired into any retrieval
PIPELINE (`HybridRetriever`/`get_relevant_claims`), and index freshness
was never measured at all.

This file proves the two new primitives (`hierarchy.coarse_route`,
`hierarchy.compute_index_freshness`) and their wiring into
`HybridRetriever.retrieve(coarse_route_table=...)`.

DELIBERATE CHOICE, worth stating: this file does NOT call
`build_hierarchy_for_table` itself. That function (like `_fetch_roots`
underneath it) scans the WHOLE `knowledge_nodes` table with no per-test
scoping -- exactly why this repo's OWN pre-existing convention
(test_hierarchy.py's docstring) already routes every DB-backed
hierarchy.py test to a separate `integration_check_v2_hierarchy.py`
script rather than a shared-DB pytest e2e file: this session's DB is
shared with real production-shaped data (confirmed live: 75+ real
knowledge_nodes rows), and letting `build_hierarchy_for_table(apply=
True)` group across ALL of them from a test would risk creating
permanent, hard-to-target 'hierarchy_group' rows this test's own
name-prefix cleanup cannot find (their names are LLM/summary-derived,
not prefixed). Instead, this file constructs one small, fully-owned
tree by hand -- the EXACT same shape `_create_internal_node` produces
(mirrored here, not reused, since that function is internal/private) --
so both construction and cleanup stay entirely scoped to this test's
own prefixed rows. `coarse_route`'s root-selection step still reads
the WHOLE table's roots (unavoidable, same real scope `_fetch_roots`
always has), but an EXACT vector match (this test's own root embedding
IS the query vector) is guaranteed to win top-1 cosine similarity over
any real, non-exact-match production row, so this stays a safe, real
assertion despite the shared table.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.hierarchy import coarse_route, compute_index_freshness
from app.services.retrieval import HybridRetriever

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

# Two maximally-separated 1024-dim vectors -- an exact match to one of
# these is guaranteed to win top-1 cosine similarity over any real,
# non-identical production embedding.
_GROUP_A_VEC = [1.0] * 512 + [0.0] * 512
_GROUP_B_VEC = [0.0] * 512 + [1.0] * 512


class FakeEmbedder:
    """Same deterministic-vector convention test_retrieval_e2e.py's own
    FakeEmbedder uses -- no real Gemini call, ever."""

    def __init__(self, vector):
        self._vector = vector

    async def embed_one(self, text, input_type="document"):
        return self._vector


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{name_prefix}%")


async def _insert_claim_node(pool, name: str, embedding: list[float]) -> str:
    row = await pool.fetchrow(
        "INSERT INTO knowledge_nodes (node_type, name, properties, embedding) "
        "VALUES ('claim', $1, '{}'::jsonb, $2::vector) RETURNING id",
        name, to_pgvector(embedding),
    )
    return str(row["id"])


async def _build_owned_group(pool, name: str, embedding: list[float], child_ids: list[str]) -> str:
    """The exact shape `hierarchy._create_internal_node` produces for
    `knowledge_nodes` (mirrored, not imported -- that function is
    module-internal), scoped entirely to this test's own prefixed rows."""
    now = datetime.now(timezone.utc)
    row = await pool.fetchrow(
        "INSERT INTO knowledge_nodes (node_type, name, properties, provenance, embedding, "
        "t_valid, t_created, created_by) "
        "VALUES ('hierarchy_group', $1, $2::jsonb, 'company_debate', $3::vector, $4, $4, $5) "
        "RETURNING id",
        name, '{"member_count": %d}' % len(child_ids), to_pgvector(embedding), now, "test-fixture",
    )
    group_id = str(row["id"])
    for child_id in child_ids:
        await pool.execute(
            "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, provenance, t_valid, t_created, created_by) "
            "VALUES ('OWNS', 'PARENT_OF', $1::uuid, 'knowledge_nodes', $2::uuid, 'knowledge_nodes', "
            "'company_debate', $3, $3, $4)",
            group_id, child_id, now, "test-fixture",
        )
    return group_id


def test_coarse_route_and_index_freshness_over_a_real_two_branch_hierarchy():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "hier-coarse-test"
        try:
            await _cleanup(pool, prefix)

            a1 = await _insert_claim_node(pool, f"{prefix}-alpha-1", _GROUP_A_VEC)
            a2 = await _insert_claim_node(pool, f"{prefix}-alpha-2", _GROUP_A_VEC)
            b1 = await _insert_claim_node(pool, f"{prefix}-beta-1", _GROUP_B_VEC)
            b2 = await _insert_claim_node(pool, f"{prefix}-beta-2", _GROUP_B_VEC)

            # Before any grouping: these 4 real leaves exist but are not
            # incorporated into any tree yet.
            fresh_before = await compute_index_freshness(pool, "knowledge_nodes")
            assert fresh_before["canonical_revision"] >= 4
            lag_before = fresh_before["canonical_revision"] - fresh_before["indexed_revision"]
            assert lag_before >= 4

            group_a = await _build_owned_group(pool, f"{prefix}-group-alpha", _GROUP_A_VEC, [a1, a2])
            group_b = await _build_owned_group(pool, f"{prefix}-group-beta", _GROUP_B_VEC, [b1, b2])

            # After grouping: these 4 real leaves are now indexed -- the
            # lag attributable to them has closed to zero. (The group
            # rows themselves are index metadata, excluded from
            # canonical_revision by hierarchy.py's own group-row filter,
            # so they must not inflate either count.)
            fresh_after = await compute_index_freshness(pool, "knowledge_nodes")
            assert fresh_after["indexed_revision"] - fresh_before["indexed_revision"] == 4
            # The 2 new group rows are index metadata, excluded from
            # canonical_revision by hierarchy.py's own group-row filter
            # -- creating them must not inflate the canonical count at all.
            assert fresh_after["canonical_revision"] == fresh_before["canonical_revision"]
            lag_after = fresh_after["canonical_revision"] - fresh_after["indexed_revision"]
            assert lag_after == lag_before - 4

            # Coarse routing: a query matching group A's own vector must
            # route to exactly {a1, a2}, never touching group B at all.
            routed_a = await coarse_route(
                pool, "knowledge_nodes", "query about alpha", embedder=FakeEmbedder(_GROUP_A_VEC),
            )
            assert routed_a is not None
            assert set(routed_a) == {a1, a2}

            routed_b = await coarse_route(
                pool, "knowledge_nodes", "query about beta", embedder=FakeEmbedder(_GROUP_B_VEC),
            )
            assert routed_b is not None
            assert set(routed_b) == {b1, b2}
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_coarse_route_never_treats_an_isolated_ungrouped_leaf_as_its_own_branch():
    """MCP hardening B37 STRICT CLOSURE (real-corpus finding): this
    module's own docstring says "Internal is a STRUCTURAL property --
    has outgoing OWNS/PARENT_OF edges" -- an isolated, ungrouped leaf
    (no real hierarchy exists over it) has none, and must therefore
    NEVER be treated as a legitimate routable branch on its own, even
    when it wins top-1 exact-vector similarity against every other real
    root. (Superseded behavior, confirmed live against this session's
    own real, unclustered `knowledge_nodes` rows: before this fix,
    `_fetch_roots`'s "no incoming PARENT_OF edge" test alone made every
    ordinary ungrouped leaf indistinguishable from a real branch root,
    so an isolated leaf could win and be returned as a fabricated
    size-1 "branch" containing only itself -- silently treating "nothing
    has been organized here yet" as "this is its own tiny organized
    group", which is not the same real state.)"""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "hier-coarse-noroute-test"
        try:
            await _cleanup(pool, prefix)
            leaf = await _insert_claim_node(pool, f"{prefix}-solo", _GROUP_A_VEC)
            result = await coarse_route(
                pool, "knowledge_nodes", "anything", embedder=FakeEmbedder(_GROUP_A_VEC),
            )
            # An exact-match query vector against this leaf's own real
            # embedding would win top-1 similarity outright (1.0, the
            # maximum) under the OLD root definition -- proving it is
            # never even offered as a candidate root now, regardless of
            # whatever real branch (if any) the query actually routes to.
            assert result is None or leaf not in result, (
                "an isolated, ungrouped leaf with no real children must "
                "never be returned as if it were its own branch"
            )
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_coarse_route_safe_exclusions_never_excludes_a_fresh_unclustered_row():
    """MCP hardening B37 STRICT CLOSURE (index-staleness-safe
    narrowing): `coarse_route_safe_exclusions` -- the real function
    `HybridRetriever.retrieve(coarse_route_table=...)` now calls instead
    of treating `coarse_route`'s own inclusion list as ground truth --
    must return only rows CONFIRMED to belong to the query's non-matched
    real branch. A brand new leaf that was never added to either real
    branch is a member of neither, so it must never appear in the
    exclusion set (get_relevant_claims's own real, tested "must find the
    claim it was just given" contract depends on exactly this)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "hier-coarse-safeexcl-test"
        try:
            await _cleanup(pool, prefix)
            a1 = await _insert_claim_node(pool, f"{prefix}-alpha-1", _GROUP_A_VEC)
            a2 = await _insert_claim_node(pool, f"{prefix}-alpha-2", _GROUP_A_VEC)
            b1 = await _insert_claim_node(pool, f"{prefix}-beta-1", _GROUP_B_VEC)
            b2 = await _insert_claim_node(pool, f"{prefix}-beta-2", _GROUP_B_VEC)
            await _build_owned_group(pool, f"{prefix}-group-alpha", _GROUP_A_VEC, [a1, a2])
            await _build_owned_group(pool, f"{prefix}-group-beta", _GROUP_B_VEC, [b1, b2])

            # Added AFTER both real branches were built -- never absorbed
            # into either one's real membership.
            fresh = await _insert_claim_node(pool, f"{prefix}-fresh-alpha-like", _GROUP_A_VEC)

            from app.services.hierarchy import coarse_route_safe_exclusions

            excluded = await coarse_route_safe_exclusions(
                pool, "knowledge_nodes", "query about alpha", embedder=FakeEmbedder(_GROUP_A_VEC),
            )
            assert excluded is not None
            assert b1 in excluded and b2 in excluded, (
                "the OTHER real branch's own confirmed members must be excluded"
            )
            assert a1 not in excluded and a2 not in excluded, (
                "the matched branch's own members must never be excluded"
            )
            assert fresh not in excluded, (
                "a row never absorbed into any real branch must never be "
                "excluded just because indexing has not caught up to it"
            )
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_hybrid_retriever_coarse_routing_narrows_candidates_to_the_matched_branch():
    """Integration proof: two real, owned hierarchy branches, one
    lexical query that would otherwise match a node in EACH branch
    (shared word) -- with `coarse_route_table` set, only the branch the
    query's own vector routes to may appear; the other branch's textual
    match must be filtered out before RRF fusion, never silently
    included."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "hier-coarse-retrieve-test"
        try:
            await _cleanup(pool, prefix)

            # Both nodes share the literal word "sharedtopic" so a
            # lexical-only search would hit both -- the real test of
            # whether coarse routing actually narrows anything.
            a1 = await _insert_claim_node(pool, f"{prefix}-sharedtopic-alpha", _GROUP_A_VEC)
            a2 = await _insert_claim_node(pool, f"{prefix}-alpha-2", _GROUP_A_VEC)
            b1 = await _insert_claim_node(pool, f"{prefix}-sharedtopic-beta", _GROUP_B_VEC)
            b2 = await _insert_claim_node(pool, f"{prefix}-beta-2", _GROUP_B_VEC)

            await _build_owned_group(pool, f"{prefix}-group-alpha", _GROUP_A_VEC, [a1, a2])
            await _build_owned_group(pool, f"{prefix}-group-beta", _GROUP_B_VEC, [b1, b2])

            retriever = HybridRetriever(
                pool, embedder=FakeEmbedder(_GROUP_A_VEC),
                scope=AccessScope.unrestricted(), tables=("knowledge_nodes",),
            )

            # WITHOUT coarse routing: the shared word means both
            # branches' "sharedtopic" node are real lexical candidates.
            flat_result = await retriever.retrieve("sharedtopic", top_k=10)
            flat_ids = {str(n.id) for n in flat_result.nodes}
            assert a1 in flat_ids and b1 in flat_ids

            # WITH coarse routing (query vector points at group A): the
            # beta branch's "sharedtopic" hit must be filtered out.
            routed_result = await retriever.retrieve(
                "sharedtopic", top_k=10, coarse_route_table="knowledge_nodes",
            )
            routed_ids = {str(n.id) for n in routed_result.nodes}
            assert a1 in routed_ids
            assert b1 not in routed_ids, (
                "coarse routing must filter out the other branch's textual "
                "match, not just add the routed branch on top of it"
            )
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())
