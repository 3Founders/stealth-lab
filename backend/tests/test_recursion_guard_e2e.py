"""
MCP hardening B9-B13: live-DB half of the recursion guard --
root_run_id propagation (migration 52), the ancestor-chain-derived
depth/budget/wall-clock checks, and `describe_child_status`'s
derived "waiting on a live child" read.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.config import settings
from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.recursion_guard import (
    ChildExecutionBudgetExceeded,
    CostBudgetExceeded,
    RecursionCycleDetected,
    RecursionDepthExceeded,
    ToolCallBudgetExceeded,
    TokenBudgetExceeded,
    WallClockBudgetExceeded,
    check_recursion_limits,
    describe_child_status,
)
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _compiled_plan(pool, procedure: dict, task_description: str):
    steps = procedure.get("steps") or [{"order": 0, "goal": task_description}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=task_description, nodes=nodes,
        extractor_version="test_recursion_guard_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    return compiled


async def _start_run(pool, procedure: dict, compiled, **kwargs) -> str:
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
        **kwargs,
    )


def test_root_run_points_at_itself_and_child_inherits_root():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recroot-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            root_row = await pool.fetchrow("SELECT root_run_id, parent_run_id FROM execution_runs WHERE id = $1", root_id)
            assert str(root_row["root_run_id"]) == root_id
            assert root_row["parent_run_id"] is None

            child_id = await _start_run(pool, procedure, compiled, parent_run_id=root_id)
            child_row = await pool.fetchrow("SELECT root_run_id, parent_run_id FROM execution_runs WHERE id = $1", child_id)
            assert str(child_row["root_run_id"]) == root_id
            assert str(child_row["parent_run_id"]) == root_id

            grandchild_id = await _start_run(pool, procedure, compiled, parent_run_id=child_id)
            grandchild_row = await pool.fetchrow("SELECT root_run_id FROM execution_runs WHERE id = $1", grandchild_id)
            assert str(grandchild_row["root_run_id"]) == root_id, "root_run_id must propagate transitively, not just one level"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_detects_cycle_via_procedure_id():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reccycle-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)

            with pytest.raises(RecursionCycleDetected):
                await check_recursion_limits(
                    pool, parent_run_id=root_id, candidate_procedure_id=procedure["procedure_id"],
                )

            # A genuinely different candidate must pass.
            chain = await check_recursion_limits(
                pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()),
            )
            assert chain.root_run_id == root_id
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_depth(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recdepth-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_recursion_depth", 2)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)  # depth 1
            # A child OF the root would be depth 2 -- exactly at the limit, allowed.
            await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
            child_id = await _start_run(pool, procedure, compiled, parent_run_id=root_id)  # depth 2

            # A child of THAT child would be depth 3 -- exceeds max_recursion_depth=2.
            with pytest.raises(RecursionDepthExceeded):
                await check_recursion_limits(pool, parent_run_id=child_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_child_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recbudget-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_child_executions", 2)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)  # chain size 1
            await _start_run(pool, procedure, compiled, parent_run_id=root_id)  # chain size 2 -- at budget

            with pytest.raises(ChildExecutionBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_wall_clock_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recwallclock-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_wall_clock_seconds", 1)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            # Backdate started_at so the 1-second budget is already exceeded.
            await pool.execute(
                "UPDATE execution_runs SET started_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(seconds=10), root_id,
            )
            with pytest.raises(WallClockBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_token_budget(monkeypatch):
    """B12's token budget: real, ATOMIC usage (durable_run.record_run_usage)
    summed across the whole chain (same root_run_id), never estimated."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-rectokens-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_tokens", 100)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))

            await _dr.record_run_usage(pool, root_id, tokens=150)
            with pytest.raises(TokenBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_tool_call_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-rectoolcalls-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_tool_calls", 5)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            child_id = await _start_run(pool, procedure, compiled, parent_run_id=root_id)
            # Split across TWO runs in the same chain -- proves this is a
            # real SUM over the chain, not just the parent's own row.
            await _dr.record_run_usage(pool, root_id, tool_calls=3)
            await _dr.record_run_usage(pool, child_id, tool_calls=3)

            with pytest.raises(ToolCallBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=child_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_cost_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reccost-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_cost_usd", 1.0)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            await _dr.record_run_usage(pool, root_id, cost_usd=1.5)

            with pytest.raises(CostBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_record_run_usage_is_a_real_atomic_increment_not_an_overwrite():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recusageinc-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)

            await _dr.record_run_usage(pool, root_id, tokens=10, tool_calls=1, cost_usd=0.1)
            await _dr.record_run_usage(pool, root_id, tokens=10, tool_calls=1, cost_usd=0.1)

            row = await pool.fetchrow(
                "SELECT tokens_used, tool_calls_used, cost_usd_used FROM execution_runs WHERE id = $1", root_id,
            )
            assert row["tokens_used"] == 20
            assert row["tool_calls_used"] == 2
            assert float(row["cost_usd_used"]) == pytest.approx(0.2)

            # A zero-usage call is a real, honest no-op -- never a
            # spurious UPDATE for nothing actually spent.
            await _dr.record_run_usage(pool, root_id)
            row2 = await pool.fetchrow("SELECT tokens_used FROM execution_runs WHERE id = $1", root_id)
            assert row2["tokens_used"] == 20
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_describe_child_status_reports_a_live_child_and_ignores_terminal_ones():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recwaiting-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step one"}, {"order": 1, "goal": "step two"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )

            assert await describe_child_status(pool, run_id=root_id, node_row_id=str(node_row_id)) is None

            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            status = await describe_child_status(pool, run_id=root_id, node_row_id=str(node_row_id))
            assert status is not None
            assert status["child_run_id"] == child_id
            assert status["child_status"] == "pending"

            await pool.execute("UPDATE execution_runs SET status = 'cancelled' WHERE id = $1", child_id)
            assert await describe_child_status(pool, run_id=root_id, node_row_id=str(node_row_id)) is None, (
                "a terminal child must no longer be reported as 'waiting on'"
            )
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
