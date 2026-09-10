"""
MCP hardening B6: `report_node_progress` -- host-executed lease
progress reporting transitions a real node's state through the SAME
claim/finish mechanics the server's own driving loop uses, both at the
service layer (`durable_run.report_node_progress`) and the real MCP
tool.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.durable_resume import NotYourRun, report_node_progress_by_id
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


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


async def _start_run(pool, procedure: dict, created_by: str = "tester") -> str:
    steps = procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_report_node_progress_e2e@1", created_by=created_by,
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
        created_by=created_by,
    )


def test_report_node_progress_transitions_a_pending_node_to_succeeded():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportnode-{run_id}"
        try:
            procedure = await _capture(pool, name)
            exec_run_id = await _start_run(pool, procedure)

            outcome = await _dr.report_node_progress(
                pool, exec_run_id, 0, ok=True, result={"diff": "real change"},
            )
            assert outcome == {"claimed": True, "status": "succeeded"}

            row = await pool.fetchrow(
                "SELECT status, result_ref, verification_state FROM execution_run_nodes "
                "WHERE execution_run_id = $1::uuid AND node_order = 0", exec_run_id,
            )
            assert row["status"] == "succeeded"
            assert row["result_ref"] == {"diff": "real change"}
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_report_node_progress_never_rewrites_an_already_succeeded_node():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportnodefence-{run_id}"
        try:
            procedure = await _capture(pool, name)
            exec_run_id = await _start_run(pool, procedure)

            await _dr.report_node_progress(pool, exec_run_id, 0, ok=True, result={"a": 1})
            # A SECOND report for the SAME node must not be silently
            # accepted as a rewrite -- the terminal-state fence + claim
            # gating together refuse it (claimed=False).
            second = await _dr.report_node_progress(pool, exec_run_id, 0, ok=False, error={"boom": True})
            assert second == {"claimed": False, "status": "succeeded"}

            row = await pool.fetchrow(
                "SELECT result_ref FROM execution_run_nodes "
                "WHERE execution_run_id = $1::uuid AND node_order = 0", exec_run_id,
            )
            assert row["result_ref"] == {"a": 1}, "the original result must be untouched"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_report_node_progress_by_id_enforces_ownership():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportnodeauth-{run_id}"
        try:
            procedure = await _capture(pool, name)
            exec_run_id = await _start_run(pool, procedure, created_by="real-owner")

            with pytest.raises(NotYourRun):
                await report_node_progress_by_id(
                    pool, exec_run_id, 0, actor_id="a-different-user", ok=True,
                )
            # The rightful owner succeeds.
            outcome = await report_node_progress_by_id(
                pool, exec_run_id, 0, actor_id="real-owner", ok=True,
            )
            assert outcome["claimed"] is True
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_report_node_progress_mcp_tool_end_to_end():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportnodemcp-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}])
            exec_run_id = await _start_run(pool, procedure)
            ctx = _FakeContext(pool)

            result = await srv.report_node_progress(
                run_id=exec_run_id, node_order=0, ok=True, ctx=ctx,
                result_json=json.dumps({"note": "did the thing"}),
            )
            payload = json.loads(result)
            assert payload == {"claimed": True, "status": "succeeded"}

            bad_json = await srv.report_node_progress(
                run_id=exec_run_id, node_order=1, ok=True, ctx=ctx, result_json="not json",
            )
            assert bad_json.startswith("REFUSED:")

            missing_run = await srv.report_node_progress(
                run_id=str(uuid4()), node_order=0, ok=True, ctx=ctx,
            )
            assert missing_run.startswith("REFUSED:")
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
