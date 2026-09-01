"""
Real, live-database tests for procedures.py. Same pattern as the other
e2e test files this session: requires a real DATABASE_URL, skips (not
fails) without one. Tests exercise ticket 13's actual numbers directly
(>=10 successes/0 failures/>=3 contexts for verified, 5-failure circuit
breaker, 14-day quarantine disable, Minton's utility formula) rather than
asserting the function "does something reasonable".
"""
import asyncio
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.db.session import create_pool
from app.services.applicability import check_hard_constraints
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    ProcedureNotFound,
    approve_procedure,
    capture_procedure,
    check_quarantine_and_disable,
    compute_utility,
    get_procedure,
    mark_procedure_stale,
    record_execution_outcome,
    reject_procedure,
    retire_negative_utility_procedures,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_approve_procedure_is_orthogonal_to_verification_state():
    """approve_procedure() must set approval_status without touching
    verification_state -- an approved procedure with zero recorded
    successes is still, correctly, not verified. The two axes must not
    bleed into each other."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-approve")
            result = await capture_procedure(
                pool, name="proc-test-approve-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["approval_status"] == "proposed"

            await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")

            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["approval_status"] == "approved"
            assert row["approved_by"] == "tester"
            assert row["approved_at"] is not None
            assert row["verification_state"] == "candidate", (
                "approval must not fast-track statistical verification"
            )
        finally:
            await _cleanup(pool, "proc-test-approve")
            await pool.close()

    asyncio.run(_run())


def test_reject_procedure_sets_rejected_status():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-reject")
            result = await capture_procedure(
                pool, name="proc-test-reject-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            await reject_procedure(pool, procedure_row_id=row_id, approved_by="tester")

            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["approval_status"] == "rejected"
        finally:
            await _cleanup(pool, "proc-test-reject")
            await pool.close()

    asyncio.run(_run())


def test_capture_procedure_starts_candidate_fresh_active():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-capture")
            result = await capture_procedure(
                pool, name="proc-test-capture-1", goal="fix a failing test",
                steps=[{"action": "locate"}, {"action": "fix"}],
                provenance="system_pending_review", scope_type="global",
            )
            assert result["id"]
            assert result["procedure_id"]

            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"])
            assert row["verification_state"] == "candidate"
            assert row["staleness"] == "fresh"
            assert row["availability"] == "active"
            assert row["verification_stats"]["attempts"] == 0
        finally:
            await _cleanup(pool, "proc-test-capture")
            await pool.close()

    asyncio.run(_run())


def test_promotion_requires_the_real_threshold_not_just_any_successes():
    """Real confirmation of ticket 13's actual numbers: successes below
    the threshold, or successes spread across too few distinct contexts,
    must NOT promote to verified -- only crossing both bars does."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-promo")
            result = await capture_procedure(
                pool, name="proc-test-promo-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            # Below the success threshold, even with enough contexts.
            for i in range(MIN_SUCCESSES_FOR_VERIFIED - 1):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}",
                )
            row = await pool.fetchrow("SELECT verification_state FROM procedures WHERE id = $1", row_id)
            assert row["verification_state"] == "candidate", "must not promote below the success threshold"

            # Cross the success threshold but keep contexts too narrow.
            result2 = await capture_procedure(
                pool, name="proc-test-promo-2", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id2 = result2["id"]
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id2, success=True, context_key="only-one-context",
                )
            row2 = await pool.fetchrow("SELECT verification_state FROM procedures WHERE id = $1", row_id2)
            assert row2["verification_state"] == "candidate", (
                "must not promote with enough successes but too few distinct contexts"
            )

            # Cross both real bars.
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                outcome = await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                )
            assert outcome["verification_state"] == "verified"
        finally:
            await _cleanup(pool, "proc-test-promo")
            await pool.close()

    asyncio.run(_run())


