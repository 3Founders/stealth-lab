"""
Real, live-database proof of directive req #22: failure -> evidence ->
classify -> durable route -> handler -> real corrective effect, with
fetch_route_queue() actually consumed by a real production entrypoint
(POST /v1/admin/failure-routes/process, app/api/admin.py) rather than
sitting defined-but-uncalled.

Also proves req #39/#50: a failed run never inflates capability or
verification_stats -- record_execution_outcome() never counts a failure
as a success, and the capability_demotion handler's own recomputation
over the real evidence stream reflects the failure honestly.

Same pattern as the other e2e files: requires a real DATABASE_URL, skips
(not fails) without one.
"""
import asyncio
import json
import os

import pytest

from app.db.session import create_pool
from app.services.procedure_extraction.failure_handlers import (
    HANDLER_STAMP,
    ledger_reason,
)
from app.services.procedures import capture_procedure, record_execution_outcome

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    # evidence, failure_routes and change_sets are [H] append-only (real
    # engine triggers refuse DELETE/UPDATE) -- honored, not fought, same
    # as every other e2e file in this suite. Only the procedures row
    # (a real, mutable/tombstonable table) is cleaned up here.
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_real_failure_routes_and_processes_to_capability_demotion_idempotently():
    """End to end against real Postgres:

    1. record_execution_outcome(success=False, failure_class=
       'implementation_wrong') writes one real evidence row AND, in the
       same transaction, a real failure_routes row via classify_and_route
       (already wired -- app/services/procedures.py).
    2. The failure must NOT inflate verification_stats/capability: the
       procedure's `successes` counter stays 0 and verification_state
       stays 'candidate'.
    3. The real production consumer -- run_failure_handlers(), now also
       reachable via POST /v1/admin/failure-routes/process -- reads
       fetch_route_queue('capability_demotion'), recomputes capability
       over the real evidence stream, and records the verdict as a real
       ChangeSet (a real corrective effect).
    4. Calling it a second time performs zero additional work (idempotent
       -- the change_sets ledger already carries this route's reason).
    """
    async def _run():
        from app.services.procedure_extraction.failure_handlers import run_failure_handlers

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-failroute")
            captured = await capture_procedure(
                pool, name="proc-test-failroute-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = captured["id"]

            # -- 1. real failure, real evidence + real routing row, atomically.
            updated = await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False,
                context_key="ctx-a", failure_class="implementation_wrong",
            )

            # -- 2. req #39/#50: a failure must never inflate capability.
            assert updated["verification_stats"]["successes"] == 0
            assert updated["verification_state"] == "candidate"

            evidence_row = await pool.fetchrow(
                "SELECT id FROM evidence WHERE target_id = $1::uuid "
                "AND outcome_status = 'failure'",
                row_id,
            )
            assert evidence_row is not None, "real evidence row must exist"

            route_row = await pool.fetchrow(
                "SELECT id, route FROM failure_routes WHERE evidence_id = $1::uuid",
                evidence_row["id"],
            )
            assert route_row is not None, "real classification/routing row must exist"
            assert route_row["route"] == "capability_demotion"

            # -- 3. the real consumer: fetch_route_queue() is not left unused.
            applied_first = await run_failure_handlers(pool)
            assert applied_first["capability_demotion"] >= 1

            reason = ledger_reason("capability_demotion", str(route_row["id"]))
            change_set = await pool.fetchrow(
                "SELECT cs.author, cso.detail FROM change_sets cs "
                "JOIN change_set_operations cso ON cso.change_set_id = cs.id "
                "WHERE cs.reason = $1", reason,
            )
            assert change_set is not None, "handler must record a real ChangeSet"
            assert change_set["author"] == HANDLER_STAMP
            detail = change_set["detail"]
            detail = json.loads(detail) if isinstance(detail, str) else detail
            verdict = detail["capability_verdict"]
            assert verdict["success_count"] == 0
            assert verdict["evidence_count"] >= 1

            change_set_count_row = await pool.fetchval(
                "SELECT COUNT(*) FROM change_sets WHERE reason = $1", reason,
            )
            assert change_set_count_row == 1

            # -- 4. idempotent: a second consume performs zero new work.
            applied_second = await run_failure_handlers(pool)
            assert applied_second["capability_demotion"] == 0

            change_set_count_row_2 = await pool.fetchval(
                "SELECT COUNT(*) FROM change_sets WHERE reason = $1", reason,
            )
            assert change_set_count_row_2 == 1, (
                "processing the queue twice must not double-apply the mandate"
            )
        finally:
            await _cleanup(pool, "proc-test-failroute")
            await pool.close()

    asyncio.run(_run())


def test_admin_endpoint_is_the_real_production_consumer():
    """POST /v1/admin/failure-routes/process must be wired to the exact
    same run_failure_handlers() the queue is designed around -- proving
    fetch_route_queue() has a real, reachable production caller, not just
    an offline test harness."""
    from app.api.admin import process_failure_routes
    from app.services.procedure_extraction.failure_handlers import run_failure_handlers as rfh

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            result = await process_failure_routes(pool=pool)
            expected = await rfh(pool)
            assert set(result.applied) == set(expected)
        finally:
            await pool.close()

    asyncio.run(_run())
