"""
Real, live-database tests for the knowledge/verification/ranking
consolidation pass. Same pattern as every other *_e2e.py in this suite:
requires a real DATABASE_URL (the throwaway container --
backend/TESTING_DB.md -- never production), skips (not fails) without one.

Covers:
  - exact version linkage (§1)
  - parent attribution through a multi-hop improvement chain, and through
    supersession (§2 -- the D19 fix)
  - "Run A after B exists still credits A's version" (§2)
  - benchmark lifecycle used/validated derived from real evaluations (§8)
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.economy import credits as credits_service
from app.economy import submissions as submissions_service
from app.economy.verification import record_usage_event
from app.services.procedures import capture_procedure, supersede_procedure, OUTCOME_WRITER_STAMP
from app.services.product_model import associate_solution, create_problem, request_evaluation
from app.utils.ids import uuid7

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute("DELETE FROM credit_ledger_events WHERE contributor_id LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_usage_events WHERE executed_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedure_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM benchmark_submissions WHERE submitted_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM procedures WHERE created_by LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM problems WHERE title LIKE $1", f"[{prefix}%")


async def _make_goal(pool, prefix: str) -> str:
    problem = await create_problem(pool, title=f"[{prefix}] a goal", description="d", objective="o", constraints=[], status="open", proposer=None, provenance="system_pending_review", metadata={}, visibility="public", scope_type="global", scope_entity_id=None)
    return str(problem["id"])


async def _verified_execution(pool, *, procedure_row_id, procedure_id, version, created_by, outcome="success"):
    plan_id, graph_id = str(uuid7()), str(uuid7())
    await pool.execute(
        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t',$5,$5,$6)",
        plan_id, procedure_id, version, procedure_row_id, "hash", created_by,
    )
    await pool.execute("INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h',$3)", graph_id, plan_id, created_by)
    run_id = str(uuid7())
    await pool.execute(
        "INSERT INTO execution_runs (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, status, final_outcome, created_by) VALUES ($1,$2,$3,$4,$5,'succeeded',$6,$7)",
        run_id, plan_id, graph_id, procedure_id, version, outcome, created_by,
    )
    evidence_id = await pool.fetchval(
        "INSERT INTO evidence (id, evidence_type, target_type, target_id, target_version, direction, strength_score, strength_method, outcome_status, success_criteria, created_by, visibility) "
        "VALUES ($1,'execution_result','procedure',$2,$3,'supports',1.0,'real_execution',$4,'{}'::jsonb,$5,'public') RETURNING id",
        str(uuid7()), procedure_row_id, version, outcome, OUTCOME_WRITER_STAMP,
    )
    return run_id, str(evidence_id)


def test_run_pins_exact_procedure_version_not_latest():
    """§1: a Run/usage event must identify the exact version it executed --
    never inferred as "the current row"."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "lineagetest-1")
            owner = "lineagetest-1-owner"
            v1 = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by=owner, owner_id=owner)
            v2 = await supersede_procedure(pool, prior_row_id=v1["id"], changed_fields={"name": "p v2"}, reason="test")
            assert v2["procedure_id"] == v1["procedure_id"]
            assert v2["version"] == v1["version"] + 1
            assert v2["id"] != v1["id"]

            run_id_v1, evidence_id_v1 = await _verified_execution(pool, procedure_row_id=v1["id"], procedure_id=str(v1["procedure_id"]), version=v1["version"], created_by="lineagetest-1-runner")
            event = await record_usage_event(pool, procedure_row_id=v1["id"], executor_subject="lineagetest-1-runner", execution_run_id=run_id_v1, evidence_id=evidence_id_v1)
            assert event["procedure_row_id"] == v1["id"], "the usage event must pin the EXACT version row that was run, not the now-current v2"
        finally:
            await _cleanup(pool, "lineagetest-1")
            await pool.close()
    asyncio.run(_run())


