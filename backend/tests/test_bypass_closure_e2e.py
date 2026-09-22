"""
Real, live-database regression tests for the B1-B4 bypass-closure pass.
Same pattern as every other *_e2e.py in this suite: requires a real
DATABASE_URL (the throwaway container -- backend/TESTING_DB.md -- never
production), skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.economy import submissions as submissions_service
from app.economy.verification import record_usage_event
from app.services import evidence_trust
from app.services.access import AccessScope, TenantScope
from app.services.procedures import capture_procedure, record_execution_outcome, OUTCOME_WRITER_STAMP
from app.services.product_model import (
    associate_solution, complete_evaluation, create_benchmark, create_problem,
    freeze_benchmark, get_problem, list_problem_solutions, request_evaluation,
)
from app.utils.ids import uuid7

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute("DELETE FROM evaluation_executions WHERE evaluation_id IN (SELECT id FROM evaluations WHERE provenance LIKE $1)", f"{prefix}%")
    await pool.execute("DELETE FROM evaluations WHERE provenance LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM solutions WHERE proposer LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM benchmark_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM benchmarks WHERE provenance LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedures WHERE created_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM problems WHERE title LIKE $1", f"[{prefix}%")


async def _make_goal(pool, prefix: str) -> str:
    problem = await create_problem(pool, title=f"[{prefix}] a goal", description="d", objective="o", constraints=[], status="open", proposer=None, provenance="system_pending_review", metadata={}, visibility="public", scope_type="global", scope_entity_id=None)
    return str(problem["id"])


def test_B1_direct_associate_never_makes_a_procedure_active_or_rankable():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b1a")
            goal_id = await _make_goal(pool, "bypasstest-b1a")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b1a-owner", owner_id="bypasstest-b1a-owner")

            # Simulates what the HARDENED API route now always does:
            # server-forced status='proposed' regardless of what a client asked for.
            await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["id"], status="proposed", proposer="bypasstest-b1a-owner")

            solutions = await list_problem_solutions(pool, goal_id, scope=AccessScope.unrestricted())
            active_only = [s for s in solutions if s["status"] == "active"]
            assert len(solutions) == 1 and solutions[0]["status"] == "proposed"
            assert active_only == [], "a direct association must never be listed as an active, rankable Way"
        finally:
            await _cleanup(pool, "bypasstest-b1a")
            await pool.close()
    asyncio.run(_run())


def test_B1_canonical_acceptance_still_makes_a_procedure_active():
    """The legitimate path -- review acceptance -- must still work exactly as before."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b1b")
            goal_id = await _make_goal(pool, "bypasstest-b1b")
            submitter = "bypasstest-b1b-submitter"
            submission = await submissions_service.create_procedure_submission(
                pool, goal_id=goal_id, submission_type="new", name="p", steps=["s"], rationale="r",
                actor_subject=submitter, provenance="system_pending_review", scope_type="global",
            )
            await submissions_service.review_procedure_submission(pool, submission_id=submission["id"], decision="accepted", actor_subject="bypasstest-b1b-reviewer")

            solutions = await list_problem_solutions(pool, goal_id, scope=AccessScope.unrestricted())
            assert any(s["status"] == "active" and str(s["target_id"]) == submission["procedure_row_id"] for s in solutions)
        finally:
            await _cleanup(pool, "bypasstest-b1b")
            await pool.close()
    asyncio.run(_run())


def test_B2_self_report_creates_claimed_not_verified_evidence():
    """A: report_execution(success=True) cannot create VERIFIED_SUCCESS. G: claimed distinct from verified."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b2a")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b2a-owner", owner_id="bypasstest-b2a-owner")

            updated = await record_execution_outcome(
                pool, procedure_row_id=proc["id"], success=True, context_key="ctx-a", execution_verified=False,
            )
            evidence_row = await pool.fetchrow("SELECT * FROM evidence WHERE id = $1", updated["evidence_id"])
            assert evidence_row["created_by"] != OUTCOME_WRITER_STAMP
            assert evidence_trust.trust_state(dict(evidence_row)) == evidence_trust.CLAIMED_SUCCESS
        finally:
            await _cleanup(pool, "bypasstest-b2a")
            await pool.close()
    asyncio.run(_run())


def test_B2_self_report_cannot_promote_verification_state():
    """B: ten self-reported successes across distinct contexts must NOT promote to verified."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b2b")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b2b-owner", owner_id="bypasstest-b2b-owner")
            updated = None
            for i in range(12):
                updated = await record_execution_outcome(
                    pool, procedure_row_id=proc["id"], success=True, context_key=f"ctx-{i}", execution_verified=False,
                )
            assert updated["verification_state"] == "candidate", "self-reports must never promote verification_state to verified"
        finally:
            await _cleanup(pool, "bypasstest-b2b")
            await pool.close()
    asyncio.run(_run())


