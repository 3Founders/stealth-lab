"""
Real, live-database tests for the economy hardening pass (migration 102
audit -> this hardening pass). Same pattern as test_procedures_e2e.py and
every other *_e2e.py in this suite: requires a real DATABASE_URL (point it
at the throwaway container -- see backend/TESTING_DB.md -- never at
production), skips (not fails) without one.

Covers hardening-spec §14 invariants E, F, G, H, I, J, K, L -- the ones
that genuinely need Postgres (execution_runs/evidence/credit_ledger_events
rows, real concurrency, real transaction rollback). A, B, C, D (client
identity stripping, unauthenticated reads) are offline -- see
tests/test_economy_hardening_offline.py.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.economy import constants as c
from app.economy import credits as credits_service
from app.economy import submissions as submissions_service
from app.economy.verification import VerificationMismatch, record_usage_event
from app.services.procedures import capture_procedure
from app.services.product_model import associate_solution, create_problem
from app.utils.ids import uuid7

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute("DELETE FROM credit_ledger_events WHERE contributor_id LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_usage_events WHERE executed_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedures WHERE created_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM problems WHERE title LIKE $1", f"[{prefix}%")


async def _make_goal(pool, prefix: str) -> str:
    problem = await create_problem(pool, title=f"[{prefix}] a goal", description="d", objective="o", constraints=[], status="open", proposer=None, provenance="system_pending_review", metadata={}, visibility="public", scope_type="global", scope_entity_id=None)
    return str(problem["id"])


async def _make_verified_execution(pool, *, procedure_row_id: str, procedure_id: str, version: int, created_by: str, outcome: str = "success"):
    """Real execution_runs + evidence rows, matching the real shapes
    _verify_execution_chain checks -- not synthetic-and-different-shaped
    fixtures. evidence.created_by MUST be the real OUTCOME_WRITER_STAMP,
    which is exactly the load-bearing fact under test."""
    from app.services.procedures import OUTCOME_WRITER_STAMP

    plan_id, graph_id = str(uuid7()), str(uuid7())
    await pool.execute(
        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, "
        "procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t',$5,$5,$6)",
        plan_id, procedure_id, version, procedure_row_id, "hash", created_by,
    )
    await pool.execute(
        "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h',$3)",
        graph_id, plan_id, created_by,
    )
    run_id = str(uuid7())
    await pool.execute(
        "INSERT INTO execution_runs (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, "
        "status, final_outcome, created_by) VALUES ($1,$2,$3,$4,$5,'succeeded' , $6, $7)".replace(" , ", ", "),
        run_id, plan_id, graph_id, procedure_id, version, outcome, created_by,
    )
    evidence_id = await pool.fetchval(
        "INSERT INTO evidence (id, evidence_type, target_type, target_id, target_version, direction, "
        "strength_score, strength_method, outcome_status, success_criteria, created_by, visibility) "
        "VALUES ($1,'execution_result','procedure',$2,$3,'supports',1.0,'real_execution',$4,'{}'::jsonb,$5,'public') RETURNING id",
        str(uuid7()), procedure_row_id, version, outcome, OUTCOME_WRITER_STAMP,
    )
    return run_id, str(evidence_id)


def test_E_client_cannot_manufacture_verified_success_with_fake_ids():
    """§14 invariant E: posting execution_run_id/evidence_id that simply
    don't exist must be rejected, never silently accepted as verified."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "econtest-e")
            goal_id = await _make_goal(pool, "econtest-e")
            captured = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="econtest-e-owner", owner_id="econtest-e-owner")
            with pytest.raises(VerificationMismatch):
                await record_usage_event(
                    pool, procedure_row_id=captured["id"], executor_subject="econtest-e-owner",
                    execution_run_id=str(uuid7()), evidence_id=str(uuid7()),
                )
        finally:
            await _cleanup(pool, "econtest-e")
            await pool.close()
    asyncio.run(_run())


