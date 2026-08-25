"""
Live-database regression tests for Band 2.7 TMS readability -- the
end-to-end halves of the proofs whose query-content halves live in
test_tms_readability_offline.py. Same pattern as the other e2e files:
requires a real DATABASE_URL, skips (not fails) without one.

The contract under proof, both halves at once:

  - VANISH: after relate_claims() flips a claim's truth_state to 'OUT',
    that claim must be absent from every retrieval surface --
    HybridRetriever's vector leg, its lexical leg (and lexical-only
    fallback), graph expansion (the SUPERSEDES edge itself is the leak
    path this band closes), and local_retrieval's structural tier.
  - HISTORY QUERYABLE: the same row must still exist with t_invalid
    NULL, its full properties intact, its SUPERSEDES edge connected --
    so "what did we once believe" stays answerable (raw row read AND a
    subject-scoped history scan), while state.py's project_state()
    keeps answering only "what do we believe now".

Real write path throughout: capture_claim()/relate_claims(), not hand-
UPDATEd fixtures, so the tests prove the production TMS loop end to end.
"""
import asyncio
import os
from uuid import UUID

import pytest

from app.db.session import create_pool
from app.services.claims import capture_claim, relate_claims
from app.services.retrieval import HybridRetriever
from app.services.local_retrieval import StructuralContext, retrieve_local_first
from app.services.state import project_state

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "tms-e2e"


class TextDerivedEmbedder:
    """
    Deterministic pseudo-embedding: identical text -> identical vector,
    different text -> different direction (byte-position hashing). So
    querying with a claim's own statement ranks THAT claim first
    (distance ~0) without a real embedding model, and the vector leg of
    retrieval is exercised for real rather than fallen back from.
    """

    DIM = 1024

    async def embed_one(self, text, input_type="document"):
        vec = [0.0] * self.DIM
        for i, b in enumerate(text.encode("utf-8")):
            vec[(i * 31 + b) % self.DIM] += 0.01
        return vec


class FailingEmbedder:
    async def embed_one(self, text, input_type="document"):
        raise RuntimeError("deliberate: force the lexical-only fallback path")


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR source_id IN (SELECT id FROM task_nodes WHERE name LIKE $1)",
        f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _insert_task_node(pool, name: str):
    """Returns `name`; claims link through task_nodes.skill_ref, which is
    set here to "skill_{name}" -- see _capture."""
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, description, skill_ref) "
        "VALUES ($1, $1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _capture(pool, statement: str, task_name: str, subject=None) -> str:
    # capture_claim() links claims through task_nodes.skill_ref values,
    # which _insert_task_node sets to "skill_{name}".
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        subject=subject, predicate="uses", object="some tool",
        embedder=TextDerivedEmbedder(),
    )
    assert claim_id, f"fixture failed: claim not written ({statement!r})"
    return claim_id


async def _supersede(pool, old_id: str, new_id: str) -> None:
    await relate_claims(
        pool, from_claim_id=new_id, to_claim_id=old_id,
        relation="SUPERSEDES", created_by="tms-e2e-test",
    )


