"""
§58 durable retry / resume E2E, against real PostgreSQL.

    node A succeeds
    node B fails MID-NODE (worker process dies)  -> state persisted
    simulate worker loss                          -> resume_run() with a new worker
    A is NOT re-run
    B retries and succeeds
    C runs
    final run state is 'succeeded', lineage intact
    a SECOND resume is an idempotent no-op (no duplicate side effects)
    a stale worker cannot mutate the terminal node (DB fence)

Skips itself when DATABASE_URL is unset.
"""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

from app.db.session import create_pool  # noqa: E402
from app.execution.durable_run import (  # noqa: E402
    ResumeInProgress,
    WorkerLost,
    execute_run,
    resume_run,
    retry_node,
    run_status,
    start_run,
)
from app.execution.plan_persistence import _GRAPH_COLUMNS  # noqa: E402,F401  (presence check)
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: [], 1: [0], 2: [1]}


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.01] * 1024


async def _plan_chain(pool):
    res = await capture_procedure(
        pool, name=f"dr-e2e-{uuid.uuid4().hex[:8]}", goal="durable run probe",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
        provenance="prior_library", scope_type="global", created_by="dr_e2e",
        embedding=await _FakeEmbedder().embed_one("x"),
    )
    proc_id, row_id = res["procedure_id"], res["id"]
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "dr-e2e",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
    return proc_id, pv, plan_id, graph_id, row_id


async def _cleanup_procedure(pool, row_id) -> None:
    """B38 sweep finding (this session): this file never cleaned up the
    procedures `_plan_chain` creates -- every run of this test file
    permanently left `is_engineering_fixture=false` corpus rows behind
    (each one pinned by a frozen `execution_plans` row the moment a run
    is started against it, so DELETE alone can never remove them). Same
    hygiene bug already found and fixed in
    test_find_best_way_plan_only_e2e.py earlier this session -- fixed
    here too now that the sweep surfaced it in this file as well."""
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


@pytest.mark.asyncio
async def test_durable_retry_resume_crash_and_continue():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=3, created_by="dr_e2e",
        )

        calls: list[tuple[int, int]] = []
        crashed_once = {"done": False}

        async def run_node(order: int, attempt: int) -> dict:
            calls.append((order, attempt))
            if order == 1 and not crashed_once["done"]:
                crashed_once["done"] = True
                raise WorkerLost("worker-1 died mid-node B")
            return {"order": order, "attempt": attempt, "ok": True}

        # --- call 1: worker-1 crashes inside node B ---
        with pytest.raises(WorkerLost):
            await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="worker-1")

        st = await run_status(pool, run_id)
        by = {n["node_order"]: n for n in st["nodes"]}
        assert by[0]["status"] == "succeeded"
        assert by[1]["status"] == "running" and by[1]["attempt_count"] == 1  # crashed mid-node
        assert by[2]["status"] == "pending"
        assert st["status"] in ("running",)

        # --- simulate worker loss: a fresh worker resumes ---
        res = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="worker-2")
        assert res["status"] == "succeeded", res
        assert res["resume_count"] == 1

        # A was NOT re-run; B retried once (attempt 2); C ran once.
        assert [c for c in calls if c[0] == 0] == [(0, 1)]
        assert (1, 2) in calls and (1, 3) not in calls
        assert [c for c in calls if c[0] == 2] == [(2, 2)] or [c for c in calls if c[0] == 2] == [(2, 1)]
        st2 = await run_status(pool, run_id)
        by2 = {n["node_order"]: n for n in st2["nodes"]}
        assert by2[0]["status"] == by2[1]["status"] == by2[2]["status"] == "succeeded"
        assert by2[1]["attempt_count"] == 2
        assert by2[0]["first_pass_success"] is True and by2[1]["first_pass_success"] is False

        # --- second resume is an idempotent no-op ---
        calls_before = len(calls)
        res3 = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="worker-3")
        assert res3["status"] == "succeeded" and "no-op" in res3["note"]
        assert len(calls) == calls_before  # no node re-run

        # --- stale worker cannot mutate the terminal (succeeded) node ---
        async with pool.acquire() as c:
            with pytest.raises(asyncpg.exceptions.RaiseError):
                await c.execute(
                    "UPDATE execution_run_nodes SET status='pending', attempt_count=0 "
                    "WHERE execution_run_id=$1 AND node_order=0", run_id,
                )
    finally:
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_non_retryable_failure_stays_visible_until_explicit_retry_node():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=2, created_by="dr_e2e",
        )
        fail_b = {"on": True}

        async def run_node(order: int, attempt: int) -> dict:
            if order == 1 and fail_b["on"]:
                raise ValueError("schema validation failed")  # -> 'validation', non-retryable
            return {"order": order}

        res = await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w1")
        assert res["status"] == "failed"
        by = {n["node_order"]: n for n in res["nodes"]}
        assert by[0]["status"] == "succeeded"
        assert by[1]["status"] == "failed" and by[1]["error_class"] == "validation"
        assert by[2]["status"] == "blocked"          # dependent blocked, not silently skipped

        # a plain resume does NOT re-run a non-retryable failure
        r2 = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w2")
        assert r2["status"] == "failed"
        assert {n["node_order"]: n["status"] for n in r2["nodes"]}[1] == "failed"

        # explicit operator retry, with the condition now fixed
        fail_b["on"] = False
        r3 = await retry_node(pool, run_id, 1, deps=DEPS, run_node=run_node, worker_id="w3", force=True)
        assert r3["status"] == "succeeded", r3
        assert {n["node_order"]: n["status"] for n in r3["nodes"]} == {0: "succeeded", 1: "succeeded", 2: "succeeded"}
    finally:
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_concurrent_resume_is_refused_not_duplicated():
    """A second worker cannot drive a run another worker holds under a
    live lease -- it gets ResumeInProgress, not a duplicate execution."""
    from app.execution.durable_run import _claim_run, _release_run

    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, created_by="dr_e2e",
        )
        # worker A takes the run's driver lease and does not release it
        held = await _claim_run(pool, run_id, "workerA")
        assert not held.get("_terminal")
        try:
            async def run_node(order, attempt):
                return {}
            with pytest.raises(ResumeInProgress):
                await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="workerB")
            with pytest.raises(ResumeInProgress):
                await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="workerB")
        finally:
            await _release_run(pool, run_id, "workerA")
        # once released, a worker can drive it to completion
        async def ok_node(order, attempt):
            return {"order": order}
        done = await execute_run(pool, run_id, deps=DEPS, run_node=ok_node, worker_id="workerC")
        assert done["status"] == "succeeded"
    finally:
        await _cleanup_procedure(pool, row_id)
        await pool.close()