def test_a_single_failure_before_promotion_permanently_blocks_it():
    """Ticket 13's bar is >=10 successes with ZERO failures, cumulative
    over the procedure's whole history -- not a recent window. One real
    failure anywhere in that history means total_failures never returns
    to 0, so promotion becomes permanently unreachable, no matter how
    many successes follow. (Separately, ticket 13 also says a procedure
    that HAS already reached verified must never be demoted by one
    failure afterward -- that's a different guarantee, checked below.)"""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-onefail")
            result = await capture_procedure(
                pool, name="proc-test-onefail-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            # One real failure early, before the threshold is anywhere close.
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False, context_key="ctx-fail",
            )
            # Then many more successes than the threshold requires.
            outcome = None
            for i in range(MIN_SUCCESSES_FOR_VERIFIED + 5):
                outcome = await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                )
            assert outcome["verification_state"] == "candidate", (
                "one early failure must permanently block promotion, even with many later successes"
            )
        finally:
            await _cleanup(pool, "proc-test-onefail")
            await pool.close()

    asyncio.run(_run())


def test_a_verified_procedure_is_never_demoted_by_a_later_failure():
    """The separate guarantee ticket 13 states explicitly: 'a verified
    procedure must never be automatically rewritten after one failure.'
    Once verification_state reaches verified, a subsequent failure must
    affect availability (circuit breaker) only, never verification_state
    itself."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-noreset")
            result = await capture_procedure(
                pool, name="proc-test-noreset-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcome = None
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                outcome = await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                )
            assert outcome["verification_state"] == "verified"

            outcome = await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False, context_key="ctx-fail-after",
            )
            assert outcome["verification_state"] == "verified", (
                "verification_state must not be rewritten by a failure after verification"
            )
        finally:
            await _cleanup(pool, "proc-test-noreset")
            await pool.close()

    asyncio.run(_run())


def test_circuit_breaker_opens_after_five_consecutive_failures():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-breaker")
            result = await capture_procedure(
                pool, name="proc-test-breaker-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcome = None
            for i in range(4):
                outcome = await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=False, context_key=f"ctx-{i}",
                )
                assert outcome["availability"] == "active", f"must stay active before the 5th failure (i={i})"

            outcome = await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False, context_key="ctx-5",
            )
            assert outcome["availability"] == "quarantined"
            assert outcome["verification_stats"]["quarantine_entered_at"] is not None
        finally:
            await _cleanup(pool, "proc-test-breaker")
            await pool.close()

    asyncio.run(_run())


def test_circuit_breaker_closes_after_five_consecutive_successes_while_quarantined():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-close")
            result = await capture_procedure(
                pool, name="proc-test-close-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            for i in range(5):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=False, context_key=f"ctx-{i}",
                )
            row = await pool.fetchrow("SELECT availability FROM procedures WHERE id = $1", row_id)
            assert row["availability"] == "quarantined"

            outcome = None
            for i in range(4):
                outcome = await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True, context_key=f"probe-{i}",
                )
                assert outcome["availability"] == "quarantined", (
                    f"must stay quarantined before the 5th consecutive success (i={i})"
                )

            outcome = await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="probe-5",
            )
            assert outcome["availability"] == "active"
            assert outcome["verification_stats"]["quarantine_entered_at"] is None
        finally:
            await _cleanup(pool, "proc-test-close")
            await pool.close()

    asyncio.run(_run())


def test_a_failure_during_probe_resets_the_close_counter():
    """A failed probe attempt while quarantined must not count toward
    the 5-in-a-row needed to close -- it resets, the same as the
    consecutive_failures counter does on the open side."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-probefail")
            result = await capture_procedure(
                pool, name="proc-test-probefail-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]
            for i in range(5):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=False, context_key=f"ctx-{i}",
                )
            for i in range(3):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True, context_key=f"probe-{i}",
                )
            # A failure resets the streak.
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False, context_key="probe-fail",
            )
            row = await pool.fetchrow(
                "SELECT availability, verification_stats FROM procedures WHERE id = $1", row_id
            )
            assert row["availability"] == "quarantined"
            assert row["verification_stats"]["consecutive_successes_since_quarantine"] == 0
        finally:
            await _cleanup(pool, "proc-test-probefail")
            await pool.close()

    asyncio.run(_run())