def test_superseded_claim_vanishes_from_hybrid_retrieval_and_row_survives():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} alpha-task")
            old_id = await _capture(
                pool, f"{PREFIX} alpha legacy deploy step uses heroku cli", task_name,
            )
            new_id = await _capture(
                pool, f"{PREFIX} alpha new deploy step uses flyctl", task_name,
            )
            await _supersede(pool, old_id, new_id)

            retriever = HybridRetriever(pool, embedder=TextDerivedEmbedder())
            result = await retriever.retrieve(
                f"{PREFIX} alpha legacy deploy step uses heroku cli", top_k=5,
            )
            names = [n.name for n in result.nodes]
            assert f"{PREFIX} alpha legacy deploy step uses heroku cli" not in names, (
                "a SUPERSEDED (truth_state='OUT') claim must vanish from "
                "hybrid retrieval results"
            )
            assert f"{PREFIX} alpha new deploy step uses flyctl" in names, (
                "the live successor must remain retrievable"
            )

            # History half: same row, still there, still itself.
            row = await pool.fetchrow(
                "SELECT properties, t_valid, t_invalid FROM knowledge_nodes WHERE id = $1",
                UUID(old_id),
            )
            assert row is not None, "invalidated claim row must survive as history"
            assert row["properties"]["truth_state"] == "OUT"
            assert row["properties"]["statement"] == (
                f"{PREFIX} alpha legacy deploy step uses heroku cli"
            )
            assert row["t_invalid"] is None, (
                "TMS invalidation flips belief only -- t_invalid is a "
                "bi-temporal concern and must stay NULL"
            )
            edge = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE edge_type = 'SUPERSEDES' "
                "AND source_id = $1 AND target_id = $2 AND t_invalid IS NULL",
                UUID(new_id), UUID(old_id),
            )
            assert edge == 1, "the SUPERSEDES justification edge must stay connected"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_out_claim_excluded_on_the_lexical_only_fallback_path_too():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} lex-task")
            old_id = await _capture(
                pool, f"{PREFIX} lex unique zebra quarantine protocol", task_name,
            )
            new_id = await _capture(
                pool, f"{PREFIX} lex replacement quarantine protocol v2", task_name,
            )
            await _supersede(pool, old_id, new_id)

            retriever = HybridRetriever(pool, embedder=FailingEmbedder())
            result = await retriever.retrieve(
                f"{PREFIX} lex unique zebra quarantine protocol", top_k=5,
            )
            names = [n.name for n in result.nodes]
            assert names, "lexical-only fallback must still return real hits"
            assert f"{PREFIX} lex unique zebra quarantine protocol" not in names, (
                "vector-leg degrades to lexical on embedder failure; the OUT "
                "claim must be excluded there too, not just on the hybrid path"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_superseded_claim_not_reintroduced_via_graph_expansion():
    """THE regression: relate_claims() writes the SUPERSEDES edge from the
    live successor directly TO the OUT claim -- expansion along exactly
    that edge was reintroducing what search excluded, before Band 2.7."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} expand-task")
            old_id = await _capture(
                pool, f"{PREFIX} expand obsolete rollback via ftp script", task_name,
            )
            new_id = await _capture(
                pool, f"{PREFIX} expand current rollback via git revert", task_name,
            )
            await _supersede(pool, old_id, new_id)

            retriever = HybridRetriever(pool, embedder=TextDerivedEmbedder())
            result = await retriever.retrieve(
                f"{PREFIX} expand current rollback via git revert",
                top_k=5, expand_depth=1,
            )
            names = [n.name for n in result.nodes]
            assert f"{PREFIX} expand current rollback via git revert" in names
            assert f"{PREFIX} expand obsolete rollback via ftp script" not in names, (
                "the OUT successor-linked claim is one hop away through its own "
                "SUPERSEDES edge; expansion must not resurrect it"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_structural_tier_also_hides_out_claims_in_local_first_retrieval():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} beta-task")
            old_id = await _capture(
                pool, f"{PREFIX} beta deploy py legacy uses heroku", task_name,
            )
            new_id = await _capture(
                pool, f"{PREFIX} beta deploy py current uses flyctl", task_name,
            )
            await _supersede(pool, old_id, new_id)

            # One substring candidate matching BOTH claims by name -- the
            # structural tier is an independent ILIKE query into
            # knowledge_nodes, so without the filter it would re-admit the
            # OUT claim the semantic tier excluded.
            assembled = await retrieve_local_first(
                pool, f"{PREFIX} beta deploy",
                embedder=TextDerivedEmbedder(),
                structural=StructuralContext(open_files=[f"{PREFIX} beta deploy py"]),
            )
            included = {str(i) for i in assembled.included_node_ids}
            assert new_id in included, "live successor enters through the structural tier"
            assert old_id not in included, (
                "OUT claim must not re-enter through the structural/temporal tiers"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_history_stays_queryable_while_current_belief_excludes_the_out_claim():
    """The two answers must stay SEPARATELY available from the same rows
    (claims.py's own design sentence): 'what did we once believe' via an
    unfiltered subject-scoped history read, 'what do we believe now' via
    project_state(). Retrieval filtering must not have collapsed them."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            subject = f"{PREFIX} gamma-subject"
            task_name = await _insert_task_node(pool, f"{PREFIX} gamma-task")
            old_id = await _capture(
                pool, f"{PREFIX} gamma build runs on jenkins agents", task_name,
                subject=subject,
            )
            new_id = await _capture(
                pool, f"{PREFIX} gamma build runs on github actions runners", task_name,
                subject=subject,
            )
            await _supersede(pool, old_id, new_id)

            history = await pool.fetch(
                "SELECT id, properties->>'truth_state' AS ts, t_valid, t_invalid "
                "FROM knowledge_nodes "
                "WHERE node_type = 'claim' AND properties->>'subject' = $1",
                subject,
            )
            # Both rows survive the flip and both generations are visible to
            # a history read -- which generation is "current" is carried by
            # truth_state alone, NOT by row ordering or existence.
            assert len(history) == 2, "history read sees BOTH generations"
            by_ts = {r["ts"]: r for r in history}
            assert set(by_ts) == {"IN", "OUT"}
            assert str(by_ts["OUT"]["id"]) == old_id
            assert str(by_ts["IN"]["id"]) == new_id
            assert all(r["t_invalid"] is None for r in history), (
                "neither generation was bi-temporally invalidated -- the TMS "
                "flip is epistemic, not existential"
            )

            current = await project_state(pool, subjects=[subject])
            current_ids = {c["id"] for c in current}
            assert current_ids == {new_id}, (
                "current-belief projection returns exactly the live successor"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
