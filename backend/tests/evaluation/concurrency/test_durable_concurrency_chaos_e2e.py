"""
Task spec §22 -- load/chaos for the durable execution layer specifically,
at the same modest scale (tens of concurrent ops) as
test_concurrency_chaos_e2e.py, which this file sits beside rather than
duplicates.

Phase 3 (backend/tests/evaluation/durable/test_gold_durable_e2e.py)
already thoroughly proves concurrent-RESUME refusal
(test_concurrent_resume_is_refused_not_duplicated) and duplicate-resume-
produces-no-duplicate-evidence for ONE run. This file's own question is
different: concurrent DUPLICATE JOB SUBMISSION -- N independent NEW runs
started against the SAME execution plan at once (not N resumes of one
run) -- and whether real mid-node crashes happening DURING that
concurrent load leave any run's persisted state torn (partial
persistence), or leak state across runs that must not share anything.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py
file in this suite.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

from app.db.session import create_pool  # noqa: E402
from app.execution.durable_run import (  # noqa: E402
    WorkerLost,
    execute_run,
    run_status,
    start_run,
)
from app.execution.plan_persistence import persist_compiled_plan  # noqa: E402
from app.execution.plans import compile_plan  # noqa: E402
from app.models.plan import PlanNode  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402

DEPS = {0: [], 1: [0], 2: [1]}
PREFIX = "durable-chaos-e2e"


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.02] * 1024


async def _compiled_plan_chain(pool, tag: str):
    """A real, DB-persisted CompiledPlan (compile_plan + persist_compiled_
    plan, exactly test_gold_durable_e2e.py's full-chain pattern) -- unlike
    tests.test_durable_run_e2e._plan_chain's raw-SQL shortcut, this is
    required here: durable_run._finalize only calls record_plan_execution
    (the thing that writes the `executions` row this test asserts on) when
    execute_run() is given a real `compiled` object, never when compiled
    is None (confirmed by reading _finalize before writing this test)."""
    res = await capture_procedure(
        pool, name=f"{PREFIX}-{tag}", goal="durable chaos probe",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
        provenance="prior_library", scope_type="global", created_by=PREFIX,
        embedding=await _FakeEmbedder().embed_one(tag),
    )
    payload = {
        "id": uuid.UUID(res["id"]), "procedure_id": uuid.UUID(res["procedure_id"]),
        "version": 1, "name": f"{PREFIX}-{tag}", "goal": "durable chaos probe",
        "steps": [{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
    }
    nodes = [
        PlanNode(order=0, goal="a", deps=[]),
        PlanNode(order=1, goal="b", deps=[0]),
        PlanNode(order=2, goal="c", deps=[1]),
    ]
    compiled = compile_plan(
        procedure_id=payload["procedure_id"], procedure_version=1,
        procedure_row_id=payload["id"], procedure_payload=payload,
        task_description=f"{PREFIX}-{tag}", nodes=nodes,
        extractor_version="find_best_way_plan_compiler@1", created_by=PREFIX,
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    return res["procedure_id"], compiled


def test_concurrent_duplicate_job_submission_produces_independent_uncorrupted_runs():
    """N callers independently submit "the same job" (identical plan/graph
    -- e.g. two schedulers both deciding to (re)run the same procedure at
    once, neither aware of the other, NOT a resume of a shared run id).
    execution_runs has no uniqueness constraint on execution_plan_id
    (confirmed by reading db/36_durable_execution_runs.sql before writing
    this test) -- the correct, honest behavior is N fully independent
    execution_runs rows, each with its own execution_run_nodes, no shared
    state, no lock contention that corrupts another run's node counts.
    Roughly half the runs get a real mid-node WorkerLost crash injected
    concurrently with the rest succeeding cleanly, so this also exercises
    partial persistence under real concurrent load: a crashed run's state
    must be exactly as consistent as Phase 3's single-run crash proof,
    even while N-1 other runs are being driven through the same pool at
    the same time."""
    N = 12

    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=4, max_size=15)
        try:
            tag = uuid.uuid4().hex[:8]
            proc_id, compiled = await _compiled_plan_chain(pool, tag)

            run_ids = await asyncio.gather(*[
                start_run(
                    pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
                    procedure_id=str(compiled.plan.procedure.procedure_id), procedure_version=1,
                    node_orders=[0, 1, 2], deps=DEPS, max_attempts=2,
                    created_by=f"{PREFIX}-{i}",
                )
                for i in range(N)
            ])
            assert len(set(run_ids)) == N, "expected N distinct run ids for N independent submissions"

            async def _drive(run_id: str, should_crash: bool):
                crashed_once = {"done": False}

                async def run_node(order: int, attempt: int) -> dict:
                    if should_crash and order == 1 and not crashed_once["done"]:
                        crashed_once["done"] = True
                        raise WorkerLost(f"simulated worker loss on {run_id}")
                    return {"order": order}

                try:
                    return await execute_run(pool, run_id, deps=DEPS, run_node=run_node,
                                              worker_id=f"w-{run_id[:8]}", compiled=compiled)
                except WorkerLost:
                    return {"status": "crashed"}

            results = await asyncio.gather(*[
                _drive(rid, should_crash=(i % 2 == 0)) for i, rid in enumerate(run_ids)
            ], return_exceptions=True)

            errors = [r for r in results if isinstance(r, Exception)]
            assert not errors, f"{len(errors)}/{N} concurrent durable runs raised unexpectedly: {errors[:3]}"

            # Every run's final persisted state is fully self-consistent and
            # uncontaminated by any other concurrently-driven run -- no
            # cross-run bleed of node status/attempt_count, and no run left
            # in an impossible intermediate state by the concurrent load.
            for i, run_id in enumerate(run_ids):
                st = await run_status(pool, run_id)
                by = {n["node_order"]: n for n in st["nodes"]}
                if i % 2 == 0:
                    # Crashed mid-node-1: node 0 succeeded, node 1 is stuck
                    # 'running' (state persisted, not silently lost), node 2
                    # never touched -- exactly Phase 3's single-run crash
                    # shape, now proven to hold under concurrent load too.
                    assert by[0]["status"] == "succeeded"
                    assert by[1]["status"] == "running"
                    assert by[2]["status"] == "pending"
                else:
                    assert by[0]["status"] == by[1]["status"] == by[2]["status"] == "succeeded"

            # No partial persistence: exactly the expected number of
            # immutable `executions` rows exist for this plan -- one per
            # run that actually reached a terminal succeeded node 2, never
            # more (no double-write) and never a row for a run that never
            # got there (no phantom write from the crashed half).
            expected_execution_rows = sum(1 for i in range(N) if i % 2 != 0)
            async with pool.acquire() as c:
                count = await c.fetchval(
                    "SELECT count(*) FROM executions WHERE execution_plan_id=$1", str(compiled.plan.id),
                )
            assert count == expected_execution_rows, (
                f"expected exactly {expected_execution_rows} terminal execution rows "
                f"(one per successfully-completed concurrent run), found {count}"
            )
        finally:
            # No cleanup of procedures/execution_plans/executions -- frozen/
            # append-only, FK-locked together (same established convention
            # as tests/test_durable_run_e2e.py and this suite's own Phase 3
            # durable file); isolation is via the random tag baked into
            # _plan_chain's procedure name only.
            await pool.close()

    asyncio.run(_body())