def test_quarantine_disables_after_14_days():
    """Time-driven escalation, independent of the outcome-driven circuit
    breaker -- confirmed by directly backdating quarantine_entered_at
    rather than waiting 14 real days."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-quarantine-disable")
            result = await capture_procedure(
                pool, name="proc-test-quarantine-disable-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]
            for i in range(5):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=False, context_key=f"ctx-{i}",
                )
            row = await pool.fetchrow("SELECT availability FROM procedures WHERE id = $1", row_id)
            assert row["availability"] == "quarantined"

            # Not yet 14 days -- must not disable.
            fresh = await check_quarantine_and_disable(pool, row_id)
            assert fresh["availability"] == "quarantined"

            # Backdate quarantine_entered_at to 15 days ago -- real state
            # mutation, not a mocked clock.
            old_ts = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
            await pool.execute(
                "UPDATE procedures SET verification_stats = "
                "jsonb_set(verification_stats, '{quarantine_entered_at}', to_jsonb($2::text)) "
                "WHERE id = $1",
                row_id, old_ts,
            )
            disabled = await check_quarantine_and_disable(pool, row_id)
            assert disabled["availability"] == "disabled"
        finally:
            await _cleanup(pool, "proc-test-quarantine-disable")
            await pool.close()

    asyncio.run(_run())


def test_utility_is_none_before_any_execution():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-utility-none")
            result = await capture_procedure(
                pool, name="proc-test-utility-none-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            utility = await compute_utility(pool, result["id"])
            assert utility is None
        finally:
            await _cleanup(pool, "proc-test-utility-none")
            await pool.close()

    asyncio.run(_run())


def test_negative_utility_procedure_gets_retired_even_if_verified():
    """Minton's finding, confirmed directly: a procedure can be correct
    (verified) and still get retired because matching costs more than
    it saves -- the retirement is orthogonal to the failure-driven
    verification/circuit-breaker machinery above."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-negutil")
            result = await capture_procedure(
                pool, name="proc-test-negutil-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            # Drive it to verified with real, cheap successes.
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                    match_cost=100.0, realised_savings=0.01,  # deliberately terrible ratio
                )
            row = await pool.fetchrow(
                "SELECT verification_state, verification_stats FROM procedures WHERE id = $1", row_id
            )
            assert row["verification_state"] == "verified"

            # times_reused defaults to 0 -- set it explicitly so
            # application_frequency * average_savings is a real,
            # non-degenerate number for this test.
            await pool.execute(
                "UPDATE procedures SET verification_stats = "
                "jsonb_set(verification_stats, '{times_reused}', '5') WHERE id = $1",
                row_id,
            )

            utility = await compute_utility(pool, row_id)
            assert utility is not None and utility < 0, f"expected negative utility, got {utility}"

            retired_ids = await retire_negative_utility_procedures(pool)
            assert row_id in retired_ids

            final = await pool.fetchrow("SELECT verification_state FROM procedures WHERE id = $1", row_id)
            assert final["verification_state"] == "retired"
        finally:
            await _cleanup(pool, "proc-test-negutil")
            await pool.close()

    asyncio.run(_run())


def test_positive_utility_procedure_is_not_retired():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-posutil")
            result = await capture_procedure(
                pool, name="proc-test-posutil-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-1",
                match_cost=1.0, realised_savings=100.0,
            )
            await pool.execute(
                "UPDATE procedures SET verification_stats = "
                "jsonb_set(verification_stats, '{times_reused}', '5') WHERE id = $1",
                row_id,
            )
            utility = await compute_utility(pool, row_id)
            assert utility is not None and utility > 0

            retired_ids = await retire_negative_utility_procedures(pool)
            assert row_id not in retired_ids
        finally:
            await _cleanup(pool, "proc-test-posutil")
            await pool.close()

    asyncio.run(_run())


