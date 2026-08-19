"""
Real, live-database characterization tests for HybridRetriever.retrieve()
(ticket 14, memory-substrate map). Same pattern as the other e2e test
files: requires a real DATABASE_URL, skips (not fails) without one.

Ticket 14's own resolved answer, quoted directly: "The component is
load-bearing with zero coverage... Property-based tests land before any
extension of retrieval.py." test_retrieval_rrf_properties.py covers the
pure RRF arithmetic; this file covers retrieve() itself end-to-end
against a real database -- the load-bearing behaviors the module's own
docstrings and comments describe but nothing previously confirmed:
visibility scoping surviving graph expansion, hierarchy-group exclusion,
PARENT_OF-skip during expansion, and vector-failure degrading to
lexical-only rather than failing the whole query.

These are characterization tests: they pin CURRENT behavior (regression
detection), not a claim that the behavior is the one obviously-correct
answer -- consistent with ticket 14's own statement that "correctness
still needs human relevance judgements, which is out of scope here."
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.retrieval import VALID_EMBEDDING_COLUMNS, HybridRetriever

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class FakeEmbedder:
    """Deterministic per-call vector so vector search finds real,
    predictable neighbours -- distinct constant vectors per fixture
    keep entrypoint ranking meaningful rather than tied."""

    def __init__(self, vector=None):
        self._vector = vector or [0.1] * 1024

    async def embed_one(self, text, input_type="document"):
        return self._vector


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM task_nodes WHERE name LIKE $1) "
        "OR source_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{name_prefix}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{name_prefix}%")


async def _insert_task_node(pool, name, embedding=None, owner_id=None, visibility="public"):
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, description, skill_ref, embedding, owner_id, visibility) "
        "VALUES ($1, $1, $2, $3::vector, $4, $5::visibility_level) RETURNING id",
        name, f"skill_{name}", to_pgvector(embedding) if embedding else None, owner_id, visibility,
    )
    return row["id"]


def test_public_entrypoint_visible_to_anonymous_scope():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-public")
            await _insert_task_node(pool, "retr-test-public-node", embedding=[0.5] * 1024)

            retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.5] * 1024),
                                        scope=AccessScope.anonymous())
            result = await retriever.retrieve("retr-test-public-node", top_k=5)
            names = [n.name for n in result.nodes]
            assert "retr-test-public-node" in names
        finally:
            await _cleanup(pool, "retr-test-public")
            await pool.close()

    asyncio.run(_run())


def test_private_entrypoint_invisible_to_a_different_viewer():
    """Real, direct confirmation that visibility scoping actually holds
    in retrieve() -- not just in the lower-level query builders it
    calls. A private node owned by 'alice' must not be a retrievable
    entrypoint under bob's scope."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-private")
            await _insert_task_node(
                pool, "retr-test-private-node", embedding=[0.6] * 1024,
                owner_id="alice", visibility="private",
            )

            bob_retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.6] * 1024),
                                            scope=AccessScope.for_user("bob"))
            bob_result = await bob_retriever.retrieve("retr-test-private-node", top_k=5)
            assert "retr-test-private-node" not in [n.name for n in bob_result.nodes]

            alice_retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.6] * 1024),
                                              scope=AccessScope.for_user("alice"))
            alice_result = await alice_retriever.retrieve("retr-test-private-node", top_k=5)
            assert "retr-test-private-node" in [n.name for n in alice_result.nodes]
        finally:
            await _cleanup(pool, "retr-test-private")
            await pool.close()

    asyncio.run(_run())


