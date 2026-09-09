"""
Positive-direction companion to test_retrieval_fixture_isolation_e2e.py.

That test proves an engineering/test fixture is now correctly EXCLUDED from
ordinary user-facing retrieval (db/39+db/40+
applicability.py::_CANDIDATE_BASE_WHERE's new `is_engineering_fixture`
predicate). This test proves the fix did not overshoot: a procedure
explicitly captured as real, non-fixture knowledge
(`is_engineering_fixture=False`, the same flag the Better-Ways admission
phase's 3 real procedures already carry -- see
.scratch/final_agent_experiment/corpus-eligibility-review.md) DOES remain
retrievable once it independently earns verification_state='verified' +
approval_status='approved' through the same real evidence path any
procedure needs.

Both tests must pass together: fixture isolation without this positive
check could be satisfied by a fix that (incorrectly) excludes everything;
this positive check without the isolation test could be satisfied by a fix
that (incorrectly) excludes nothing. Neither alone proves the eligibility
boundary is actually principled.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.applicability import find_applicable_procedures
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


@pytest.mark.asyncio
async def test_explicitly_real_procedure_remains_retrievable_after_the_fixture_isolation_fix():
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    row_id = None
    try:
        run_id = uuid.uuid4().hex[:8]
        embedder = Embedder()

        goal_text = (
            f"regression-test-real-knowledge unique marker phrase ({run_id}) "
            "-- audit a repository for unused dependencies and remove them safely"
        )
        goal_vec = await embedder.embed_one(goal_text, input_type="document")
        result = await capture_procedure(
            pool, name=f"regression-test-real-knowledge-{run_id}", goal=goal_text,
            steps=[{"order": 0, "goal": "run dependency audit"}],
            provenance="prior_library", scope_type="global", embedding=goal_vec,
            is_engineering_fixture=False,  # the one thing this test does differently
        )
        row_id = result["id"]

        row = await pool.fetchrow(
            "SELECT is_engineering_fixture FROM procedures WHERE id = $1", row_id,
        )
        assert row["is_engineering_fixture"] is False, (
            "capture_procedure's is_engineering_fixture=False was not persisted -- "
            "this test's own premise is broken, not the retrieval fix"
        )

        for i in range(MIN_SUCCESSES_FOR_VERIFIED):
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True,
                context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
            )
        await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")

        confirmed = await pool.fetchrow(
            "SELECT verification_state, approval_status FROM procedures WHERE id = $1", row_id,
        )
        assert confirmed["verification_state"] == "verified"
        assert confirmed["approval_status"] == "approved"

        query_vec = await embedder.embed_one(
            f"audit a repository for unused dependencies and remove them safely ({run_id})",
            input_type="query",
        )
        candidates = await find_applicable_procedures(
            pool, goal_embedding=query_vec, require_verified=True, limit=10,
        )
        matched_ids = {str(c["id"]) for c in candidates}

        assert str(row_id) in matched_ids, (
            "A procedure explicitly captured as real knowledge "
            "(is_engineering_fixture=False) that independently earned "
            "verification_state='verified'/approval_status='approved' "
            "should remain reachable through ordinary user-facing "
            "retrieval -- the fixture-isolation fix must not also exclude "
            "genuine knowledge. See "
            "test_retrieval_fixture_isolation_e2e.py (the negative "
            "companion to this test) and "
            ".scratch/final_agent_experiment/corpus-eligibility-review.md."
        )
    finally:
        if row_id is not None:
            await pool.execute("DELETE FROM procedures WHERE id = $1", row_id)
        await pool.close()