def test_historical_run_stays_attached_after_later_supersession():
    """§1: historical Runs must remain attached to their historical
    Procedure version even after later improvements exist."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "lineagetest-2")
            owner = "lineagetest-2-owner"
            v1 = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by=owner, owner_id=owner)
            run_id, evidence_id = await _verified_execution(pool, procedure_row_id=v1["id"], procedure_id=str(v1["procedure_id"]), version=v1["version"], created_by="lineagetest-2-runner")
            event = await record_usage_event(pool, procedure_row_id=v1["id"], executor_subject="lineagetest-2-runner", execution_run_id=run_id, evidence_id=evidence_id)

            await supersede_procedure(pool, prior_row_id=v1["id"], changed_fields={"name": "p v2"}, reason="test")

            row = await pool.fetchrow("SELECT * FROM procedure_usage_events WHERE id = $1", event["id"])
            assert str(row["procedure_row_id"]) == v1["id"], "the historical usage event must still point at v1's row after v2 exists"
        finally:
            await _cleanup(pool, "lineagetest-2")
            await pool.close()
    asyncio.run(_run())


def test_parent_attribution_chain_A_B_C():
    """§2: A original, B improves A, C improves B. Running C (verified,
    independent) must credit C's own contributor as primary and B's
    contributor (the immediate parent) at the reduced share -- never A's
    (no multi-hop)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "lineagetest-3")
            goal_id = await _make_goal(pool, "lineagetest-3")
            alice, bob, carol, dave = ("lineagetest-3-alice", "lineagetest-3-bob", "lineagetest-3-carol", "lineagetest-3-dave")

            sub_a = await submissions_service.create_procedure_submission(pool, goal_id=goal_id, submission_type="new", name="A", steps=["s"], rationale="r", actor_subject=alice, provenance="system_pending_review", scope_type="global")
            sub_b = await submissions_service.create_procedure_submission(pool, goal_id=goal_id, submission_type="improvement", name="B", steps=["s", "s2"], rationale="better", parent_procedure_row_id=sub_a["procedure_row_id"], actor_subject=bob, provenance="system_pending_review", scope_type="global")
            sub_c = await submissions_service.create_procedure_submission(pool, goal_id=goal_id, submission_type="improvement", name="C", steps=["s", "s2", "s3"], rationale="even better", parent_procedure_row_id=sub_b["procedure_row_id"], actor_subject=carol, provenance="system_pending_review", scope_type="global")

            for sub in (sub_a, sub_b, sub_c):
                await submissions_service.review_procedure_submission(pool, submission_id=sub["id"], decision="accepted", actor_subject="lineagetest-3-reviewer")

            run_id, evidence_id = await _verified_execution(pool, procedure_row_id=sub_c["procedure_row_id"], procedure_id=str((await pool.fetchrow("SELECT procedure_id FROM procedures WHERE id=$1", sub_c["procedure_row_id"]))["procedure_id"]), version=1, created_by=dave)
            event = await record_usage_event(pool, procedure_row_id=sub_c["procedure_row_id"], executor_subject=dave, execution_run_id=run_id, evidence_id=evidence_id)
            assert event["is_self_use"] is False

            rewards = await credits_service.reward_verified_reuse(pool, usage_event=event)
            recipients = {r["contributor_id"]: r for r in rewards}
            assert carol in recipients, "C's own contributor must be credited"
            assert bob in recipients, "B, the IMMEDIATE parent, must be credited"
            assert alice not in recipients, "A must NOT be credited -- single-hop attribution only, no multi-hop chain"
            assert recipients[bob]["metadata"].get("parent_attribution") is True
        finally:
            await _cleanup(pool, "lineagetest-3")
            await pool.close()
    asyncio.run(_run())