def test_record_execution_outcome_on_unknown_procedure_raises():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with pytest.raises(ProcedureNotFound):
                await record_execution_outcome(
                    pool, procedure_row_id="00000000-0000-0000-0000-000000000000",
                    success=True, context_key="ctx",
                )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_concurrent_outcome_recording_does_not_lose_updates():
    """Real concurrency test: the row-locked transaction
    (SELECT ... FOR UPDATE) must serialize concurrent
    record_execution_outcome() calls on the SAME procedure, not lose
    some of their attempts/successes counts to a race."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            await _cleanup(pool, "proc-test-concurrent")
            result = await capture_procedure(
                pool, name="proc-test-concurrent-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            N = 30
            await asyncio.gather(*[
                record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True, context_key=f"ctx-{i}",
                )
                for i in range(N)
            ])

            row = await pool.fetchrow("SELECT verification_stats FROM procedures WHERE id = $1", row_id)
            assert row["verification_stats"]["attempts"] == N
            assert row["verification_stats"]["successes"] == N
            assert row["verification_stats"]["distinct_contexts"] == N
        finally:
            await _cleanup(pool, "proc-test-concurrent")
            await pool.close()

    asyncio.run(_run())


def test_verified_transition_engine_trigger_actually_fires_live():
    """Phase 1 (memory-substrate map, this pass): db/30's
    `tg_procedures_verified_requires_evidence` trigger was previously only
    proven by reading its SQL text (test_wave3_tenancy_adoption.py) --
    never fired against a real running Postgres. Real proof: a raw UPDATE
    to `verified` with zero supporting evidence must be rejected by the
    ENGINE itself (not just application code choosing not to attempt it),
    and the identical UPDATE must succeed once real supporting evidence
    exists for that exact procedure version."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-trigger")
            result = await capture_procedure(
                pool, name="proc-test-trigger-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]
            version = (await pool.fetchrow(
                "SELECT version FROM procedures WHERE id = $1", row_id
            ))["version"]

            # No evidence at all yet -- the engine itself must refuse.
            with pytest.raises(asyncpg.exceptions.RaiseError, match="invariant #3"):
                await pool.execute(
                    "UPDATE procedures SET verification_state = 'verified' WHERE id = $1",
                    row_id,
                )

            # Real supporting evidence for THIS exact version.
            await pool.execute(
                """
                INSERT INTO evidence (
                    id, evidence_type, target_type, target_id, target_version,
                    direction, strength_score, strength_method,
                    outcome_status, success_criteria, created_by, visibility
                ) VALUES (
                    gen_random_uuid(), 'execution_result', 'procedure', $1, $2,
                    'supports', 1.0, 'recorded_outcome',
                    'success', '{"predicate": "test-recorded"}'::jsonb,
                    'test_verified_trigger', 'public'
                )
                """,
                row_id, version,
            )

            # Same UPDATE, now with real evidence backing it -- must succeed.
            await pool.execute(
                "UPDATE procedures SET verification_state = 'verified' WHERE id = $1",
                row_id,
            )
            row = await pool.fetchrow("SELECT verification_state FROM procedures WHERE id = $1", row_id)
            assert row["verification_state"] == "verified"
        finally:
            await _cleanup(pool, "proc-test-trigger")
            await pool.close()

    asyncio.run(_run())


def test_mark_procedure_stale_is_one_directional_and_audited():
    """Phase 4 (memory-substrate map, this pass): `mark_procedure_stale`
    must actually flip the real `staleness` column, be idempotent (a
    second real-world drift detection against an already-stale procedure
    is a no-op, not a duplicate ChangeSet entry), and leave a real
    ChangeSet audit row -- same convention as approve_procedure/
    reject_procedure/check_quarantine_and_disable."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-stale")
            result = await capture_procedure(
                pool, name="proc-test-stale-1", goal="g",
                invariants=[{"kind": "numeric", "expr": "pandas_version >= 2.0"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            row = await pool.fetchrow("SELECT staleness FROM procedures WHERE id = $1", row_id)
            assert row["staleness"] == "fresh"

            updated = await mark_procedure_stale(
                pool, procedure_row_id=row_id,
                reason="pandas_version >= 2.0 contradicted by probed bindings {'pandas_version': 1.5}",
                detected_by="test_mark_procedure_stale",
            )
            assert updated["staleness"] == "stale"

            # Idempotent: an already-stale procedure stays a no-op, not a
            # second ChangeSet entry.
            again = await mark_procedure_stale(
                pool, procedure_row_id=row_id, reason="re-detected",
                detected_by="test_mark_procedure_stale",
            )
            assert again["staleness"] == "stale"

            changesets = await pool.fetch(
                "SELECT cs.reason FROM change_sets cs "
                "JOIN change_set_operations op ON op.change_set_id = cs.id "
                "WHERE op.target_table = 'procedures' AND op.target_id = $1 "
                "ORDER BY cs.created_at",
                row_id,
            )
            assert len(changesets) == 1, (
                "the second (no-op) call must not append a duplicate ChangeSet "
                f"for THIS procedure -- got {len(changesets)}"
            )
            assert "fresh -> stale" in changesets[0]["reason"]

            # The real, load-bearing consequence: applicability.py's hard
            # cascade already excludes staleness='stale' rows (this was
            # always enforced) -- now confirm a REAL row this function
            # marked stale is actually caught by it, closing the loop
            # between "a producer exists" and "the existing consumer
            # actually sees what it produces".
            full_row = await get_procedure(pool, row_id)
            outcome = await check_hard_constraints(pool, full_row, require_verified=False)
            assert outcome.applicable is False
            assert "staleness" in outcome.failed_constraints
        finally:
            await _cleanup(pool, "proc-test-stale")
            await pool.close()

    asyncio.run(_run())


def test_reproduction_evidence_is_recorded_distinctly_from_execution_result():
    """Phase 2 (memory-substrate map, this pass): `record_execution_outcome`'s
    new `evidence_type` parameter must actually reach the stored evidence
    row, not just be accepted and silently coerced back to the default.
    Real proof, not a mock: query the `evidence` table directly and confirm
    both the default `execution_result` row and an explicit `reproduction`
    row exist, each independently countable toward
    `procedure_evidence_stats.independent_supporting_required` -- the exact
    view db/30's verified-requires-evidence engine trigger reads."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-repro")
            result = await capture_procedure(
                pool, name="proc-test-repro-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            # Organic reuse -- the default, unchanged behavior.
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-organic",
            )
            # Deliberate reproduction attempt -- the new path.
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-reproduce",
                evidence_type="reproduction",
            )

            rows = await pool.fetch(
                "SELECT evidence_type, outcome_status, direction FROM evidence "
                "WHERE target_type = 'procedure' AND target_id = $1 ORDER BY evidence_type",
                row_id,
            )
            types = sorted(r["evidence_type"] for r in rows)
            assert types == ["execution_result", "reproduction"], (
                "both evidence_type values must be persisted verbatim, "
                f"not silently collapsed to one -- got {types}"
            )
            for r in rows:
                assert r["outcome_status"] == "success"
                assert r["direction"] == "supports"

            stats = await pool.fetchrow(
                "SELECT independent_supporting_required FROM procedure_evidence_stats "
                "WHERE procedure_row_id = $1", row_id,
            )
            # Two ungrouped rows of the required types -> two independent
            # supporting rows (COALESCE(independence_group, id::text)
            # self-groups each), confirming db/30's gate sees BOTH kinds
            # of evidence, not just execution_result.
            assert stats["independent_supporting_required"] == 2
        finally:
            await _cleanup(pool, "proc-test-repro")
            await pool.close()

    asyncio.run(_run())