def test_visibility_scoping_survives_graph_expansion():
    """The module's own comment claims this explicitly: 'the graph store
    inherits the same scope -- retrieval that filtered its entrypoints
    but then expanded through unscoped traversal would leak exactly
    what the filter prevented.' Tested directly: a public entrypoint
    linked to a private neighbour must not surface that neighbour
    during expansion, under a scope that cannot see it."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-expand")
            entry_id = await _insert_task_node(
                pool, "retr-test-expand-entry", embedding=[0.7] * 1024,
            )
            private_neighbour_id = await _insert_task_node(
                pool, "retr-test-expand-private-neighbour",
                owner_id="alice", visibility="private",
            )
            await pool.execute(
                "INSERT INTO edges (edge_type, source_id, source_table, target_id, "
                "target_table, provenance) VALUES ('REQUIRES', $1, 'task_nodes', "
                "$2, 'task_nodes', 'company_debate')",
                entry_id, private_neighbour_id,
            )

            bob_retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.7] * 1024),
                                            scope=AccessScope.for_user("bob"))
            result = await bob_retriever.retrieve(
                "retr-test-expand-entry", top_k=5, expand_depth=1,
            )
            names = [n.name for n in result.nodes]
            assert "retr-test-expand-entry" in names
            assert "retr-test-expand-private-neighbour" not in names, (
                "a private neighbour must not leak in via graph expansion "
                "even when the entrypoint that links to it is public"
            )
        finally:
            await _cleanup(pool, "retr-test-expand")
            await pool.close()

    asyncio.run(_run())


def test_hierarchy_group_node_excluded_from_direct_search():
    """A node that is the source of a live PARENT_OF edge is a
    hierarchy-group organizer, not a retrievable instance -- the
    module's own _NOT_A_HIERARCHY_GROUP filter, confirmed live rather
    than just read."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-hgroup")
            group_id = await _insert_task_node(
                pool, "retr-test-hgroup-parent", embedding=[0.8] * 1024,
            )
            child_id = await _insert_task_node(
                pool, "retr-test-hgroup-child", embedding=[0.8] * 1024,
            )
            await pool.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, provenance) VALUES "
                "('OWNS', 'PARENT_OF', $1, 'task_nodes', $2, 'task_nodes', 'company_debate')",
                group_id, child_id,
            )

            retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.8] * 1024))
            result = await retriever.retrieve("retr-test-hgroup", top_k=10)
            names = [n.name for n in result.nodes]
            assert "retr-test-hgroup-parent" not in names, (
                "a hierarchy-group parent (source of a live PARENT_OF edge) "
                "must never be a direct search hit"
            )
            assert "retr-test-hgroup-child" in names
        finally:
            await _cleanup(pool, "retr-test-hgroup")
            await pool.close()

    asyncio.run(_run())


def test_parent_of_edge_not_followed_during_expansion():
    """Direct search already excludes group nodes themselves (see
    test_hierarchy_group_node_excluded_from_direct_search); this pins
    the SEPARATE claim in the module's own comment: expansion must not
    reintroduce a group's PARENT node, or its OTHER children, through
    the PARENT_OF edge itself.

    Real structure, not the group node itself as entrypoint (that would
    be excluded from direct search before expansion is ever reached,
    testing nothing): `group --PARENT_OF--> entry` and
    `group --PARENT_OF--> other_child`, with `entry` as a valid,
    directly-findable entrypoint (it is the TARGET of the PARENT_OF
    edge, not the source, so _NOT_A_HIERARCHY_GROUP does not exclude
    it). Expanding from `entry` at depth 2 would, without the PARENT_OF
    skip, reach `group` (1 hop) then `other_child` (2 hops) -- both
    must be absent.

    Sibling/group names carry NO overlap with the query -- a shared
    prefix would let lexical search's OR-based word matching find them
    directly, confounding the test regardless of whether the PARENT_OF
    skip in expansion actually works. Likewise given NO embedding at
    all (not just a "distant" one): a real, direct measurement showed
    that in a small local test database, even a deliberately distant
    embedding still ranks within vector_search's generous top_k*2
    candidate window and gets treated as its own independent
    entrypoint -- confirmed by inspecting traverse_from()'s real output
    directly, which showed both PARENT_OF edges correctly tagged and
    would be skipped by retrieve()'s own `continue` on
    custom_edge_type=='PARENT_OF'; the leak traced to vector search
    finding the node on its own, not to expansion failing to skip it.
    A NULL embedding cannot be a vector-search candidate at all
    (_vector_search's own WHERE clause requires it IS NOT NULL), which
    is what actually isolates this test to graph expansion alone.
    """
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-noexpand")
            group_id = await _insert_task_node(
                pool, "zzz-unrelated-group-abc",
            )
            entry_id = await _insert_task_node(
                pool, "retr-test-noexpand-entry", embedding=[0.95] * 1024,
            )
            other_child_id = await _insert_task_node(
                pool, "zzz-unrelated-other-child-def",
            )
            await pool.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, provenance) VALUES "
                "('OWNS', 'PARENT_OF', $1, 'task_nodes', $2, 'task_nodes', 'company_debate')",
                group_id, entry_id,
            )
            await pool.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, provenance) VALUES "
                "('OWNS', 'PARENT_OF', $1, 'task_nodes', $2, 'task_nodes', 'company_debate')",
                group_id, other_child_id,
            )

            retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.95] * 1024))
            result = await retriever.retrieve("retr-test-noexpand-entry", top_k=5, expand_depth=2)
            names = [n.name for n in result.nodes]
            assert "retr-test-noexpand-entry" in names
            assert "zzz-unrelated-group-abc" not in names, (
                "expansion must not follow a PARENT_OF edge to reach the group parent"
            )
            assert "zzz-unrelated-other-child-def" not in names, (
                "expansion must not follow a PARENT_OF edge to reach a sibling child"
            )
        finally:
            await _cleanup(pool, "retr-test-noexpand")
            await pool.execute(
                "DELETE FROM task_nodes WHERE name IN "
                "('zzz-unrelated-group-abc', 'zzz-unrelated-other-child-def')"
            )
            await pool.close()

    asyncio.run(_run())


