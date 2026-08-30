"""
Real, live-database proving test for the local-client verification-default
bug fix (V1 push, local-agent-runner-default lane).

Bug: LocalAgentRunner.run()'s allow_unverified defaulted to True, threaded
into search_procedures as require_verified=not allow_unverified -- so the
default PRODUCT behavior silently allowed a `candidate`
(unverified/unapproved) procedure to be selected for real execution. This
violated spec's Phase-3 rule: default user-facing execution must use
verified + approved + fresh + applicable procedures only; unverified
procedures may exist for development/explicit experimentation, never
silently.

find_best_way (app/mcp_server/server.py) already defaulted
allow_unverified_procedures=False correctly -- the bug was CLIENT-side
only, in LocalAgentRunner. The fix flips LocalAgentRunner.run()'s
allow_unverified default to False.

This test proves the effect at the lowest real layer that carries the
actual decision: app.services.applicability.find_applicable_procedures,
the exact function search_procedures (both the remote MCP tool
LocalAgentRunner calls, and find_best_way's own tier-1) calls internally.
A full live LocalAgentRunner.run() would additionally require a real MCP
server process, a real LLM key, and a real repo -- unnecessary to prove
that the DEFAULT now excludes an unverified/unapproved candidate and
selects a verified+approved procedure instead, and that explicit opt-in
(require_verified=False, i.e. allow_unverified=True client-side) still
lets the candidate through.

Same pattern as the other *_e2e.py files in this directory: requires a
real DATABASE_URL, skips (not fails) without one.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.applicability import find_applicable_procedures
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


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


async def _make_verified_and_approved(pool, *, name: str, goal: str) -> str:
    """Real path to verified+approved: capture, cross ticket-13's real
    threshold with real successes across enough distinct contexts, then
    approve -- the same two-axis mechanism test_procedures_e2e.py already
    proves is real and non-fast-tracked."""
    result = await capture_procedure(
        pool, name=name, goal=goal, provenance="system_pending_review", scope_type="global",
    )
    row_id = result["id"]
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    row = await pool.fetchrow(
        "SELECT verification_state, approval_status FROM procedures WHERE id = $1", row_id
    )
    assert row["verification_state"] == "verified"
    assert row["approval_status"] == "approved"
    return row_id


def test_default_require_verified_excludes_candidate_and_selects_verified():
    """The exact bug: with the DEFAULT (require_verified=True, matching
    LocalAgentRunner.run()'s new allow_unverified=False default), a fresh
    `candidate`/unapproved procedure for the same task must never appear
    in the results, and the real verified+approved procedure must."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-verdefault")

            verified_id = await _make_verified_and_approved(
                pool, name="proc-test-verdefault-verified",
                goal="fix the failing calculator test",
            )

            candidate_result = await capture_procedure(
                pool, name="proc-test-verdefault-candidate",
                goal="fix the failing calculator test",
                provenance="system_pending_review", scope_type="global",
            )
            candidate_id = candidate_result["id"]
            row = await pool.fetchrow(
                "SELECT verification_state, approval_status FROM procedures WHERE id = $1",
                candidate_id,
            )
            assert row["verification_state"] == "candidate"
            assert row["approval_status"] == "proposed"

            # This is the real decision LocalAgentRunner.run()'s new
            # default (allow_unverified=False -> require_verified=True)
            # now drives, at the exact layer search_procedures calls.
            # limit/candidate_pool_size set generously above their
            # defaults: this repo's shared local Postgres carries rows
            # from other parallel agents' work, and the pre-filter orders
            # by jsonb_array_length(preconditions) ASC with arbitrary tie
            # order among the many zero-precondition rows -- a small
            # default limit could truncate our own test rows out on tie
            # order alone, which would be cross-run noise, not a real
            # signal about this fix.
            matches = await find_applicable_procedures(
                pool, goal_embedding=None, current_scope={}, require_verified=True,
                limit=5000, candidate_pool_size=5000,
            )
            matched_ids = {str(m["id"]) for m in matches}

            assert str(verified_id) in matched_ids, (
                "the real verified+approved procedure must be selectable by default"
            )
            assert str(candidate_id) not in matched_ids, (
                "BUG: an unverified/unapproved candidate procedure must never be "
                "silently selected under the default (require_verified=True)"
            )
        finally:
            await _cleanup(pool, "proc-test-verdefault")
            await pool.close()

    asyncio.run(_run())


def test_explicit_opt_in_still_allows_the_candidate_through():
    """The opt-in side of the same fix: require_verified=False (what
    LocalAgentRunner.run(allow_unverified=True) now explicitly requests)
    must still surface the unverified/unapproved candidate -- the fix
    changes the DEFAULT, not the existence of the escape hatch."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-veroptin")

            candidate_result = await capture_procedure(
                pool, name="proc-test-veroptin-candidate",
                goal="fix the failing calculator test",
                provenance="system_pending_review", scope_type="global",
            )
            candidate_id = candidate_result["id"]

            matches = await find_applicable_procedures(
                pool, goal_embedding=None, current_scope={}, require_verified=False,
                limit=5000, candidate_pool_size=5000,
            )
            matched_ids = {str(m["id"]) for m in matches}

            assert str(candidate_id) in matched_ids, (
                "explicit opt-in (require_verified=False) must still let an "
                "unverified/unapproved candidate through, unchanged"
            )
        finally:
            await _cleanup(pool, "proc-test-veroptin")
            await pool.close()

    asyncio.run(_run())
