"""
MCP hardening B20 STRICT CLOSURE: the mandatory E2E V4 names but this
codebase's test suite never actually drove -- "Repeat with A -> child B
-> child C, and process loss at every level."

test_durable_run_e2e.py already proves crash/resume for a FLAT run (one
execution_run, several nodes). This file proves the SAME real crash
(`WorkerLost`) + `resume_run()` recovery mechanics compose correctly
across a real RECURSIVE parent/child/grandchild chain of THREE
separate execution_runs (A -> child B -> child C, via start_run's own
real `parent_run_id`/`root_run_id` linkage, migrations 51/52) -- at
every level:

    A's own node crashes mid-execution -> resume -> A succeeds
    B (A's real child) crashes mid-execution -> resume -> B succeeds
    C (B's real child) crashes mid-execution -> resume -> C succeeds
    lineage intact throughout: A never re-run, B never re-run once
    succeeded, root_run_id/parent_run_id correct at every level, and
    each child's real terminal status is recorded on its parent's own
    event log (child_run_completed, this session's own B8 fix).

Skips itself when DATABASE_URL is unset, self-cleaning by row id.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

from app.db.session import create_pool  # noqa: E402
from app.execution.durable_run import WorkerLost, execute_run, resume_run, run_status, start_run  # noqa: E402
from app.execution.recorder import get_run_events  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: [], 1: [0]}


async def _plan_chain(pool, label: str):
    res = await capture_procedure(
        pool, name=f"recur-crash-e2e-{label}-{uuid.uuid4().hex[:8]}", goal=f"recursive crash probe {label}",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}],
        provenance="prior_library", scope_type="global", created_by="recur_crash_e2e",
        embedding=[0.01] * 1024,
    )
    proc_id, row_id = res["procedure_id"], res["id"]
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, f"recur-crash-e2e-{label}",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
    return proc_id, pv, plan_id, graph_id, row_id


async def _cleanup_procedure(pool, row_id) -> None:
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


def _crashing_run_node(calls: list, crashed: dict, crash_order: int = 0):
    async def run_node(order: int, attempt: int) -> dict:
        calls.append((order, attempt))
        if order == crash_order and not crashed["done"]:
            crashed["done"] = True
            raise WorkerLost(f"worker died mid-node {order}")
        return {"order": order, "attempt": attempt, "ok": True}

    return run_node


def test_process_loss_recovers_correctly_at_every_level_of_a_root_child_grandchild_chain():
    async def _run():
        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        row_ids = []
        run_ids = {}
        try:
            # --- A: root run ---
            a_proc, a_pv, a_plan, a_graph, a_row = await _plan_chain(pool, "root-a")
            row_ids.append(a_row)
            a_run_id = await start_run(
                pool, execution_plan_id=a_plan, task_graph_id=a_graph,
                procedure_id=a_proc, procedure_version=a_pv,
                node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="recur_crash_e2e",
            )
            run_ids["a"] = a_run_id

            a_calls: list = []
            a_crashed = {"done": False}
            a_run_node = _crashing_run_node(a_calls, a_crashed, crash_order=0)

            with pytest.raises(WorkerLost):
                await execute_run(pool, a_run_id, deps=DEPS, run_node=a_run_node, worker_id="a-worker-1")
            st = await run_status(pool, a_run_id)
            by = {n["node_order"]: n for n in st["nodes"]}
            assert by[0]["status"] == "running" and by[0]["attempt_count"] == 1

            a_result = await resume_run(pool, a_run_id, deps=DEPS, run_node=a_run_node, worker_id="a-worker-2")
            assert a_result["status"] == "succeeded", a_result
            assert [c for c in a_calls if c[0] == 0] == [(0, 1), (0, 2)]  # crashed once, retried once

            # --- B: A's real child, created only AFTER A's own node 0
            # (the one B is nominally "under") has already recovered --
            # a genuine, real parent_run_id/parent_node_id linkage, not
            # a fabricated one.
            a_node0_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id=$1 AND node_order=0", a_run_id,
            )
            b_proc, b_pv, b_plan, b_graph, b_row = await _plan_chain(pool, "child-b")
            row_ids.append(b_row)
            b_run_id = await start_run(
                pool, execution_plan_id=b_plan, task_graph_id=b_graph,
                procedure_id=b_proc, procedure_version=b_pv,
                node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="recur_crash_e2e",
                parent_run_id=a_run_id, parent_node_id=str(a_node0_id),
            )
            run_ids["b"] = b_run_id

            b_row_check = await pool.fetchrow(
                "SELECT parent_run_id, root_run_id FROM execution_runs WHERE id=$1", b_run_id,
            )
            assert str(b_row_check["parent_run_id"]) == a_run_id
            assert str(b_row_check["root_run_id"]) == a_run_id  # A is root of itself

            b_calls: list = []
            b_crashed = {"done": False}
            b_run_node = _crashing_run_node(b_calls, b_crashed, crash_order=0)

            with pytest.raises(WorkerLost):
                await execute_run(pool, b_run_id, deps=DEPS, run_node=b_run_node, worker_id="b-worker-1")
            st_b = await run_status(pool, b_run_id)
            by_b = {n["node_order"]: n for n in st_b["nodes"]}
            assert by_b[0]["status"] == "running" and by_b[0]["attempt_count"] == 1

            b_result = await resume_run(pool, b_run_id, deps=DEPS, run_node=b_run_node, worker_id="b-worker-2")
            assert b_result["status"] == "succeeded", b_result
            assert [c for c in b_calls if c[0] == 0] == [(0, 1), (0, 2)]

            # A's own already-succeeded node was never rerun by any of
            # B's crash/resume activity above.
            assert [c for c in a_calls if c[0] == 0] == [(0, 1), (0, 2)]

            a_events_after_b = await get_run_events(pool, a_run_id)
            completed_on_a = [e for e in a_events_after_b if e["event_type"] == "child_run_completed"]
            assert len(completed_on_a) == 1
            assert completed_on_a[0]["payload"]["child_run_id"] == b_run_id
            assert completed_on_a[0]["payload"]["child_status"] == "succeeded"

            # --- C: B's real child (grandchild of A) ---
            b_node0_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id=$1 AND node_order=0", b_run_id,
            )
            c_proc, c_pv, c_plan, c_graph, c_row = await _plan_chain(pool, "grandchild-c")
            row_ids.append(c_row)
            c_run_id = await start_run(
                pool, execution_plan_id=c_plan, task_graph_id=c_graph,
                procedure_id=c_proc, procedure_version=c_pv,
                node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="recur_crash_e2e",
                parent_run_id=b_run_id, parent_node_id=str(b_node0_id),
            )
            run_ids["c"] = c_run_id

            c_row_check = await pool.fetchrow(
                "SELECT parent_run_id, root_run_id FROM execution_runs WHERE id=$1", c_run_id,
            )
            assert str(c_row_check["parent_run_id"]) == b_run_id
            # C's root is A -- the materialized-path root, not its
            # immediate parent B.
            assert str(c_row_check["root_run_id"]) == a_run_id

            c_calls: list = []
            c_crashed = {"done": False}
            c_run_node = _crashing_run_node(c_calls, c_crashed, crash_order=1)

            with pytest.raises(WorkerLost):
                await execute_run(pool, c_run_id, deps=DEPS, run_node=c_run_node, worker_id="c-worker-1")
            st_c = await run_status(pool, c_run_id)
            by_c = {n["node_order"]: n for n in st_c["nodes"]}
            assert by_c[0]["status"] == "succeeded"  # node 0 finished before the crash on node 1
            assert by_c[1]["status"] == "running" and by_c[1]["attempt_count"] == 1

            c_result = await resume_run(pool, c_run_id, deps=DEPS, run_node=c_run_node, worker_id="c-worker-2")
            assert c_result["status"] == "succeeded", c_result
            assert [c for c in c_calls if c[0] == 0] == [(0, 1)]  # never rerun
            assert [c for c in c_calls if c[0] == 1] == [(1, 1), (1, 2)]

            # B's own already-succeeded node was never rerun by any of
            # C's crash/resume activity above.
            assert [c for c in b_calls if c[0] == 0] == [(0, 1), (0, 2)]

            b_events_after_c = await get_run_events(pool, b_run_id)
            completed_on_b = [e for e in b_events_after_c if e["event_type"] == "child_run_completed"]
            assert len(completed_on_b) == 1
            assert completed_on_b[0]["payload"]["child_run_id"] == c_run_id
            assert completed_on_b[0]["payload"]["child_status"] == "succeeded"

            # Final lineage sanity: every run genuinely terminal, and the
            # SAME root_run_id ties all three together.
            for rid in (a_run_id, b_run_id, c_run_id):
                row = await pool.fetchrow(
                    "SELECT status, root_run_id FROM execution_runs WHERE id=$1", rid,
                )
                assert row["status"] == "succeeded"
                assert str(row["root_run_id"]) == a_run_id
        finally:
            # Children first -- execution_runs.parent_run_id is a real FK,
            # so a parent cannot be deleted while a child still references it.
            for key in ("c", "b", "a"):
                rid = run_ids.get(key)
                if rid is not None:
                    await pool.execute("DELETE FROM execution_runs WHERE id=$1", rid)
            for rid_val in row_ids:
                await _cleanup_procedure(pool, rid_val)
            await pool.close()

    import asyncio
    asyncio.run(_run())