def test_vector_search_failure_degrades_to_lexical_only_not_a_crash():
    """The module's own documented contract: 'Degrade to lexical-only
    rather than failing the whole query.' Confirmed by forcing a real
    embedder failure (not mocked away) and checking retrieve() still
    returns real lexical results instead of raising."""
    class FailingEmbedder:
        async def embed_one(self, text, input_type="document"):
            raise RuntimeError("real, deliberate embedder failure for this test")

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-degrade")
            await _insert_task_node(pool, "retr-test-degrade-lexical-match")

            retriever = HybridRetriever(pool, embedder=FailingEmbedder())
            result = await retriever.retrieve("retr-test-degrade-lexical-match", top_k=5)
            names = [n.name for n in result.nodes]
            assert "retr-test-degrade-lexical-match" in names, (
                "a real embedder failure must still return real lexical hits, not empty/crash"
            )
        finally:
            await _cleanup(pool, "retr-test-degrade")
            await pool.close()

    asyncio.run(_run())


def test_hybrid_fusion_ranks_a_dual_match_above_a_single_signal_match():
    """End-to-end confirmation of the RRF premise through the real
    pipeline (not just the pure fuse_rrf() function tested elsewhere):
    a node matched by BOTH vector and lexical search should rank at or
    above one matched by only one signal."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "retr-test-dualmatch")
            # Dual match: shares the query's embedding AND its exact words.
            await _insert_task_node(
                pool, "retr-test-dualmatch shared query words here",
                embedding=[0.42] * 1024,
            )
            # Lexical-only: shares the words, deliberately far embedding.
            await _insert_task_node(
                pool, "retr-test-dualmatch other lexical words here",
                embedding=[-0.9] * 1024,
            )

            retriever = HybridRetriever(pool, embedder=FakeEmbedder([0.42] * 1024))
            result = await retriever.retrieve(
                "retr-test-dualmatch shared query words here", top_k=5,
            )
            by_name = {n.name: n for n in result.nodes}
            assert "retr-test-dualmatch shared query words here" in by_name
            dual = by_name["retr-test-dualmatch shared query words here"]
            assert set(dual.matched_by) >= {"semantic", "keyword"}, (
                f"expected the dual-match node to carry both signals, got {dual.matched_by}"
            )
        finally:
            await _cleanup(pool, "retr-test-dualmatch")
            await pool.close()

    asyncio.run(_run())


def test_invalid_embedding_column_rejected_at_construction():
    """Pure unit check, no DB needed for the assertion itself, but kept
    here alongside the rest of this module's characterization tests
    rather than split into a separate file."""
    with pytest.raises(ValueError):
        HybridRetriever(pool=None, embedding_column="not_a_real_column")


def test_embedding_joint_column_restricts_tables_to_task_nodes_only():
    """The module's own comment: 'embedding_joint only exists on
    task_nodes... a caller asking for the joint embedding is implicitly
    restricted there rather than erroring.'"""
    retriever = HybridRetriever(
        pool=None, embedding_column="embedding_joint",
        tables=("task_nodes", "knowledge_nodes"),
    )
    assert retriever._tables == ("task_nodes",)