def test_transfer_tier_reproduction_is_distinguishable_from_same_repo_reproduction():
    """V1 spec Phase 9 ("Replay A / Replay B / Transfer C / Transfer D",
    tracked as SEPARATE evidence): `reproduce_procedure`'s new
    `transfer_repo_path` slice (Transfer C -- "different repository,
    same task family") must produce a real, separately-queryable
    `reproduction` evidence row from any same-repo (Replay A)
    reproduction that ran in the same call, using the chosen design --
    the EXISTING `reproduction` evidence_type plus a structured
    `context_key` tier convention (`reproduction:<basename>` for
    same-instance, `reproduction:transfer:<basename>-><basename>` for
    transfer), not a new DB evidence_type.

    Tests at the level `reproduce_procedure` itself calls into
    (`record_execution_outcome` + the real `check_invariants_async` /
    `probe_environment` staleness primitives) rather than running a full
    sandboxed agent -- the sandboxed execution path is already proven
    live (test_full_chain_live.py); the goal here is proving the
    EVIDENCE DISTINCTION is real and queryable.

    Also proves the staleness check the tool runs per-repo is genuinely
    per-repo, not a shared/stale binding: seeds a 'same' repo pinning a
    satisfying pandas version and a 'transfer' repo pinning a violating
    one, and confirms `check_invariants_async` disagrees between them
    when each is probed independently -- exactly what `reproduce_procedure`
    does before attempting a transfer run against a target whose real
    environment doesn't qualify.
    """
    from app.services.environment_facts import invariant_bindings_from_facts, probe_environment
    from app.services.invariants import check_invariants_async

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        same_dir = tempfile.mkdtemp(prefix="proc_test_transfer_same_")
        transfer_dir = tempfile.mkdtemp(prefix="proc_test_transfer_xfer_")
        try:
            await _cleanup(pool, "proc-test-transfer")
            invariants = [{"kind": "numeric", "expr": "pandas_version >= 2.0"}]
            result = await capture_procedure(
                pool, name="proc-test-transfer-1", goal="g",
                invariants=invariants,
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            # Seed two REAL, distinct repo checkouts on disk -- same
            # FRESH_REPO/STALE_REPO pattern as test_full_chain_live.py,
            # one satisfying the procedure's invariant, one violating it.
            with open(os.path.join(same_dir, "requirements.txt"), "w") as f:
                f.write("pandas==2.1.0\n")
            with open(os.path.join(transfer_dir, "requirements.txt"), "w") as f:
                f.write("pandas==1.5.3\n")

            # Prove the staleness primitive reproduce_procedure calls is
            # genuinely per-repo: each target probes its OWN real
            # filesystem, not a shared binding.
            same_bindings = invariant_bindings_from_facts(probe_environment(same_dir))
            transfer_bindings = invariant_bindings_from_facts(probe_environment(transfer_dir))
            assert same_bindings.get("pandas_version") == 2.1
            assert transfer_bindings.get("pandas_version") == 1.5
            same_check = await check_invariants_async(invariants, same_bindings)
            transfer_check = await check_invariants_async(invariants, transfer_bindings)
            assert not same_check.violated, (
                "the same-instance repo satisfies pandas_version >= 2.0 -- "
                "reproduce_procedure would proceed to a real agent run here"
            )
            assert transfer_check.violated == ["pandas_version >= 2.0"], (
                "the transfer repo's OWN probed environment violates the "
                "invariant -- reproduce_procedure must detect this against "
                "the TRANSFER target specifically, not skip the check"
            )

            same_basename = os.path.basename(same_dir)
            transfer_basename = os.path.basename(transfer_dir)

            # Replay A (same-instance) -- byte-for-byte the same
            # context_key convention reproduce_procedure has always used.
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True,
                context_key=f"reproduction:{same_basename}",
                evidence_type="reproduction",
            )
            # Transfer C -- the new tier's context_key convention.
            transfer_context_key = f"reproduction:transfer:{same_basename}->{transfer_basename}"
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True,
                context_key=transfer_context_key,
                evidence_type="reproduction",
            )

            # Real SQL proof, not prose: both rows exist, both are typed
            # `reproduction` (no new evidence_type), and the tier is
            # queryable back out via context_key alone.
            rows = await pool.fetch(
                "SELECT context_key, evidence_type, outcome_status, direction "
                "FROM evidence WHERE target_type = 'procedure' AND target_id = $1 "
                "ORDER BY context_key",
                row_id,
            )
            assert len(rows) == 2
            for r in rows:
                assert r["evidence_type"] == "reproduction"
                assert r["outcome_status"] == "success"
                assert r["direction"] == "supports"

            same_repo_rows = await pool.fetch(
                "SELECT context_key FROM evidence WHERE target_type = 'procedure' "
                "AND target_id = $1 AND context_key LIKE 'reproduction:%' "
                "AND context_key NOT LIKE 'reproduction:transfer:%'",
                row_id,
            )
            transfer_rows = await pool.fetch(
                "SELECT context_key FROM evidence WHERE target_type = 'procedure' "
                "AND target_id = $1 AND context_key LIKE 'reproduction:transfer:%'",
                row_id,
            )
            assert [r["context_key"] for r in same_repo_rows] == [f"reproduction:{same_basename}"]
            assert [r["context_key"] for r in transfer_rows] == [transfer_context_key]

            # Both independently count toward the verified gate's
            # required-evidence statistic -- a transfer-tier row is not
            # a second-class citizen next to a same-repo one.
            stats = await pool.fetchrow(
                "SELECT independent_supporting_required FROM procedure_evidence_stats "
                "WHERE procedure_row_id = $1", row_id,
            )
            assert stats["independent_supporting_required"] == 2
        finally:
            await _cleanup(pool, "proc-test-transfer")
            await pool.close()
            shutil.rmtree(same_dir, ignore_errors=True)
            shutil.rmtree(transfer_dir, ignore_errors=True)


