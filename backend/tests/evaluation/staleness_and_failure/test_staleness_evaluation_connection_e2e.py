"""
Task spec §14 -- connecting the existing, already-proven claim->staleness->
selection chain (tests/test_staleness_selection_e2e.py) to the
Problem/Benchmark/Solution/Evaluation product-model layer that didn't exist
when that chain was written.

Real finding, verified directly against the code before writing this test
(not a guess): `app/services/product_model.py` has ZERO references to
`staleness` anywhere -- `grep -in stale app/services/product_model.py`
returns nothing, and the only place it touches the `procedures` table is a
target-existence check for Solution association (`_SOLUTION_TABLE`), never
a join on `procedures.staleness`. `problem_leaderboard`/`complete_evaluation`/
`current_best` compute purely from `evaluations`/`evaluation_executions`
rows. This is a real, current product gap, not invented staleness semantics
beyond what the product supports -- the task spec explicitly asks to test
the EXISTING actual policy, and the existing actual policy is "no
connection at all" between claim staleness and Evaluation/leaderboard
status. This test proves that gap concretely rather than asserting it from
static analysis alone.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py file.
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
from tests.test_product_model_e2e import _run_executions  # noqa: E402

PREFIX = "staleness-eval-gap-e2e"


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


def _tag() -> str:
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio
async def test_stale_underlying_procedure_does_not_change_evaluation_or_leaderboard_status():
    # No cleanup of procedures/executions/execution_plans: those tables are
    # frozen/append-only by trigger (migration 23) and FK-locked to each
    # other once a real execution exists, matching
    # tests/test_product_model_e2e.py's own established convention of never
    # deleting them -- isolation here comes entirely from the random `tag`,
    # not teardown.
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    task_name = f"{PREFIX}-task-{tag}"
    subject = f"{PREFIX}:repo:{tag}"
    try:
        # --- Real claim-gated procedure, exactly test_staleness_selection_e2e.py's
        # own setup (claim X backs a precondition on procedure A). ---
        await pool.execute(
            "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
            task_name, f"skill_{task_name}",
        )
        claim_x = await capture_claim(
            pool, statement=f"{PREFIX} {subject} has_version 2.0",
            task_ids=[f"skill_{task_name}"], subject=subject, predicate="has_version",
            object="2.0", embedder=_FakeEmbedder(),
        )
        procedure = await capture_procedure(
            pool, name=f"{PREFIX}-{tag}-proc", goal=f"{PREFIX} goal",
            preconditions=[precondition_with_claim(subject, "has_version", "2.0", claim_id=claim_x)],
            provenance="system_pending_review", scope_type="global",
        )
        proc_row_id = procedure["id"]
        proc_logical_id = procedure["procedure_id"]

        # --- Wire it into a real Problem/Benchmark/Solution/Evaluation. ---
        problem = await pm.create_problem(
            pool, title=f"[{PREFIX} {tag}] staleness vs evaluation gap probe", proposer="userA",
        )
        bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="stale-gap-bench", version=1)
        sol = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_logical_id,
        )

        # _run_executions (tests.test_product_model_e2e) writes a real
        # execution_plans/task_graphs row plus n executions AND the
        # supporting `evidence` rows complete_evaluation's verified-success
        # count actually requires (target_type='procedure', direction=
        # 'supports') -- reusing it rather than hand-rolling raw executions
        # with no evidence, which would silently leave verified_successes=0
        # regardless of staleness and prove nothing.
        n, successes = 10, 10  # 100% verified success, well over MIN_RUNS_FOR_RANKING/BEST_VERIFIED_FLOOR
        exec_ids = await _run_executions(pool, proc_logical_id, proc_row_id, n=n, successes=successes)

        ev = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
        )
        await pm.complete_evaluation(pool, ev["id"], execution_ids=exec_ids)

        # --- Before: the procedure is fresh, and the leaderboard reports the
        # solution as BEST_VERIFIED / current_best -- the premise this gap
        # will contradict. ---
        before_staleness = await pool.fetchval(
            "SELECT staleness::text FROM procedures WHERE id = $1", proc_row_id,
        )
        assert before_staleness == "fresh"

        board_before = await pm.problem_leaderboard(pool, problem["id"], scope=AccessScope.unrestricted())
        assert sol["id"] in board_before["current_best"]
        state_before = next(r["state"] for r in board_before["leaderboard"] if r["solution_id"] == sol["id"])
        assert state_before == "BEST_VERIFIED"

        # --- The real change: a fresher claim supersedes claim_x via the
        # actual production entry point, exactly as
        # test_staleness_selection_e2e.py proves -- this genuinely marks the
        # procedure stale and would exclude it from
        # find_applicable_procedures(). ---
        claim_y = await capture_claim(
            pool, statement=f"{PREFIX} {subject} has_version 3.0",
            task_ids=[f"skill_{task_name}"], subject=subject, predicate="has_version",
            object="3.0", embedder=_FakeEmbedder(),
        )
        stale_marked = await relate_claims(
            pool, from_claim_id=claim_y, to_claim_id=claim_x, relation="SUPERSEDES",
        )
        assert proc_row_id in stale_marked

        after_staleness = await pool.fetchval(
            "SELECT staleness::text FROM procedures WHERE id = $1", proc_row_id,
        )
        assert after_staleness == "stale", "the underlying procedure really did go stale"

        # --- THE GAP: the product-model layer never looks at staleness at
        # all. The Evaluation and leaderboard report EXACTLY the same
        # BEST_VERIFIED / current_best status as before, even though the
        # Solution's only underlying procedure is now stale. ---
        ev_reloaded = await pm.get_evaluation(pool, ev["id"])
        assert ev_reloaded["status"] == "completed"
        assert ev_reloaded["run_count"] == n
        assert set(ev_reloaded["executions"]) == set(exec_ids), (
            "get_evaluation does not re-derive anything from procedure staleness -- "
            "it reports exactly the same executions/run_count as before the claim change"
        )

        board_after = await pm.problem_leaderboard(pool, problem["id"], scope=AccessScope.unrestricted())
        assert sol["id"] in board_after["current_best"], (
            "CONFIRMED GAP: the solution is still current_best even though its only "
            "underlying procedure is now stale -- problem_leaderboard has no staleness input"
        )
        state_after = next(r["state"] for r in board_after["leaderboard"] if r["solution_id"] == sol["id"])
        assert state_after == "BEST_VERIFIED", (
            "CONFIRMED GAP: still BEST_VERIFIED -- an Evaluation based on a since-invalidated "
            "Solution is not silently demoted or flagged by anything in product_model.py. "
            "Not fixed here (house rules against touching product code this pass) -- this is "
            "the honest current policy, not a bug this test papers over."
        )
    finally:
        await pool.close()
