"""Final-V1 evaluation Bug #7 regression, against real PostgreSQL.

A Solution's completed Evaluation is HISTORICAL evidence. Whether that
Solution is still an eligible *current* leader must be recomputed on read
from the underlying target's validity RIGHT NOW. Before the fix, a Solution
whose backing Procedure had gone stale kept its BEST_VERIFIED band and
stayed in `current_best`.

The staleness itself is forced through the exact production entry point the
existing staleness suite uses -- a real newer claim SUPERSEDES the claim a
precondition of the procedure is tied to, `relate_claims()` fires
`claim_impact.propagate_claim_change()` -> `mark_procedure_stale()`, and the
`procedures.staleness` column really flips. Nothing here calls
`mark_procedure_stale()` directly and nothing writes a leaderboard row by
hand.

Skips (not fails) without a real DATABASE_URL, like every other *_e2e.py.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.services import product_model as pm  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.claims import capture_claim, relate_claims  # noqa: E402
from app.services.procedure_extraction.derive import precondition_with_claim  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from tests.test_product_model_e2e import _FakeEmbedder, _make_procedure, _run_executions  # noqa: E402

_PREFIX = "pm-stale-lb-e2e"


async def _claim_gated_procedure(pool, name: str, tag: str):
    """A real procedure whose precondition is tied to a real claim, so a
    later SUPERSEDES of that claim marks the procedure stale through the
    production path. Returns (procedure_id, procedure_row_id, claim_id)."""
    task_name = f"{_PREFIX}-task-{tag}"
    subject = f"{_PREFIX}:sub:{tag}"
    await pool.execute(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        task_name, f"skill_{task_name}",
    )
    claim_id = await capture_claim(
        pool, statement=f"{_PREFIX} {subject} has_version 2.0",
        task_ids=[f"skill_{task_name}"],
        subject=subject, predicate="has_version", object="2.0",
        embedder=_FakeEmbedder(),
    )
    assert claim_id
    res = await capture_procedure(
        pool, name=name, goal=f"{name} goal", steps=[{"order": 0, "goal": "do it"}],
        preconditions=[precondition_with_claim(subject, "has_version", "2.0", claim_id=claim_id)],
        provenance="prior_library", scope_type="global", created_by="pm_stale_lb_e2e",
        embedding=await _FakeEmbedder().embed_one(name),
    )
    return res["procedure_id"], res["id"], claim_id, subject, task_name


async def _supersede_claim(pool, prior_claim_id: str, subject: str, task_name: str):
    """Real belief change: a newer claim SUPERSEDES the prior one via the
    production `relate_claims` entry point (which calls
    propagate_claim_change internally). Returns the row ids relate_claims
    reports as marked stale."""
    claim_y = await capture_claim(
        pool, statement=f"{_PREFIX} {subject} has_version 3.0",
        task_ids=[f"skill_{task_name}"],
        subject=subject, predicate="has_version", object="3.0",
        embedder=_FakeEmbedder(),
    )
    assert claim_y
    return await relate_claims(
        pool, from_claim_id=claim_y, to_claim_id=prior_claim_id, relation="SUPERSEDES",
    )


async def _staleness(pool, procedure_row_id: str) -> str:
    return await pool.fetchval(
        "SELECT staleness::text FROM procedures WHERE id = $1", procedure_row_id,
    )


@pytest.mark.asyncio
async def test_stale_underlying_procedure_drops_out_of_current_best_and_fresh_one_wins():
    pool = await create_pool(statement_cache_size=0)
    scope = AccessScope.unrestricted()
    tag = uuid.uuid4().hex[:8]
    try:
        problem = await pm.create_problem(
            pool, title=f"[{_PREFIX} {tag}] keep the deploy playbook current",
            objective="deploy succeeds first try", proposer="pm_stale_lb_e2e",
        )
        bench = await pm.create_benchmark(
            pool, problem_id=problem["id"], name="deploy-bench", version=1,
            evaluation_protocol={"verification": "deterministic"},
            environment_specification={"runtime": "linux"},
        )

        pa_id, pa_row, claim_x, subject, task_name = await _claim_gated_procedure(
            pool, f"{_PREFIX}-A-{tag}", tag)
        pb_id, pb_row = await _make_procedure(pool, f"{_PREFIX}-B-{tag}")

        sol_a = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=pa_id, proposer="pm_stale_lb_e2e", status="active")
        sol_b = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=pb_id, proposer="pm_stale_lb_e2e", status="active")

        # A clearly ahead of B, both above the BEST_VERIFIED floor.
        exec_a = await _run_executions(pool, pa_id, pa_row, n=30, successes=30)
        exec_b = await _run_executions(pool, pb_id, pb_row, n=30, successes=28)
        eval_a = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol_a["id"],
            procedure_id=pa_id, procedure_version=1,
            environment={"runtime": "linux"}, methodology={"verification": "deterministic"})
        eval_b = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol_b["id"],
            procedure_id=pb_id, procedure_version=1,
            environment={"runtime": "linux"}, methodology={"verification": "deterministic"})
        done_a = await pm.complete_evaluation(pool, eval_a["id"], execution_ids=exec_a)
        await pm.complete_evaluation(pool, eval_b["id"], execution_ids=exec_b)

        # --- BEFORE: A is the sole current best, both are BEST_VERIFIED. ---
        lb0 = await pm.problem_leaderboard(pool, problem["id"], scope=scope)
        by0 = {e["solution_id"]: e for e in lb0["leaderboard"]}
        assert lb0["current_best"] == [sol_a["id"]], lb0
        assert by0[sol_a["id"]]["state"] == "BEST_VERIFIED"
        assert by0[sol_a["id"]]["eligible"] is True
        assert by0[sol_b["id"]]["state"] == "BEST_VERIFIED"
        assert await _staleness(pool, pa_row) == "fresh"

        # --- MUTATE: newer claim supersedes the one A's precondition needs. ---
        marked = await _supersede_claim(pool, claim_x, subject, task_name)
        assert pa_row in marked, marked
        assert await _staleness(pool, pa_row) == "stale"   # real column, real path
        assert await _staleness(pool, pb_row) == "fresh"   # B untouched

        # --- AFTER: recompute the leaderboard through the real service. ---
        lb1 = await pm.problem_leaderboard(pool, problem["id"], scope=scope)
        by1 = {e["solution_id"]: e for e in lb1["leaderboard"]}

        # A is no longer an eligible current-best winner...
        assert sol_a["id"] not in lb1["current_best"]
        assert by1[sol_a["id"]]["state"] == "STALE"
        assert by1[sol_a["id"]]["eligible"] is False
        assert "stale" in by1[sol_a["id"]]["ineligibility_reason"].lower()
        # ...but it is NOT deleted from the board and keeps its history.
        assert by1[sol_a["id"]]["run_count"] == 30
        assert by1[sol_a["id"]]["verified_successes"] == 30
        assert {"solution_id": sol_a["id"], "reason": by1[sol_a["id"]]["ineligibility_reason"]} \
            in lb1["ineligible_solutions"]

        # the fresh solution B is now the current best, and it did not have
        # to be fabricated -- it was already BEST_VERIFIED.
        assert lb1["current_best"] == [sol_b["id"]], lb1
        assert by1[sol_b["id"]]["state"] == "BEST_VERIFIED"
        # A must not outrank the currently valid B.
        order = [e["solution_id"] for e in lb1["leaderboard"]]
        assert order.index(sol_b["id"]) < order.index(sol_a["id"])
        # conditional leaders must not point at the stale solution either.
        assert sol_a["id"] not in lb1["conditional_leaders"].values()

        # --- HISTORICAL EVALUATION IS PRESERVED, not rewritten. ---
        hist = await pm.get_evaluation(pool, eval_a["id"], scope=scope)
        assert hist is not None
        assert hist["status"] == "completed"                 # not invalidated
        assert hist["procedure_id"] == pa_id                 # lineage still pinned
        assert hist["procedure_version"] == 1
        assert hist["verification_summary"]["verified_successes"] == 30
        all_evals = await pm.list_problem_evaluations(pool, problem["id"], scope=scope)
        assert len(all_evals) == 2, "staleness must not fabricate a new evaluation"
        assert {e["status"] for e in all_evals} == {"completed"}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_stale_only_solution_leaves_no_current_best():
    pool = await create_pool(statement_cache_size=0)
    scope = AccessScope.unrestricted()
    tag = uuid.uuid4().hex[:8]
    try:
        problem = await pm.create_problem(
            pool, title=f"[{_PREFIX} {tag}] single-solution problem",
            objective="x", proposer="pm_stale_lb_e2e")
        bench = await pm.create_benchmark(
            pool, problem_id=problem["id"], name="single-bench", version=1,
            evaluation_protocol={"verification": "deterministic"},
            environment_specification={"runtime": "linux"})
        pa_id, pa_row, claim_x, subject, task_name = await _claim_gated_procedure(
            pool, f"{_PREFIX}-solo-{tag}", tag)
        sol = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=pa_id, proposer="pm_stale_lb_e2e", status="active")
        ex = await _run_executions(pool, pa_id, pa_row, n=30, successes=30)
        ev = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
            procedure_id=pa_id, procedure_version=1,
            environment={"runtime": "linux"}, methodology={"verification": "deterministic"})
        await pm.complete_evaluation(pool, ev["id"], execution_ids=ex)

        assert (await pm.problem_leaderboard(pool, problem["id"], scope=scope))["current_best"] == [sol["id"]]

        marked = await _supersede_claim(pool, claim_x, subject, task_name)
        assert pa_row in marked
        assert await _staleness(pool, pa_row) == "stale"

        lb = await pm.problem_leaderboard(pool, problem["id"], scope=scope)
        assert lb["current_best"] == []          # §38: "no verified solution yet"
        assert lb["current_best_is_tie"] is False
        assert lb["leaderboard"][0]["state"] == "STALE"   # still visible, just not a leader
        assert all(v is None for v in lb["conditional_leaders"].values())
    finally:
        await pool.close()