def test_F_evidence_from_a_different_procedure_is_rejected():
    """§14 invariant G (evidence from another Procedure): evidence whose
    target_id points at a DIFFERENT procedure row must not verify this one,
    even paired with a real, matching execution_run."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "econtest-f")
            goal_id = await _make_goal(pool, "econtest-f")
            p1 = await capture_procedure(pool, name="p1", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="econtest-f-owner", owner_id="econtest-f-owner")
            p2 = await capture_procedure(pool, name="p2", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="econtest-f-owner", owner_id="econtest-f-owner")
            run_id, evidence_id = await _make_verified_execution(pool, procedure_row_id=p1["id"], procedure_id=str(p1["procedure_id"]), version=1, created_by="econtest-f-owner")
            with pytest.raises(VerificationMismatch):
                # Real run+evidence, but for p1 -- claimed against p2.
                await record_usage_event(pool, procedure_row_id=p2["id"], executor_subject="econtest-f-owner", execution_run_id=run_id, evidence_id=evidence_id)
        finally:
            await _cleanup(pool, "econtest-f")
            await pool.close()
    asyncio.run(_run())


def test_H_self_use_never_generates_a_reward():
    """§14 invariant H: the procedure's own contributor executing it,
    verified, must never mint a verified_reuse reward."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "econtest-h")
            goal_id = await _make_goal(pool, "econtest-h")
            owner = "econtest-h-owner"
            captured = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by=owner, owner_id=owner)
            run_id, evidence_id = await _make_verified_execution(pool, procedure_row_id=captured["id"], procedure_id=str(captured["procedure_id"]), version=1, created_by=owner)

            event = await record_usage_event(pool, procedure_row_id=captured["id"], executor_subject=owner, execution_run_id=run_id, evidence_id=evidence_id)
            assert event["is_self_use"] is True
            assert event["outcome_state"] == "verified_success"

            rewards = await credits_service.reward_verified_reuse(pool, usage_event=event)
            assert rewards == []
            balance = await credits_service.get_balance(pool, owner)
            assert balance == 0.0
        finally:
            await _cleanup(pool, "econtest-h")
            await pool.close()
    asyncio.run(_run())


def test_I_duplicate_usage_event_for_the_same_evidence_does_not_double_pay():
    """§14 invariant I: the same evidence_id posted twice must not create
    two usage events or two rewards (migration 103's unique index)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "econtest-i")
            goal_id = await _make_goal(pool, "econtest-i")
            owner, reuser = "econtest-i-owner", "econtest-i-reuser"
            captured = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by=owner, owner_id=owner)
            await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=captured["id"], proposer=owner)
            run_id, evidence_id = await _make_verified_execution(pool, procedure_row_id=captured["id"], procedure_id=str(captured["procedure_id"]), version=1, created_by=reuser)

            event1 = await record_usage_event(pool, procedure_row_id=captured["id"], executor_subject=reuser, execution_run_id=run_id, evidence_id=evidence_id)
            event2 = await record_usage_event(pool, procedure_row_id=captured["id"], executor_subject=reuser, execution_run_id=run_id, evidence_id=evidence_id)
            assert event1["id"] == event2["id"], "a retried usage-event post must return the SAME event, not a new one"

            count = await pool.fetchval("SELECT count(*) FROM procedure_usage_events WHERE evidence_id = $1", evidence_id)
            assert count == 1

            r1 = await credits_service.reward_verified_reuse(pool, usage_event=event1)
            r2 = await credits_service.reward_verified_reuse(pool, usage_event=event2)
            assert len(r1) == 1
            assert r2 == [] or r2[0]["id"] == r1[0]["id"]
            balance = await credits_service.get_balance(pool, owner)
            assert balance == c.VERIFIED_REUSE_REWARD
        finally:
            await _cleanup(pool, "econtest-i")
            await pool.close()
    asyncio.run(_run())


def test_J_concurrent_reward_attempts_cannot_exceed_the_daily_cap():
    """§14 invariant J: 20 concurrent reward attempts for the same
    contributor, with a cap that permits only ~5 rewards worth, must never
    credit more than the cap allows."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=5, max_size=25)
        try:
            await _cleanup(pool, "econtest-j")
            contributor = "econtest-j-contributor"

            # A tiny cap, monkeypatched via the module constant the cap logic
            # actually reads, so this test proves the real code path.
            original_cap = c.DAILY_REWARD_CAP_PER_CONTRIBUTOR
            c.DAILY_REWARD_CAP_PER_CONTRIBUTOR = c.NEW_PROCEDURE_REWARD * 5
            try:
                async def _one_attempt(i: int):
                    return await credits_service._capped_reward(
                        pool, contributor_id=contributor, amount=c.NEW_PROCEDURE_REWARD, reason="new_procedure",
                        submission_id=str(uuid7()),  # distinct submission per attempt -- no idempotency short-circuit
                    )
                results = await asyncio.gather(*[_one_attempt(i) for i in range(20)])
                paid = [r for r in results if r is not None]
                balance = await credits_service.get_balance(pool, contributor)
                assert balance <= c.DAILY_REWARD_CAP_PER_CONTRIBUTOR, f"balance {balance} exceeded cap {c.DAILY_REWARD_CAP_PER_CONTRIBUTOR}"
                assert len(paid) <= 5, f"{len(paid)} of 20 concurrent attempts were paid; cap should allow at most 5"
            finally:
                c.DAILY_REWARD_CAP_PER_CONTRIBUTOR = original_cap
        finally:
            await _cleanup(pool, "econtest-j")
            await pool.close()
    asyncio.run(_run())


