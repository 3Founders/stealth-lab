"""
MCP hardening B7/B8: `app/execution/recorder.py` (ExecutionRecorder) is a
thin facade over the durable, append-only event log added by migration
61 (`execution_run_events`). This does not test a second scheduler --
it tests that the REAL transition points already in durable_run.py
(start_run, _claim_run, _node_claim/_node_finish, the pause branch in
_drive, _finalize) each leave exactly the event trail they claim to,
using the actual public API (start_run/execute_run/retry_node), never
poking the recorder directly except for the append-only fence check.

Skips itself when DATABASE_URL is unset, self-cleaning by row id.
"""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

from app.db.session import create_pool  # noqa: E402
from app.execution.durable_run import WorkerLost, execute_run, resume_run, start_run  # noqa: E402
from app.execution.recorder import get_run_events  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: [], 1: [0]}


async def _cleanup_procedure(pool, row_id) -> None:
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


async def _plan_chain(pool):
    res = await capture_procedure(
        pool, name=f"exrec-e2e-{uuid.uuid4().hex[:8]}", goal="execution recorder probe",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}],
        provenance="prior_library", scope_type="global", created_by="exrec_e2e",
        embedding=[0.01] * 1024,
    )
    proc_id, row_id = res["procedure_id"], res["id"]
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "exrec-e2e",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
    return proc_id, pv, plan_id, graph_id, row_id


