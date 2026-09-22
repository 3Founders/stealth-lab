"""
Real, live-database regression tests for the N1 hardening of
request_evaluation() (final-audit non-blocking finding): the caller-supplied
problem_id/benchmark_id/solution_id/procedure_id/procedure_version bundle is
no longer trusted as self-consistent -- solution_id and benchmark_id are
resolved server-side and cross-checked against problem_id, and (for
procedure solutions) the real target procedure+version is resolved from the
solution's own target_id, not from whatever procedure_id/procedure_version
the client separately supplied.

Same pattern as test_bypass_closure_e2e.py: requires a real DATABASE_URL
(the throwaway container -- backend/TESTING_DB.md -- never production),
skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.procedures import capture_procedure
from app.services.product_model import (
    associate_solution, complete_evaluation, create_benchmark, create_problem,
    request_evaluation,
)
from app.utils.ids import uuid7

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, prefix: str) -> None:
    # benchmarks in this file are all created with a fixed, non-prefixed
    # provenance ("system_pending_review") -- clean them up by problem_id
    # (via the prefix-tagged problem title) instead of by provenance.
    await pool.execute("DELETE FROM evaluation_executions WHERE evaluation_id IN (SELECT id FROM evaluations WHERE provenance LIKE $1)", f"{prefix}%")
    await pool.execute("DELETE FROM evaluations WHERE provenance LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM solutions WHERE proposer LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM benchmarks WHERE problem_id IN (SELECT id FROM problems WHERE title LIKE $1)", f"[{prefix}%")
    try:
        await pool.execute("DELETE FROM procedures WHERE created_by LIKE $1", f"{prefix}%")
    except Exception:
        # Band 1.7: `executions` is append-only/frozen by DB trigger -- a
        # procedure a test ran a real execution against can never be
        # deleted again. Expected and harmless in a disposable test DB;
        # leave that row (and its problem, below) orphaned rather than fail
        # cleanup.
        pass
    try:
        await pool.execute("DELETE FROM problems WHERE title LIKE $1", f"[{prefix}%")
    except Exception:
        pass


async def _make_goal(pool, prefix: str, suffix: str = "") -> str:
    problem = await create_problem(
        pool, title=f"[{prefix}] a goal{suffix}", description="d", objective="o", constraints=[],
        status="open", proposer=None, provenance="system_pending_review", metadata={},
        visibility="public", scope_type="global", scope_entity_id=None,
    )
    return str(problem["id"])


async def _real_execution(pool, proc: dict) -> str:
    """Insert a real, minimal execution_plans/task_graphs/executions chain
    so complete_evaluation() has genuine lineage to validate against."""
    plan_id, graph_id = str(uuid7()), str(uuid7())
    await pool.execute(
        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, task_description, procedure_content_hash, content_hash, created_by) VALUES ($1,$2,$3,$4,'t','h','h','x')",
        plan_id, proc["procedure_id"], 1, proc["id"],
    )
    await pool.execute("INSERT INTO task_graphs (id, execution_plan_id, graph_hash, created_by) VALUES ($1,$2,'h','x')", graph_id, plan_id)
    exec_id = str(uuid7())
    await pool.execute(
        "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, procedure_version, outcome, started_at, ended_at) VALUES ($1,$2,$3,$4,$5,'success',now(),now())",
        exec_id, plan_id, graph_id, proc["procedure_id"], 1,
    )
    return exec_id


def test_N1_matching_solution_procedure_version_succeeds():
    """1: a self-consistent bundle is accepted exactly as before."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-1")
            goal_id = await _make_goal(pool, "n1test-1")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-1-owner", owner_id="n1test-1-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-1-owner")

            ev = await request_evaluation(
                pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"],
                procedure_id=str(proc["procedure_id"]), procedure_version=1,
                provenance="n1test-1",
            )
            assert ev["solution_id"] == sol["id"]
            assert str(ev["procedure_id"]) == str(proc["procedure_id"])
            assert ev["procedure_version"] == 1
        finally:
            await _cleanup(pool, "n1test-1")
            await pool.close()
    asyncio.run(_run())


def test_N1_omitted_procedure_fields_are_derived_from_the_solution():
    """Preserve existing valid behavior: callers that name only solution_id
    (no procedure_id/procedure_version) still work -- the fields are now
    correctly derived server-side instead of left NULL."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-1b")
            goal_id = await _make_goal(pool, "n1test-1b")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-1b-owner", owner_id="n1test-1b-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-1b-owner")

            ev = await request_evaluation(
                pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"],
                provenance="n1test-1b",
            )
            assert str(ev["procedure_id"]) == str(proc["procedure_id"])
            assert ev["procedure_version"] == 1
        finally:
            await _cleanup(pool, "n1test-1b")
            await pool.close()
    asyncio.run(_run())


def test_N1_wrong_solution_id_is_rejected():
    """2: a solution_id that does not exist at all is rejected."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-2")
            goal_id = await _make_goal(pool, "n1test-2")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")

            with pytest.raises(ValueError, match="not found"):
                await request_evaluation(
                    pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=str(uuid7()),
                    provenance="n1test-2",
                )
        finally:
            await _cleanup(pool, "n1test-2")
            await pool.close()
    asyncio.run(_run())


