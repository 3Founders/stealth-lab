"""
`app.services.claim_equivalence`'s DB-backed half against a real Postgres:
candidate finding (embedding pre-filter), candidate-row recording (pair-order
normalization + idempotency), the pending review queue, and resolution --
and, deliberately, that resolving a candidate NEVER touches `knowledge_nodes`
or writes a real claim-graph edge (the founder's "we'll decide how to
resolve them later" directive: this is a review queue, not a second source
of truth for claim relations).

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.claim_equivalence import (
    find_candidate_claim_pairs,
    get_pending_claim_relation_candidates,
    record_claim_relation_candidate,
    resolve_claim_relation_candidate,
)
from app.services.claims import capture_claim
from app.services.sources import register_source

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database claim-equivalence test"
)

_MARK = "test-claim-equivalence-e2e"


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM claim_relation_candidates WHERE claim_a_id IN "
        "(SELECT id FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1) "
        "OR claim_b_id IN (SELECT id FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1",
                       f"{name_prefix}%")


async def _make_claim(pool, statement: str, src_id: str) -> str:
    return await capture_claim(
        pool, statement=statement, task_ids=[], source_ref=src_id,
        created_by="tester", owner_id="tester", visibility="public", scope_type="global",
    )


def test_candidate_pairs_finds_near_duplicate_claims_above_the_similarity_floor():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        try:
            src = await register_source(
                pool, source_type="document", locator=f"{_MARK}-src-{tag}",
                created_by="tester", visibility="public", owner_id="tester", scope_type="global",
            )
            a = await _make_claim(pool, f"{_MARK}: retrying failed requests improves success rate {tag}", src["id"])
            b = await _make_claim(pool, f"{_MARK}: retrying failed requests improves success rate {tag}", src["id"])
            unrelated = await _make_claim(pool, f"{_MARK}: the sky is blue on a clear day {tag}", src["id"])

            candidates = await find_candidate_claim_pairs(pool, a, top_k=10, min_similarity=0.75)
            candidate_ids = {str(c["id"]) for c in candidates}
            assert b in candidate_ids
            assert unrelated not in candidate_ids
        finally:
            await _cleanup(pool, f"{_MARK}:")
            await pool.close()

    asyncio.run(_run())


def test_record_candidate_normalizes_pair_order_and_is_idempotent():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        try:
            src = await register_source(
                pool, source_type="document", locator=f"{_MARK}-src-{tag}",
                created_by="tester", visibility="public", owner_id="tester", scope_type="global",
            )
            a = await _make_claim(pool, f"{_MARK}: claim A {tag}", src["id"])
            b = await _make_claim(pool, f"{_MARK}: claim B {tag}", src["id"])
            hi, lo = max(a, b), min(a, b)

            id1 = await record_claim_relation_candidate(
                pool, claim_a_id=hi, claim_b_id=lo, relation="equivalent",
                confidence=0.87, created_by="tester",
            )
            row = await pool.fetchrow(
                "SELECT claim_a_id, claim_b_id, relation, status FROM claim_relation_candidates WHERE id=$1",
                uuid.UUID(id1),
            )
            assert str(row["claim_a_id"]) == lo
            assert str(row["claim_b_id"]) == hi
            assert row["relation"] == "equivalent"
            assert row["status"] == "pending"

            # re-detecting the same (reversed-order) pair with the same detector is a no-op
            id2 = await record_claim_relation_candidate(
                pool, claim_a_id=lo, claim_b_id=hi, relation="equivalent",
                confidence=0.91, created_by="tester",
            )
            assert id2 == id1
            count = await pool.fetchval(
                "SELECT count(*) FROM claim_relation_candidates WHERE claim_a_id=$1::uuid AND claim_b_id=$2::uuid",
                lo, hi,
            )
            assert count == 1
        finally:
            await _cleanup(pool, f"{_MARK}:")
            await pool.close()

    asyncio.run(_run())


def test_pending_queue_and_resolution_never_touches_the_claim_graph():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        try:
            src = await register_source(
                pool, source_type="document", locator=f"{_MARK}-src-{tag}",
                created_by="tester", visibility="public", owner_id="tester", scope_type="global",
            )
            a = await _make_claim(pool, f"{_MARK}: claim C {tag}", src["id"])
            b = await _make_claim(pool, f"{_MARK}: claim D {tag}", src["id"])

            candidate_id = await record_claim_relation_candidate(
                pool, claim_a_id=a, claim_b_id=b, relation="contradicts",
                confidence=0.8, created_by="tester",
            )

            pending = await get_pending_claim_relation_candidates(pool, limit=1000)
            assert any(c["id"] == uuid.UUID(candidate_id) for c in pending)

            edges_before = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE (source_id=$1::uuid AND target_id=$2::uuid) "
                "OR (source_id=$2::uuid AND target_id=$1::uuid)", a, b,
            )
            assert edges_before == 0

            await resolve_claim_relation_candidate(
                pool, candidate_id=candidate_id, resolution="confirmed_contradicts", resolved_by="human-1",
            )

            row = await pool.fetchrow(
                "SELECT status, resolution, resolved_by, resolved_at FROM claim_relation_candidates WHERE id=$1",
                uuid.UUID(candidate_id),
            )
            assert row["status"] == "resolved"
            assert row["resolution"] == "confirmed_contradicts"
            assert row["resolved_by"] == "human-1"
            assert row["resolved_at"] is not None

            pending_after = await get_pending_claim_relation_candidates(pool, limit=1000)
            assert not any(c["id"] == uuid.UUID(candidate_id) for c in pending_after)

            # resolving is review bookkeeping only -- it must never write a real
            # claim-graph edge or change either claim's row
            edges_after = await pool.fetchval(
                "SELECT count(*) FROM edges WHERE (source_id=$1::uuid AND target_id=$2::uuid) "
                "OR (source_id=$2::uuid AND target_id=$1::uuid)", a, b,
            )
            assert edges_after == 0
            claim_rows = await pool.fetch(
                "SELECT properties->>'truth_state' AS truth_state FROM knowledge_nodes WHERE id = ANY($1::uuid[])",
                [a, b],
            )
            assert all(r["truth_state"] != "OUT" for r in claim_rows)
        finally:
            await _cleanup(pool, f"{_MARK}:")
            await pool.close()

    asyncio.run(_run())
