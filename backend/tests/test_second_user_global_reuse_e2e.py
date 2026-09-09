"""
Real, live-database proving test for the full local-private ->
publish -> global-candidate -> independently-verified -> SECOND user's
own reuse lifecycle. Same DATABASE_URL-gated pattern as
test_publish_e2e.py / test_solution_search_e2e.py -- skips (not fails)
without a real DATABASE_URL.

No step in this file seeds the outcome directly with a raw UPDATE.
Every state transition goes through the real production functions:
  - LocalProcedureStore.capture_local_procedure /
    record_local_execution_outcome  (User A's real local verification)
  - publish_local_procedure                       (explicit publish)
  - record_execution_outcome (procedures.py)       (real ticket-13 evidence)
  - approve_procedure (procedures.py)              (real human sign-off)
  - find_applicable_procedures (applicability.py)  (User B's real search)
  - compile_plan (execution/plans.py)              (User B's real plan)
  - record_execution_outcome again, as User B       (User B's own evidence)

SEQUENCE PROVEN (each numbered step matches the task spec):
  1. User A captures a local procedure and runs it locally enough times
     to cross the REAL local verification threshold
     (MIN_SUCCESSES_FOR_VERIFIED / MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
     the exact arithmetic record_local_execution_outcome implements --
     imported from procedures.py, not re-declared here).
  2. User A explicitly publishes it -> a real `procedures` row,
     created_by/owner_id = user A, verification_state='candidate',
     approval_status='proposed' (capture_procedure's own untouched
     default) -- a real GLOBAL CANDIDATE, not auto-verified.
  3. The row is driven to real global verified+approved via
     record_execution_outcome (crossing the SAME ticket-13 threshold a
     second, independent time, this time against the global row) and
     approve_procedure -- no direct UPDATE anywhere in this file.
  4. User B (a distinct AccessScope.for_user id, never seen before this
     point) finds it via find_applicable_procedures -- the real
     automatic-selection cascade a second, unrelated user would hit.
  5. User B compiles a real plan against it (compile_plan) and records a
     NEW execution_outcome as themselves (owner_id=user_b).
  6. The procedure's evidence stream now carries a row that is
     genuinely User B's own (owner_id=user_b, a context_key User B
     chose) -- proven by querying the real `evidence` table, not by
     trusting the return value alone.

A separate assertion in the same file proves the mirror-image case: a
User A local procedure that is NEVER published has no corresponding
global `procedures` row at all, so User B's real global search
structurally cannot surface it -- there is nothing in Postgres to find.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from uuid import uuid4

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

USER_A = "user-a@example.com"
USER_B = "user-b@example.com"


def _run(coro):
    return asyncio.run(coro)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL "
        "AND target_id IN (SELECT id FROM procedures WHERE name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_second_user_finds_and_independently_reuses_a_published_procedure():
    async def _run_test():
        from app.db.session import create_pool
        from app.execution.plans import compile_plan
        from app.local_agent.local_store import LocalProcedureStore
        from app.services.access import AccessScope
        from app.services.applicability import find_applicable_procedures
        from app.services.procedures import (
            MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
            MIN_SUCCESSES_FOR_VERIFIED,
            approve_procedure,
            get_procedure,
            record_execution_outcome,
        )
        from app.services.publish import publish_local_procedure

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        name_prefix = f"second-user-reuse-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, name_prefix)

            with tempfile.TemporaryDirectory() as tmp_dir:
                # ---- STEP 1: User A's real local procedure + real local
                # verification (own SQLite store, nothing global touched
                # yet). ----
                store = LocalProcedureStore(repo_root=tmp_dir)
                local_result = store.capture_local_procedure(
                    name=f"{name_prefix}-restart-the-worker-fleet",
                    goal="restart the worker fleet safely",
                    steps=[{"action": "drain"}, {"action": "restart"}],
                    provenance="system_pending_review",
                    scope_type="user",
                    scope_entity_id="user-a-workspace",
                )
                local_row_id = local_result["id"]

                local_record = None
                for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                    ctx = f"local-ctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}"
                    local_record = store.record_local_execution_outcome(
                        row_id=local_row_id, success=True, context_key=ctx,
                    )
                assert local_record["verification_state"] == "verified", (
                    "fixture sanity: User A's LOCAL track record must actually "
                    "cross the real local threshold"
                )

                # ---- STEP 2: explicit publish -> real global candidate,
                # never auto-verified, identity = the publishing user. ----
                published = await publish_local_procedure(
                    pool,
                    local_store=store,
                    local_row_id=local_row_id,
                    actor_subject=USER_A,
                    scope_type="global",
                )
                global_row_id = published["id"]

                fresh_row = await get_procedure(pool, global_row_id)
                assert fresh_row["verification_state"] == "candidate", (
                    "a freshly published row must be a real candidate, never "
                    "auto-verified just because the local copy was verified"
                )
                assert fresh_row["approval_status"] == "proposed"
                assert fresh_row["created_by"] == USER_A
                assert fresh_row["owner_id"] == USER_A
                assert fresh_row["verification_stats"]["attempts"] == 0, (
                    "zero-inherited-evidence: User A's real local track record "
                    "must not be carried onto the global row"
                )

                # Before independent verification, User B's real automatic
                # search must NOT surface it (require_verified gate, ticket 13).
                # Pool/limit widened for the same reason the positive check
                # below is: this shared live DB carries hundreds of
                # 0-precondition procedures, and applicability.py's
                # no-embedding candidate pre-filter is a bounded
                # `candidate_pool_size`. A default window would let this
                # negative assertion pass for the wrong reason (row simply
                # outside the window); the wide pool makes it prove the
                # verification gate specifically.
                pre_verify_hits = await find_applicable_procedures(
                    pool, access_scope=AccessScope.for_user(USER_B),
                    require_verified=True, limit=5000, candidate_pool_size=20000,
                )
                assert global_row_id not in {str(h["id"]) for h in pre_verify_hits}, (
                    "an unverified candidate must not be automatically "
                    "selectable by a second user yet"
                )

                # ---- STEP 3: real global independent verification --
                # record_execution_outcome MIN_SUCCESSES_FOR_VERIFIED times
                # across MIN_DISTINCT_CONTEXTS_FOR_VERIFIED contexts (the
                # SAME real threshold, now against the GLOBAL row, not
                # inherited from User A's local one), then approve_procedure.
                # No direct UPDATE anywhere in this sequence. ----
                updated = None
                for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                    ctx = f"global-ctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}"
                    updated = await record_execution_outcome(
                        pool, procedure_row_id=global_row_id, success=True,
                        context_key=ctx, owner_id=USER_A,
                    )
                assert updated["verification_state"] == "verified", (
                    "fixture sanity: the global row must actually cross the "
                    "real ticket-13 threshold via record_execution_outcome"
                )

                await approve_procedure(pool, procedure_row_id=global_row_id, approved_by=USER_A)
                approved_row = await get_procedure(pool, global_row_id)
                assert approved_row["approval_status"] == "approved"
                assert approved_row["verification_state"] == "verified"

                # ---- STEP 4: User B (distinct AccessScope), a real
                # automatic search, finds the now-verified+approved
                # procedure. ----
                # Real-corpus live DB: the published procedure carries no
                # goal embedding, so applicability.py places it via the
                # cost-only pre-filter (fewest-precondition procedures, bounded
                # by candidate_pool_size). Hundreds of other 0-precondition
                # procedures live in this shared DB, so the pool/limit are
                # widened to prove RETRIEVABILITY (User B can find the
                # independently-verified procedure at all) rather than its
                # incidental rank inside a small default window -- same idiom
                # test_canonical_personal_memory_e2e.py documents.
                hits = await find_applicable_procedures(
                    pool, access_scope=AccessScope.for_user(USER_B),
                    require_verified=True, limit=5000, candidate_pool_size=20000,
                )
                hit_ids = {str(h["id"]) for h in hits}
                assert global_row_id in hit_ids, (
                    "User B's real find_applicable_procedures call must "
                    "surface the independently-verified published procedure"
                )
                found = next(h for h in hits if str(h["id"]) == global_row_id)
                assert found["name"] == fresh_row["name"]

                # ---- STEP 5: User B compiles a real plan against it and
                # records a NEW execution outcome as themselves. ----
                compiled = compile_plan(
                    procedure_id=found["procedure_id"],
                    procedure_version=found["version"],
                    procedure_row_id=found["id"],
                    procedure_payload=dict(found),
                    task_description=f"{name_prefix}: user B's own real run",
                    scope_type="global",
                    extractor_version="test_second_user_global_reuse_e2e@1",
                    created_by=USER_B,
                    owner_id=USER_B,
                    nodes=[{"order": 0, "goal": "restart the worker fleet safely"}],
                )
                assert compiled.plan.procedure_row_id == found["id"]
                assert compiled.plan.created_by == USER_B

                user_b_context = f"user-b-own-context-{uuid4().hex[:6]}"
                after_b = await record_execution_outcome(
                    pool, procedure_row_id=global_row_id, success=True,
                    context_key=user_b_context, owner_id=USER_B,
                )
                assert after_b["verification_stats"]["attempts"] == MIN_SUCCESSES_FOR_VERIFIED + 1

                # ---- STEP 6: the evidence stream carries a row that is
                # genuinely User B's own -- queried directly, not just
                # trusted from the return value. ----
                b_evidence = await pool.fetch(
                    "SELECT owner_id, context_key, outcome_status FROM evidence "
                    "WHERE target_type = 'procedure' AND target_id = $1::uuid "
                    "AND owner_id = $2 AND t_invalid IS NULL",
                    global_row_id, USER_B,
                )
                assert len(b_evidence) == 1, "exactly one evidence row must exist for User B's own run"
                assert b_evidence[0]["context_key"] == user_b_context
                assert b_evidence[0]["outcome_status"] == "success"

                # User A's evidence rows are still real, distinct, and
                # unaffected -- User B's run neither overwrote nor erased them.
                a_evidence_count = await pool.fetchval(
                    "SELECT count(*) FROM evidence WHERE target_type = 'procedure' "
                    "AND target_id = $1::uuid AND owner_id = $2 AND t_invalid IS NULL",
                    global_row_id, USER_A,
                )
                assert a_evidence_count == MIN_SUCCESSES_FOR_VERIFIED

                # User A's private local data was never touched/visible to
                # User B: the local SQLite row's own track record is still
                # exactly what User A's local calls produced, and the
                # global row carries no local-only bookkeeping fields.
                local_after = store.get_local_procedure(local_row_id)
                assert local_after["verification_stats"]["attempts"] == MIN_SUCCESSES_FOR_VERIFIED, (
                    "User A's local row must be unaffected by anything User B did globally"
                )
                assert "local-ctx-0" not in str(fresh_row.get("domain_payload", {})), (
                    "User A's local context keys must never leak onto the global row"
                )
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    _run(_run_test())


def test_unpublished_private_local_procedure_is_invisible_to_second_user_global_search():
    """The mirror-image case: a User A local procedure that is NEVER
    published has no corresponding global `procedures` row at all --
    User B's real find_applicable_procedures call structurally cannot
    surface it, because there is nothing in Postgres to find."""
    async def _run_test():
        from app.db.session import create_pool
        from app.local_agent.local_store import LocalProcedureStore
        from app.services.access import AccessScope
        from app.services.applicability import find_applicable_procedures
        from app.services.procedures import MIN_SUCCESSES_FOR_VERIFIED

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        name_prefix = f"second-user-private-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, name_prefix)

            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalProcedureStore(repo_root=tmp_dir)
                local_result = store.capture_local_procedure(
                    name=f"{name_prefix}-private-only-procedure",
                    goal="a procedure User A never publishes",
                    provenance="system_pending_review",
                    scope_type="user",
                    scope_entity_id="user-a-workspace",
                )
                store.record_local_execution_outcome(
                    row_id=local_result["id"], success=True, context_key="ctx-only",
                )
                # NOTE: publish_local_procedure is deliberately never called.

                row_in_global_db = await pool.fetchrow(
                    "SELECT id FROM procedures WHERE name = $1",
                    f"{name_prefix}-private-only-procedure",
                )
                assert row_in_global_db is None, (
                    "an unpublished local procedure must never have a "
                    "corresponding global `procedures` row"
                )

                hits = await find_applicable_procedures(
                    pool, access_scope=AccessScope.for_user(USER_B),
                    require_verified=False, limit=200,
                )
                names = {h["name"] for h in hits}
                assert f"{name_prefix}-private-only-procedure" not in names, (
                    "User B's real global search must never surface a local "
                    "procedure User A never published"
                )
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    _run(_run_test())