def test_run_A_after_B_exists_still_credits_A():
    """§2: even after B (an improvement of A) exists, running A's OWN
    version must still credit A's contributor -- not B's."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "lineagetest-4")
            goal_id = await _make_goal(pool, "lineagetest-4")
            alice, bob, runner = "lineagetest-4-alice", "lineagetest-4-bob", "lineagetest-4-runner"

            sub_a = await submissions_service.create_procedure_submission(pool, goal_id=goal_id, submission_type="new", name="A", steps=["s"], rationale="r", actor_subject=alice, provenance="system_pending_review", scope_type="global")
            await submissions_service.review_procedure_submission(pool, submission_id=sub_a["id"], decision="accepted", actor_subject="lineagetest-4-reviewer")
            sub_b = await submissions_service.create_procedure_submission(pool, goal_id=goal_id, submission_type="improvement", name="B", steps=["s", "s2"], rationale="better", parent_procedure_row_id=sub_a["procedure_row_id"], actor_subject=bob, provenance="system_pending_review", scope_type="global")
            await submissions_service.review_procedure_submission(pool, submission_id=sub_b["id"], decision="accepted", actor_subject="lineagetest-4-reviewer")

            proc_a = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id=$1", sub_a["procedure_row_id"])
            run_id, evidence_id = await _verified_execution(pool, procedure_row_id=sub_a["procedure_row_id"], procedure_id=str(proc_a["procedure_id"]), version=proc_a["version"], created_by=runner)
            event = await record_usage_event(pool, procedure_row_id=sub_a["procedure_row_id"], executor_subject=runner, execution_run_id=run_id, evidence_id=evidence_id)
            rewards = await credits_service.reward_verified_reuse(pool, usage_event=event)
            recipients = {r["contributor_id"] for r in rewards}
            assert alice in recipients
            assert bob not in recipients, "running A must not accidentally credit B just because B exists as a later improvement"
        finally:
            await _cleanup(pool, "lineagetest-4")
            await pool.close()
    asyncio.run(_run())


def test_benchmark_lifecycle_used_and_validated_derive_from_real_evaluations():
    """§8: 'accepted' does not mean 'proven useful'. used/validated must
    come from real evaluations, not be set by acceptance."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "benchtest-1")
            goal_id = await _make_goal(pool, "benchtest-1")
            submitter = "benchtest-1-submitter"

            sub = await submissions_service.create_benchmark_submission(pool, goal_id=goal_id, name="bench", description="d", success_criteria={"x": 1}, actor_subject=submitter, provenance="system_pending_review", scope_type="global")
            fetched = await submissions_service.get_benchmark_submission(pool, sub["id"])
            assert fetched["lifecycle"]["used"] is False
            assert fetched["lifecycle"]["validated"] is False

            accepted = await submissions_service.review_benchmark_submission(pool, submission_id=sub["id"], decision="accepted", actor_subject="benchtest-1-reviewer")
            still_unused = await submissions_service.get_benchmark_submission(pool, accepted["id"])
            assert still_unused["lifecycle"]["used"] is False, "acceptance alone must not imply usage"

            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by=submitter, owner_id=submitter)
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["id"], proposer=submitter)
            ev_req = await request_evaluation(pool, problem_id=goal_id, benchmark_id=accepted["benchmark_id"], solution_id=sol["id"], procedure_id=proc["id"], procedure_version=1, environment={}, methodology={})

            row = await pool.fetchrow("UPDATE evaluations SET status='completed', aggregate_result='pass', run_count=1, completed_at=now() WHERE id=$1 RETURNING *", ev_req["id"])
            used_only = await submissions_service.get_benchmark_submission(pool, accepted["id"])
            assert used_only["lifecycle"]["used"] is True
            assert used_only["lifecycle"]["validated"] is False, "one pass-only result does not demonstrate the benchmark distinguishes outcomes"

            ev_req2 = await request_evaluation(pool, problem_id=goal_id, benchmark_id=accepted["benchmark_id"], solution_id=sol["id"], procedure_id=proc["id"], procedure_version=1, environment={"x": 1}, methodology={})
            await pool.execute("UPDATE evaluations SET status='completed', aggregate_result='fail', run_count=1, completed_at=now() WHERE id=$1", ev_req2["id"])
            validated = await submissions_service.get_benchmark_submission(pool, accepted["id"])
            assert validated["lifecycle"]["validated"] is True, "a recorded pass AND a recorded fail together demonstrate real discrimination"
        finally:
            await _cleanup(pool, "benchtest-1")
            await pool.close()
    asyncio.run(_run())
