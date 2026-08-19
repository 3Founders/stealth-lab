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
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import create_pool
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    ProcedureNotFound,
    capture_procedure,
    check_quarantine_and_disable,
    compute_utility,
    record_execution_outcome,
    retire_negative_utility_procedures,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_capture_procedure_starts_candidate_fresh_active():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-capture")
            result = await capture_procedure(
                pool, name="proc-test-capture-1", goal="fix a failing test",
                steps=[{"action": "locate"}, {"action": "fix"}],
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
            result = await capture_procedure(pool, name="proc-test-promo-1", goal="g")
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
            result2 = await capture_procedure(pool, name="proc-test-promo-2", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-onefail-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-noreset-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-breaker-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-close-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-probefail-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-quarantine-disable-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-utility-none-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-negutil-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-posutil-1", goal="g")
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
            result = await capture_procedure(pool, name="proc-test-concurrent-1", goal="g")
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