def test_verification_stats_stays_in_lockstep_with_real_evidence_rows():
    """core-a audit (verification/evidence authoritative source): proves
    `procedures.verification_stats` never drifts from the real `evidence`
    rows it is supposed to summarize. `record_execution_outcome` is the
    ONLY writer of `procedures.verification_stats` in this codebase (grep
    confirms it -- no second, independent counter-update path exists), and
    it inserts the evidence row and updates the counters in the SAME
    transaction (see that function's own docstring/WAVE-3 comment). This
    test locks that invariant in with a real count, not a code-reading
    argument: after a mixed sequence of successes/failures, `attempts`/
    `successes` on the procedures row must equal the actual row counts in
    `evidence` for that exact target_id/target_version -- queried
    independently, not read back from the same counters."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-lockstep")
            result = await capture_procedure(
                pool, name="proc-test-lockstep-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            outcomes = [True, True, False, True, False, True]
            for i, success in enumerate(outcomes):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=success,
                    context_key=f"ctx-{i % 3}",
                )

            row = await pool.fetchrow(
                "SELECT verification_stats, version FROM procedures WHERE id = $1", row_id
            )
            stats = row["verification_stats"]

            evidence_rows = await pool.fetch(
                "SELECT outcome_status FROM evidence WHERE target_type = 'procedure' "
                "AND target_id = $1 AND target_version = $2",
                row_id, row["version"],
            )
            real_attempts = len(evidence_rows)
            real_successes = sum(1 for r in evidence_rows if r["outcome_status"] == "success")

            assert real_attempts == len(outcomes), (
                "one evidence row must exist per record_execution_outcome call"
            )
            assert stats["attempts"] == real_attempts, (
                f"cached attempts counter ({stats['attempts']}) drifted from real "
                f"evidence row count ({real_attempts})"
            )
            assert stats["successes"] == real_successes, (
                f"cached successes counter ({stats['successes']}) drifted from real "
                f"evidence success-row count ({real_successes})"
            )
            assert real_successes == sum(outcomes)
        finally:
            await _cleanup(pool, "proc-test-lockstep")
            await pool.close()

    asyncio.run(_run())

    asyncio.run(_run())
