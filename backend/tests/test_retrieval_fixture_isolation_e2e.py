"""
Regression test for a real finding from the Final Baseline vs Stealth Agent
Experiment (12-trial pilot, T1/T3/B_default): every completed Stealth-arm
trial's retrieval matched a test-fixture procedure -- specifically ones
created by test_find_best_way_plan_only_e2e.py's own `_make_verified_approved`
helper (name prefix `proc-test-planonly-root-`) -- not real knowledge.

Root cause, confirmed by direct inspection of the real corpus and the real
retrieval predicate (see .scratch/final_agent_experiment/
retrieval-contamination-review.md for the full investigation): as of this
commit, `find_applicable_procedures`'s real WHERE-clause chain
(app/services/applicability.py's `_CANDIDATE_BASE_WHERE` and its
`require_verified` gate) filters ONLY on `verification_state`,
`approval_status`, `t_invalid`, `staleness`, and `availability` -- it has NO
predicate anywhere that references `provenance`, `created_by`, or any other
field that could distinguish a test-fixture procedure from real production
knowledge. A test that mechanically earns `verification_state='verified'` +
`approval_status='approved'` (the exact bar `_make_verified_approved`
clears, using `approved_by="tester"`) is retrieval-indistinguishable from
real knowledge today.

Confirmed via live query: ALL 12 of the 12 procedures currently meeting
`verification_state='verified' AND approval_status='approved'` in this
shared dev corpus are engineering smoke/demo/test data (5 x
`proc-test-planonly-root-*`, 5 x `canon-demo-*`, 2 x `seed_demo_procedures`)
-- ZERO are real externally-ingested knowledge.

THIS TEST WAS ORIGINALLY WRITTEN TO FAIL against the code as it stood when
this file was first added, pinning the real gap honestly rather than being
written to pass. db/39 (`procedures.is_engineering_fixture`) + db/40
(explicit backfill classification) + `applicability.py::
_CANDIDATE_BASE_WHERE`'s `AND is_engineering_fixture = false` predicate
together close the gap. This test's own fixture below originally relied
entirely on the column's DEFAULT to become a fixture -- that DEFAULT was
`true` (fail-closed) when this file was written, but db/57 (this session)
deliberately flipped the column's DEFAULT to `false`, because that
fail-closed default was also silently hiding ~1500-2700 real, bulk-
ingested procedures that had never explicitly opted in as fixtures (see
db/57's own docstring). Any caller that wants fixture semantics now must
say so explicitly, this test's fixture included -- `is_engineering_
fixture=True` is passed below rather than left to the (now permissive)
column default. See .scratch/final_agent_experiment/corpus-eligibility-
review.md for the original investigation. Do not weaken the assertion
below -- if it ever starts failing again with the explicit flag still in
place, that is a real regression of the fix, not something to loosen.
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
async def test_test_fixture_procedure_does_not_surface_as_normal_knowledge_in_user_facing_retrieval():
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    row_id = None
    try:
        run_id = uuid.uuid4().hex[:8]
        embedder = Embedder()

        # Deliberately mirror test_find_best_way_plan_only_e2e.py's real
        # fixture-creation pattern exactly -- same name prefix convention
        # this test's own module docstring documents as the actual, real
        # contamination source; same provenance/scope_type any e2e test in
        # this codebase already uses; same mechanical bar
        # (MIN_SUCCESSES_FOR_VERIFIED successes across
        # MIN_DISTINCT_CONTEXTS_FOR_VERIFIED distinct contexts, then
        # approve_procedure) that legitimately earns verification_state=
        # 'verified'/approval_status='approved' today.
        goal_text = (
            f"regression-test-fixture-isolation unique marker phrase ({run_id}) "
            "-- run the shared lint pass and verify the change"
        )
        goal_vec = await embedder.embed_one(goal_text, input_type="document")
        result = await capture_procedure(
            pool, name=f"regression-test-fixture-isolation-{run_id}", goal=goal_text,
            steps=[{"order": 0, "goal": "run linter"}],
            provenance="system_pending_review", scope_type="global", embedding=goal_vec,
            is_engineering_fixture=True,
        )
        row_id = result["id"]
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

        # A genuinely unrelated, normal user-facing query embedding, sharing
        # only the rare marker phrase -- if retrieval is doing anything more
        # than "matches the fixture's own exact wording", this proves the
        # fixture is reachable via ordinary semantic similarity the way any
        # real procedure would be, not just an exact-string coincidence.
        query_vec = await embedder.embed_one(
            f"run the shared lint pass and verify the change ({run_id})", input_type="query",
        )
        candidates = await find_applicable_procedures(
            pool, goal_embedding=query_vec, require_verified=True, limit=10,
        )
        matched_ids = {c["id"] for c in candidates}

        assert str(row_id) not in {str(m) for m in matched_ids}, (
            "REGRESSION of the db/39+db/40+_CANDIDATE_BASE_WHERE fix: a "
            "test-fixture procedure, created and approved by the exact "
            "same mechanism any e2e test in this codebase can trivially "
            "trigger, is once again fully eligible for and returned by "
            "ordinary user-facing retrieval (require_verified=True, the "
            "production default) -- indistinguishable from real "
            "externally-ingested knowledge. See "
            "test_retrieval_fixture_isolation_e2e.py's module docstring "
            "and .scratch/final_agent_experiment/corpus-eligibility-"
            "review.md for the original root-cause investigation and fix."
        )
    finally:
        if row_id is not None:
            await pool.execute("DELETE FROM procedures WHERE id = $1", row_id)
        await pool.close()