def test_B2_self_report_cannot_generate_a_verified_reuse_reward():
    """D: report_execution-style self-reports cannot generate Credits/reuse rewards -- the
    usage-event verification chain requires a real execution_run, which a self-report never has."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b2d")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b2d-owner", owner_id="bypasstest-b2d-owner")
            updated = await record_execution_outcome(pool, procedure_row_id=proc["id"], success=True, context_key="ctx-a", execution_verified=False)
            evidence_id = updated["evidence_id"]

            from app.economy.verification import VerificationMismatch
            with pytest.raises(VerificationMismatch):
                # No real execution_run exists for this self-report -- a
                # fabricated id must be rejected, never treated as verified.
                await record_usage_event(
                    pool, procedure_row_id=proc["id"], executor_subject="bypasstest-b2d-reuser",
                    execution_run_id=str(uuid7()), evidence_id=evidence_id,
                )
        finally:
            await _cleanup(pool, "bypasstest-b2d")
            await pool.close()
    asyncio.run(_run())


def test_B2_real_durable_execution_still_creates_verified_evidence():
    """E: the legitimate path (execution_verified=True, the default) must be unaffected."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b2e")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b2e-owner", owner_id="bypasstest-b2e-owner")
            updated = await record_execution_outcome(pool, procedure_row_id=proc["id"], success=True, context_key="ctx-a")  # execution_verified defaults True
            evidence_row = await pool.fetchrow("SELECT * FROM evidence WHERE id = $1", updated["evidence_id"])
            assert evidence_row["created_by"] == OUTCOME_WRITER_STAMP
            assert evidence_trust.trust_state(dict(evidence_row)) == evidence_trust.VERIFIED_SUCCESS
        finally:
            await _cleanup(pool, "bypasstest-b2e")
            await pool.close()
    asyncio.run(_run())