def test_K_retrying_an_accepted_submission_review_does_not_double_pay():
    """§14 invariant K."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "econtest-k")
            goal_id = await _make_goal(pool, "econtest-k")
            submitter = "econtest-k-submitter"
            submission = await submissions_service.create_procedure_submission(
                pool, goal_id=goal_id, submission_type="new", name="do it", steps=["s"], rationale="because",
                actor_subject=submitter, provenance="system_pending_review", scope_type="global",
            )
            r1 = await submissions_service.review_procedure_submission(pool, submission_id=submission["id"], decision="accepted", actor_subject="econtest-k-reviewer")
            r2 = await submissions_service.review_procedure_submission(pool, submission_id=submission["id"], decision="accepted", actor_subject="econtest-k-reviewer")
            assert r1["status"] == r2["status"] == "accepted"

            balance = await credits_service.get_balance(pool, submitter)
            assert balance == c.NEW_PROCEDURE_REWARD, f"expected exactly one reward's worth, got balance {balance}"
            n = await pool.fetchval("SELECT count(*) FROM credit_ledger_events WHERE submission_id = $1", submission["id"])
            assert n == 1
        finally:
            await _cleanup(pool, "econtest-k")
            await pool.close()
    asyncio.run(_run())


def test_L_partial_submission_failure_leaves_a_safe_incomplete_state(monkeypatch):
    """§14 invariant L: if capture_procedure fails mid-submission, the
    submission row must still exist (visibly incomplete), never an
    orphaned Procedure with no submission trail."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "econtest-l")
            goal_id = await _make_goal(pool, "econtest-l")

            async def boom(*a, **kw):
                raise RuntimeError("simulated capture_procedure failure")

            monkeypatch.setattr(submissions_service, "capture_procedure", boom)
            with pytest.raises(RuntimeError):
                await submissions_service.create_procedure_submission(
                    pool, goal_id=goal_id, submission_type="new", name="do it", steps=["s"], rationale="because",
                    actor_subject="econtest-l-submitter", provenance="system_pending_review", scope_type="global",
                )
            row = await pool.fetchrow("SELECT * FROM procedure_submissions WHERE goal_id = $1 AND submitted_by = $2", goal_id, "econtest-l-submitter")
            assert row is not None, "the submission row must still exist after a downstream failure"
            assert row["procedure_row_id"] is None
            assert row["status"] == "needs_review"
        finally:
            await _cleanup(pool, "econtest-l")
            await pool.close()
    asyncio.run(_run())
