"""
Live-database proving tests for app/services/claim_traversal.py's three
read-only modes (EXPLAIN/RESEARCH/DECIDE), against real procedures,
claims, and edges -- not FakePool.

Same pattern as every other `*_e2e.py` file (test_claim_relations_e2e.py
in particular, which this file's fixtures mirror): requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.claim_evidence import record_claim_evidence
from app.services.claim_traversal import decide, explain, research
from app.services.claims import capture_claim, link_claims, relate_claims
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-trav-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    # evidence is [H] append-only (db/24_evidence.sql, invariant #19):
    # DELETE is refused outright, so a rerun retracts via the same
    # t_invalid tombstone the engine itself enforces, rather than
    # deleting the row.
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'claim' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _claim(pool, statement, task_name, *, subject, predicate, obj):
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        embedder=FakeEmbedder(), subject=subject, predicate=predicate, object=obj,
    )
    assert claim_id
    return claim_id


def test_explain_finds_the_real_claim_satisfying_a_real_precondition():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task1")
            claim_id = await _claim(
                pool, f"{PREFIX} pandas installed", task,
                subject=f"{PREFIX}:project:p1", predicate="uses", obj="pandas",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc1", goal="do a thing",
                preconditions=[{"subject": f"{PREFIX}:project:p1", "predicate": "uses", "object": "pandas"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcome = await explain(pool, row_id)

            assert outcome.procedure_row_id == row_id
            assert len(outcome.preconditions) == 1
            pc = outcome.preconditions[0]
            assert pc.satisfied is True
            assert {c["id"] for c in pc.supporting_claims} == {claim_id}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_explain_surfaces_real_evidence_recorded_against_the_supporting_claim():
    """The task's own round-trip proof: record_claim_evidence ->
    get_claim_evidence -> explain()'s new `evidence` field, against a
    real claim/procedure pair, no mocks."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task5")
            claim_id = await _claim(
                pool, f"{PREFIX} pandas installed 5", task,
                subject=f"{PREFIX}:project:p5", predicate="uses", obj="pandas",
            )
            evidence_id = await record_claim_evidence(
                pool, claim_id=claim_id, evidence_type="execution_result",
                outcome_status="success",
                success_criteria={"predicate": "pip show pandas exited 0"},
                context_key=f"{PREFIX}-ctx5",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc5", goal="do a thing",
                preconditions=[{"subject": f"{PREFIX}:project:p5", "predicate": "uses", "object": "pandas"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcome = await explain(pool, row_id)

            pc = outcome.preconditions[0]
            assert pc.satisfied is True
            assert {c["id"] for c in pc.supporting_claims} == {claim_id}
            assert len(pc.evidence) == 1
            assert str(pc.evidence[0]["id"]) == evidence_id
            assert pc.evidence[0]["outcome_status"] == "success"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_explain_evidence_is_empty_for_a_real_claim_with_none_recorded():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task6")
            await _claim(
                pool, f"{PREFIX} pandas installed 6", task,
                subject=f"{PREFIX}:project:p6", predicate="uses", obj="pandas",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc6", goal="do a thing",
                preconditions=[{"subject": f"{PREFIX}:project:p6", "predicate": "uses", "object": "pandas"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcome = await explain(pool, row_id)

            pc = outcome.preconditions[0]
            assert pc.satisfied is True
            assert pc.evidence == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_research_finds_a_real_two_hop_chain_and_stops_there():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            a = await _claim(pool, f"{PREFIX} claim A", task, subject=f"{PREFIX}:s:a", predicate="p", obj="a")
            b = await _claim(pool, f"{PREFIX} claim B", task, subject=f"{PREFIX}:s:b", predicate="p", obj="b")
            c = await _claim(pool, f"{PREFIX} claim C", task, subject=f"{PREFIX}:s:c", predicate="p", obj="c")
            d = await _claim(pool, f"{PREFIX} claim D", task, subject=f"{PREFIX}:s:d", predicate="p", obj="d")

            await link_claims(pool, from_claim_id=a, to_claim_id=b, relation="SUPPORTS")
            await link_claims(pool, from_claim_id=b, to_claim_id=c, relation="SUPPORTS")
            await link_claims(pool, from_claim_id=c, to_claim_id=d, relation="SUPPORTS")
            await relate_claims(pool, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS")

            outcome = await research(pool, a)

            assert outcome.claim_id == a
            supporting_ids = {(r.to_claim_id, r.hop) for r in outcome.supporting}
            assert (b, 1) in supporting_ids
            assert (c, 2) in supporting_ids
            assert d not in {r.to_claim_id for r in outcome.supporting}, "must not reach hop 3"
            assert any(r.to_claim_id == b and r.hop == 1 for r in outcome.contradicting)
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_decide_wraps_real_check_hard_constraints_and_surfaces_closest_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task3")
            # Claim exists for the subject but does NOT satisfy the
            # procedure's precondition (wrong object).
            claim_id = await _claim(
                pool, f"{PREFIX} numpy installed", task,
                subject=f"{PREFIX}:project:p3", predicate="uses", obj="numpy",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc3", goal="do a thing",
                preconditions=[{"subject": f"{PREFIX}:project:p3", "predicate": "uses", "object": "pandas"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            # require_verified=False -- explicit invocation, the same
            # bypass check_hard_constraints documents for a caller
            # naming this procedure directly; a freshly captured
            # procedure starts candidate/unverified and this test's
            # subject is the precondition gate, not the verification
            # gate (which the real 'verified requires evidence' DB
            # trigger enforces separately and correctly).
            outcome = await decide(pool, row_id, {}, require_verified=False)

            assert outcome.applicability.applicable is False
            assert outcome.applicability.failed_constraints[0].startswith("precondition:")
            assert outcome.closest_precondition_claims is not None
            assert outcome.closest_precondition_claims["subject"] == f"{PREFIX}:project:p3"
            assert {c["id"] for c in outcome.closest_precondition_claims["claims"]} == {claim_id}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_decide_applicable_when_real_claim_satisfies_precondition():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task4")
            await _claim(
                pool, f"{PREFIX} pandas installed 4", task,
                subject=f"{PREFIX}:project:p4", predicate="uses", obj="pandas",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc4", goal="do a thing",
                preconditions=[{"subject": f"{PREFIX}:project:p4", "predicate": "uses", "object": "pandas"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcome = await decide(pool, row_id, {}, require_verified=False)

            assert outcome.applicability.applicable is True
            assert outcome.closest_precondition_claims is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