def test_N1_wrong_procedure_id_is_rejected():
    """3: solution really targets procedure A, but the client claims
    procedure B -- must be rejected, not silently recorded."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-3")
            goal_id = await _make_goal(pool, "n1test-3")
            proc_a = await capture_procedure(pool, name="a", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-3-owner", owner_id="n1test-3-owner")
            proc_b = await capture_procedure(pool, name="b", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-3-owner", owner_id="n1test-3-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc_a["procedure_id"], status="active", proposer="n1test-3-owner")

            with pytest.raises(ValueError, match="does not match solution"):
                await request_evaluation(
                    pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"],
                    procedure_id=str(proc_b["procedure_id"]), procedure_version=1,
                    provenance="n1test-3",
                )
        finally:
            await _cleanup(pool, "n1test-3")
            await pool.close()
    asyncio.run(_run())


def test_N1_wrong_procedure_version_is_rejected():
    """4: correct procedure_id but a version that does not match the
    solution's actual target row -- must be rejected."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-4")
            goal_id = await _make_goal(pool, "n1test-4")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-4-owner", owner_id="n1test-4-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-4-owner")

            with pytest.raises(ValueError, match="does not match solution"):
                await request_evaluation(
                    pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"],
                    procedure_id=str(proc["procedure_id"]), procedure_version=1 + 1,
                    provenance="n1test-4",
                )
        finally:
            await _cleanup(pool, "n1test-4")
            await pool.close()
    asyncio.run(_run())


def test_N1_wrong_goal_is_rejected():
    """5: the solution belongs to Goal A, but the caller names Goal B as
    problem_id -- must be rejected."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-5")
            goal_a = await _make_goal(pool, "n1test-5", "-a")
            goal_b = await _make_goal(pool, "n1test-5", "-b")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-5-owner", owner_id="n1test-5-owner")
            bench_b = await create_benchmark(pool, problem_id=goal_b, name="b", provenance="system_pending_review")
            sol_a = await associate_solution(pool, problem_id=goal_a, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-5-owner")

            with pytest.raises(ValueError, match="belongs to problem"):
                await request_evaluation(
                    pool, problem_id=goal_b, benchmark_id=bench_b["id"], solution_id=sol_a["id"],
                    procedure_id=str(proc["procedure_id"]), procedure_version=1,
                    provenance="n1test-5",
                )
        finally:
            await _cleanup(pool, "n1test-5")
            await pool.close()
    asyncio.run(_run())


def test_N1_wrong_benchmark_goal_is_rejected():
    """6: the benchmark belongs to Goal B, but the caller pairs it with a
    solution+problem_id from Goal A -- must be rejected."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-6")
            goal_a = await _make_goal(pool, "n1test-6", "-a")
            goal_b = await _make_goal(pool, "n1test-6", "-b")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-6-owner", owner_id="n1test-6-owner")
            bench_b = await create_benchmark(pool, problem_id=goal_b, name="b", provenance="system_pending_review")
            sol_a = await associate_solution(pool, problem_id=goal_a, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-6-owner")

            with pytest.raises(ValueError, match="belongs to problem"):
                await request_evaluation(
                    pool, problem_id=goal_a, benchmark_id=bench_b["id"], solution_id=sol_a["id"],
                    procedure_id=str(proc["procedure_id"]), procedure_version=1,
                    provenance="n1test-6",
                )
        finally:
            await _cleanup(pool, "n1test-6")
            await pool.close()
    asyncio.run(_run())


def test_N1_valid_evaluation_completion_still_works():
    """7: with a self-consistent bundle, the full request -> complete flow
    still produces a correct server-derived 'pass' result, unaffected by
    the new cross-validation."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-7")
            goal_id = await _make_goal(pool, "n1test-7")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-7-owner", owner_id="n1test-7-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-7-owner")
            ev = await request_evaluation(
                pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"],
                procedure_id=str(proc["procedure_id"]), procedure_version=1,
                provenance="n1test-7",
            )
            exec_id = await _real_execution(pool, proc)

            completed = await complete_evaluation(pool, ev["id"], execution_ids=[exec_id])
            assert completed["aggregate_result"] == "pass"
        finally:
            await _cleanup(pool, "n1test-7")
            await pool.close()
    asyncio.run(_run())


def test_N1_idempotent_completion_still_intact():
    """8: retrying complete_evaluation with the same execution set after
    the new request_evaluation checks still returns the existing row
    unchanged, and a different execution set on an already-completed
    evaluation is still refused."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "n1test-8")
            goal_id = await _make_goal(pool, "n1test-8")
            proc = await capture_procedure(pool, name="p", goal="g", steps=["s"], provenance="system_pending_review", scope_type="global", created_by="n1test-8-owner", owner_id="n1test-8-owner")
            bench = await create_benchmark(pool, problem_id=goal_id, name="b", provenance="system_pending_review")
            sol = await associate_solution(pool, problem_id=goal_id, solution_type="procedure", target_id=proc["procedure_id"], status="active", proposer="n1test-8-owner")
            ev = await request_evaluation(
                pool, problem_id=goal_id, benchmark_id=bench["id"], solution_id=sol["id"],
                procedure_id=str(proc["procedure_id"]), procedure_version=1,
                provenance="n1test-8",
            )
            exec_id = await _real_execution(pool, proc)

            completed1 = await complete_evaluation(pool, ev["id"], execution_ids=[exec_id])
            completed2 = await complete_evaluation(pool, ev["id"], execution_ids=[exec_id])
            assert completed2["id"] == completed1["id"] and completed2["aggregate_result"] == "pass"

            with pytest.raises(ValueError, match="already completed"):
                await complete_evaluation(pool, ev["id"], execution_ids=[str(uuid7())])
        finally:
            await _cleanup(pool, "n1test-8")
            await pool.close()
    asyncio.run(_run())