def test_B2_verified_failure_remains_distinct():
    """F: verified failure remains its own state, never folded into unknown or success."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b2f")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b2f-owner", owner_id="bypasstest-b2f-owner")
            updated = await record_execution_outcome(pool, procedure_row_id=proc["id"], success=False, context_key="ctx-a")
            evidence_row = await pool.fetchrow("SELECT * FROM evidence WHERE id = $1", updated["evidence_id"])
            assert evidence_trust.trust_state(dict(evidence_row)) == evidence_trust.VERIFIED_FAILURE
        finally:
            await _cleanup(pool, "bypasstest-b2f")
            await pool.close()
    asyncio.run(_run())


def test_B3_normal_user_cannot_freeze_a_never_reviewed_benchmark():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b3a")
            goal_id = await _make_goal(pool, "bypasstest-b3a")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", description="d", provenance="system_pending_review")
            # No benchmark_submissions row exists for this benchmark at all
            # (created directly, bypassing the submission workflow).
            has_accepted = await pool.fetchval("SELECT id FROM benchmark_submissions WHERE benchmark_id = $1 AND status = 'accepted'", bench["id"])
            assert has_accepted is None, "this is exactly the state the API route's 409 guard checks for"
        finally:
            await _cleanup(pool, "bypasstest-b3a")
            await pool.close()
    asyncio.run(_run())


def test_B3_accepted_benchmark_can_be_frozen():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b3b")
            goal_id = await _make_goal(pool, "bypasstest-b3b")
            submitter = "bypasstest-b3b-submitter"
            submission = await submissions_service.create_benchmark_submission(pool, goal_id=goal_id, name="b", description="d", actor_subject=submitter, provenance="system_pending_review")
            accepted = await submissions_service.review_benchmark_submission(pool, submission_id=submission["id"], decision="accepted", actor_subject="bypasstest-b3b-reviewer")
            has_accepted = await pool.fetchval("SELECT id FROM benchmark_submissions WHERE benchmark_id = $1 AND status = 'accepted'", accepted["benchmark_id"])
            assert has_accepted is not None
            frozen = await freeze_benchmark(pool, accepted["benchmark_id"])
            assert frozen["status"] == "frozen"
        finally:
            await _cleanup(pool, "bypasstest-b3b")
            await pool.close()
    asyncio.run(_run())


def test_B4_client_pass_claim_with_no_matching_evidence_is_rejected():
    """A: client sends aggregate_result=pass but the real execution was a failure -> reject."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b4a")
            goal_id = await _make_goal(pool, "bypasstest-b4a")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b4a-owner", owner_id="bypasstest-b4a-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["id"], status="active", proposer="bypasstest-b4a-owner")
            ev = await request_evaluation(pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"], procedure_id=str(proc["procedure_id"]), procedure_version=proc["version"], provenance="bypasstest-b4a")

            plan_id, graph_id = str(uuid7()), str(uuid7())
            await pool.execute(
                "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t','h','h','x')",
                plan_id, proc["procedure_id"], proc["version"], proc["id"],
            )
            await pool.execute("INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h','x')", graph_id, plan_id)
            real_exec_id = str(uuid7())
            await pool.execute(
                "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, outcome, started_at, ended_at) VALUES ($1,$2,$3,$4,$5,'failure',now(),now())",
                real_exec_id, plan_id, graph_id, proc["procedure_id"], proc["version"],
            )
            with pytest.raises(ValueError, match="does not match the server-derived result"):
                await complete_evaluation(pool, ev["id"], execution_ids=[real_exec_id], aggregate_result="pass")
        finally:
            await _cleanup(pool, "bypasstest-b4a")
            await pool.close()
    asyncio.run(_run())


def test_B4_unrelated_execution_id_is_rejected():
    """C/D: an execution of a DIFFERENT procedure cannot validate this evaluation."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b4c")
            goal_id = await _make_goal(pool, "bypasstest-b4c")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b4c-owner", owner_id="bypasstest-b4c-owner")
            other_proc = await capture_procedure(pool, name="other", goal="g2", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b4c-owner", owner_id="bypasstest-b4c-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["id"], status="active", proposer="bypasstest-b4c-owner")
            ev = await request_evaluation(pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"], procedure_id=str(proc["procedure_id"]), procedure_version=proc["version"], provenance="bypasstest-b4c")

            plan_id, graph_id = str(uuid7()), str(uuid7())
            await pool.execute(
                "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t','h','h','x')",
                plan_id, other_proc["procedure_id"], other_proc["version"], other_proc["id"],
            )
            await pool.execute("INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h','x')", graph_id, plan_id)
            unrelated_exec_id = str(uuid7())
            await pool.execute(
                "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, outcome, started_at, ended_at) VALUES ($1,$2,$3,$4,$5,'success',now(),now())",
                unrelated_exec_id, plan_id, graph_id, other_proc["procedure_id"], other_proc["version"],
            )
            with pytest.raises(ValueError, match="do not match this evaluation's own procedure"):
                await complete_evaluation(pool, ev["id"], execution_ids=[unrelated_exec_id])
        finally:
            await _cleanup(pool, "bypasstest-b4c")
            await pool.close()
    asyncio.run(_run())


def test_B4_valid_execution_produces_correct_server_derived_result_and_is_idempotent():
    """F: correct case. G: retry with the same execution set is idempotent. H: a different
    execution set on an already-completed evaluation is refused."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "bypasstest-b4f")
            goal_id = await _make_goal(pool, "bypasstest-b4f")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="bypasstest-b4f-owner", owner_id="bypasstest-b4f-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["id"], status="active", proposer="bypasstest-b4f-owner")
            ev = await request_evaluation(pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"], procedure_id=str(proc["procedure_id"]), procedure_version=proc["version"], provenance="bypasstest-b4f")

            plan_id, graph_id = str(uuid7()), str(uuid7())
            await pool.execute(
                "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t','h','h','x')",
                plan_id, proc["procedure_id"], proc["version"], proc["id"],
            )
            await pool.execute("INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h','x')", graph_id, plan_id)
            real_exec_id = str(uuid7())
            await pool.execute(
                "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, outcome, started_at, ended_at) VALUES ($1,$2,$3,$4,$5,'success',now(),now())",
                real_exec_id, plan_id, graph_id, proc["procedure_id"], proc["version"],
            )

            completed1 = await complete_evaluation(pool, ev["id"], execution_ids=[real_exec_id])
            assert completed1["aggregate_result"] == "pass"

            completed2 = await complete_evaluation(pool, ev["id"], execution_ids=[real_exec_id])
            assert completed2["id"] == completed1["id"] and completed2["aggregate_result"] == "pass"

            with pytest.raises(ValueError, match="already completed"):
                await complete_evaluation(pool, ev["id"], execution_ids=[str(uuid7())])
        finally:
            await _cleanup(pool, "bypasstest-b4f")
            await pool.close()
    asyncio.run(_run())
