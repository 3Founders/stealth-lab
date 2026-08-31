"""
Live-database proving tests for app/services/claim_evidence.py (task
#38): record_claim_evidence -> get_claim_evidence round-trips a real
evidence row through the real `evidence` table.

Same pattern as every other `*_e2e.py` file (test_claim_relations_e2e.py
in particular, which this file's fixtures mirror): requires a real
DATABASE_URL, skips (not fails) without one. Self-cleaning by name
prefix, real `capture_claim` fixtures, no mocks.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.execution.evidence import EvidenceViolation
from app.services.claim_evidence import get_claim_evidence, record_claim_evidence
from app.services.claims import capture_claim

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-evd-e2e"


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


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _claim(pool, statement: str, task_name: str) -> str:
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        embedder=FakeEmbedder(),
    )
    assert claim_id
    return claim_id


def test_record_claim_evidence_round_trips_through_get_claim_evidence():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task1")
            claim_id = await _claim(pool, f"{PREFIX} claim one", task)

            evidence_id = await record_claim_evidence(
                pool,
                claim_id=claim_id,
                evidence_type="execution_result",
                outcome_status="success",
                success_criteria={"predicate": "the recorded run completed without error"},
                context_key=f"{PREFIX}-ctx-1",
                created_by=f"{PREFIX}-writer",
            )
            assert evidence_id

            rows = await get_claim_evidence(pool, claim_id)

            assert len(rows) == 1
            row = rows[0]
            assert str(row["id"]) == evidence_id
            assert row["target_type"] == "claim"
            assert str(row["target_id"]) == claim_id
            assert row["target_version"] is None
            assert row["outcome_status"] == "success"
            assert row["direction"] == "supports"
            assert row["created_by"] == f"{PREFIX}-writer"
            assert row["tenant_id"] is not None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_evidence_is_empty_for_a_claim_with_none():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            claim_id = await _claim(pool, f"{PREFIX} claim two, no evidence", task)

            rows = await get_claim_evidence(pool, claim_id)

            assert rows == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_evidence_orders_oldest_first_across_two_rows():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task3")
            claim_id = await _claim(pool, f"{PREFIX} claim three", task)

            first_id = await record_claim_evidence(
                pool, claim_id=claim_id, evidence_type="execution_result",
                outcome_status="failure", failure_class="environment_changed",
                context_key=f"{PREFIX}-ctx-3a",
            )
            second_id = await record_claim_evidence(
                pool, claim_id=claim_id, evidence_type="reproduction",
                outcome_status="success",
                success_criteria={"metrics": {"reproduced": True}},
                context_key=f"{PREFIX}-ctx-3b",
            )

            rows = await get_claim_evidence(pool, claim_id)

            assert [str(r["id"]) for r in rows] == [first_id, second_id]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_record_claim_evidence_rejects_bare_success_with_no_criteria_before_any_write():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task4")
            claim_id = await _claim(pool, f"{PREFIX} claim four", task)

            with pytest.raises(EvidenceViolation):
                await record_claim_evidence(
                    pool, claim_id=claim_id, evidence_type="execution_result",
                    outcome_status="success",
                )

            rows = await get_claim_evidence(pool, claim_id)
            assert rows == [], "a rejected payload must never reach the table"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())