@pytest.mark.asyncio
async def test_full_successful_run_leaves_the_expected_event_trail():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="exrec_e2e",
        )

        async def run_node(order: int, attempt: int) -> dict:
            return {"order": order, "attempt": attempt, "ok": True}

        result = await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="exrec-worker-1")
        assert result["status"] == "succeeded"

        events = await get_run_events(pool, run_id)
        types_in_order = [e["event_type"] for e in events]
        # run_created must be first, run_finalized must be last, and every
        # node must have been claimed and succeeded exactly once.
        assert types_in_order[0] == "run_created"
        assert types_in_order[-1] == "run_finalized"
        assert types_in_order.count("run_claimed") == 1
        assert types_in_order.count("node_claimed") == 2
        assert types_in_order.count("node_succeeded") == 2
        assert "node_failed" not in types_in_order

        finalized = [e for e in events if e["event_type"] == "run_finalized"][0]
        assert finalized["payload"] == {"status": "succeeded", "outcome": "success"}

        node_claims = [e for e in events if e["event_type"] == "node_claimed"]
        assert sorted(e["node_order"] for e in node_claims) == [0, 1]
        for e in node_claims:
            assert e["payload"]["worker_id"] == "exrec-worker-1"
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_full_successful_run_emits_the_b8_named_types_previously_declared_but_never_fired():
    """MCP hardening B8 STRICT CLOSURE: 10 of the 18 "at minimum" named
    event types were declared in EVENT_TYPES but had zero real emission
    call sites anywhere in production -- dead vocabulary. Proves 6 of
    them (run_started, procedure_retrieved, applicability_checked,
    plan_created, node_started, knowledge_requested) fire on the same
    real run/continue_run path this file's other tests already use for
    the pre-existing types. The remaining 4 (implementation_bound,
    node_waiting, child_run_completed, node_resumed) need real
    implementation bindings / a real child run / an explicit retry --
    covered by dedicated, more targeted tests elsewhere."""
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        from app.services.route_decision import decide_route, persist_route_decision

        route_decision = await decide_route(
            pool, task_description="execution recorder probe", mode="full_run",
            repo_path=None, authorized=True, authorization_detail={}, goal_embedding=None,
        )
        route_decision_id = await persist_route_decision(pool, route_decision)

        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="exrec_e2e",
            route_decision_id=route_decision_id,
        )

        async def run_node(order: int, attempt: int) -> dict:
            return {"order": order, "attempt": attempt, "ok": True}

        result = await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="exrec-worker-b8")
        assert result["status"] == "succeeded"

        from app.execution.durable_resume import get_run_context
        await get_run_context(pool, run_id)

        events = await get_run_events(pool, run_id)
        types = {e["event_type"] for e in events}
        for expected in (
            "run_started", "procedure_retrieved", "applicability_checked",
            "plan_created", "node_started", "knowledge_requested",
        ):
            assert expected in types, f"{expected} never fired: {sorted(types)}"

        assert len([e for e in events if e["event_type"] == "run_started"]) == 1
        assert len([e for e in events if e["event_type"] == "node_started"]) == 2
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_retry_node_emits_node_resumed():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps=DEPS, max_attempts=1, created_by="exrec_e2e",
        )

        async def failing_run_node(order: int, attempt: int) -> dict:
            if order == 0:
                raise ValueError("deliberately not retryable")
            return {"order": order}

        result = await execute_run(pool, run_id, deps=DEPS, run_node=failing_run_node, worker_id="exrec-retry-1")
        assert result["status"] == "failed"

        async def succeeding_run_node(order: int, attempt: int) -> dict:
            return {"order": order, "attempt": attempt}

        from app.execution.durable_run import retry_node

        retried = await retry_node(
            pool, run_id, 0, deps=DEPS, run_node=succeeding_run_node,
            worker_id="exrec-retry-2", force=True,
        )
        assert retried["status"] == "succeeded"

        events = await get_run_events(pool, run_id)
        resumed = [e for e in events if e["event_type"] == "node_resumed"]
        assert len(resumed) == 1
        assert resumed[0]["node_order"] == 0
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_child_run_creation_and_completion_emit_node_waiting_and_child_run_completed_on_the_parent():
    pool = await create_pool(statement_cache_size=0)
    parent_row_id = None
    child_row_id = None
    parent_run_id = None
    child_run_id = None
    try:
        parent_proc_id, parent_pv, parent_plan_id, parent_graph_id, parent_row_id = await _plan_chain(pool)
        parent_run_id = await start_run(
            pool, execution_plan_id=parent_plan_id, task_graph_id=parent_graph_id,
            procedure_id=parent_proc_id, procedure_version=parent_pv,
            node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="exrec_e2e",
        )

        child_proc_id, child_pv, child_plan_id, child_graph_id, child_row_id = await _plan_chain(pool)
        parent_node_id = await pool.fetchval(
            "SELECT id FROM execution_run_nodes WHERE execution_run_id=$1 AND node_order=0", parent_run_id,
        )
        child_run_id = await start_run(
            pool, execution_plan_id=child_plan_id, task_graph_id=child_graph_id,
            procedure_id=child_proc_id, procedure_version=child_pv,
            node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="exrec_e2e",
            parent_run_id=parent_run_id, parent_node_id=str(parent_node_id),
        )

        parent_events = await get_run_events(pool, parent_run_id)
        waiting = [e for e in parent_events if e["event_type"] == "node_waiting"]
        assert len(waiting) == 1
        assert waiting[0]["node_order"] == 0
        assert waiting[0]["payload"]["child_run_id"] == child_run_id

        async def run_node(order: int, attempt: int) -> dict:
            return {"order": order}

        result = await execute_run(pool, child_run_id, deps=DEPS, run_node=run_node, worker_id="exrec-child-1")
        assert result["status"] == "succeeded"

        parent_events_after = await get_run_events(pool, parent_run_id)
        completed = [e for e in parent_events_after if e["event_type"] == "child_run_completed"]
        assert len(completed) == 1
        assert completed[0]["node_order"] == 0
        assert completed[0]["payload"]["child_run_id"] == child_run_id
        assert completed[0]["payload"]["child_status"] == "succeeded"
    finally:
        if child_run_id is not None:
            await pool.execute("DELETE FROM execution_runs WHERE id=$1", child_run_id)
        if parent_run_id is not None:
            await pool.execute("DELETE FROM execution_runs WHERE id=$1", parent_run_id)
        if child_row_id is not None:
            await _cleanup_procedure(pool, child_row_id)
        if parent_row_id is not None:
            await _cleanup_procedure(pool, parent_row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_crashed_node_records_a_pause_event():
    """WorkerLost only marks the node's lease already-expired and
    re-raises (durable_run.py's own docstring: it propagates rather than
    being recorded as a node failure) -- the actual park-and-pause branch
    in `_drive` only fires on the NEXT drive call that observes the
    stale 'running' lease, i.e. a resume_run(), same as
    test_durable_run_e2e.py's own crash-then-resume pattern."""
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps=DEPS, side_effecting={0}, max_attempts=3, created_by="exrec_e2e",
        )

        async def run_node(order: int, attempt: int) -> dict:
            if order == 0:
                raise WorkerLost("worker died")
            return {"order": order, "attempt": attempt}

        with pytest.raises(WorkerLost):
            await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="exrec-worker-2")

        result = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="exrec-worker-2b")
        assert result["status"] == "paused"

        events = await get_run_events(pool, run_id)
        types = [e["event_type"] for e in events]
        assert "run_paused" in types
        paused = [e for e in events if e["event_type"] == "run_paused"][0]
        assert paused["node_order"] == 0
        assert "run_finalized" not in types  # never terminal after a pause
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_failed_node_records_a_node_failed_event_with_error_class():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps=DEPS, max_attempts=1, created_by="exrec_e2e",
        )

        async def run_node(order: int, attempt: int) -> dict:
            if order == 0:
                raise ValueError("deliberately not retryable")
            return {"order": order}

        result = await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="exrec-worker-3")
        assert result["status"] == "failed"

        events = await get_run_events(pool, run_id)
        failed = [e for e in events if e["event_type"] == "node_failed"]
        assert len(failed) == 1
        assert failed[0]["node_order"] == 0
        assert failed[0]["payload"]["error_class"] == "validation"

        # B8's own vocabulary distinguishes run_failed from run_finalized
        # as two separate event types (not one type with a status field)
        # -- a failed run emits run_failed, never run_finalized.
        assert "run_finalized" not in [e["event_type"] for e in events]
        failed_run_events = [e for e in events if e["event_type"] == "run_failed"]
        assert len(failed_run_events) == 1
        assert failed_run_events[0]["payload"]["status"] == "failed"
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_event_log_forbids_update_but_allows_delete_via_run_cleanup():
    """UPDATE is forbidden (an event can never be rewritten in place
    while its run exists). DELETE is deliberately NOT forbidden --
    execution_run_events is scoped to its execution_runs parent's own
    lifecycle (like execution_run_nodes, ON DELETE CASCADE), not
    permanent testimony (like execution_plans/executions) -- so deleting
    the run (this file's own cleanup, and any real run cleanup) must
    cascade-delete its events rather than fail (migration 62)."""
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, pv, plan_id, graph_id, row_id = await _plan_chain(pool)
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps=DEPS, created_by="exrec_e2e",
        )
        events = await get_run_events(pool, run_id)
        event_id = events[0]["id"]
        with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
            await pool.execute("UPDATE execution_run_events SET event_type='run_paused' WHERE id=$1", event_id)

        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        remaining = await pool.fetch("SELECT id FROM execution_run_events WHERE execution_run_id=$1", run_id)
        assert remaining == []
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_record_event_rejects_an_unknown_event_type():
    from app.execution.recorder import record_event
    pool = await create_pool(statement_cache_size=0)
    try:
        with pytest.raises(ValueError, match="unknown execution_run_events.event_type"):
            await record_event(pool, execution_run_id=str(uuid.uuid4()), event_type="not_a_real_event")
    finally:
        await pool.close()
