"""
MCP hardening B36: the `declare_file_intent` MCP tool end to end --
success, cross-run conflict (typed, exact), and malformed JSON refusal.

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


async def _start_run(pool, procedure: dict) -> str:
    steps = procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_declare_file_intent_mcp_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
    )


def test_declare_file_intent_mcp_tool_end_to_end():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_a = f"proc-test-mcpcoorda-{run_id}"
        name_b = f"proc-test-mcpcoordb-{run_id}"
        try:
            proc_a = await _capture(pool, name_a)
            proc_b = await _capture(pool, name_b)
            run_a = await _start_run(pool, proc_a)
            run_b = await _start_run(pool, proc_b)
            ctx = _FakeContext(pool)

            ok_result = await srv.declare_file_intent(
                procedure_run_id=run_a, node_order=0, owner_agent_id="agent-a", ctx=ctx,
                write_exact_json=json.dumps([f"shared/mcp-{run_id}.py"]),
            )
            ok_payload = json.loads(ok_result)
            assert ok_payload["conflict"] is False
            assert ok_payload["declaration"]["owner_agent_id"] == "agent-a"

            conflict_result = await srv.declare_file_intent(
                procedure_run_id=run_b, node_order=0, owner_agent_id="agent-b", ctx=ctx,
                write_exact_json=json.dumps([f"shared/mcp-{run_id}.py"]),
            )
            conflict_payload = json.loads(conflict_result)
            assert conflict_payload["conflict"] is True
            assert conflict_payload["conflicts"][0]["execution_run_id"] == run_a
            assert conflict_payload["conflicts"][0]["owner_agent_id"] == "agent-a"

            bad_json = await srv.declare_file_intent(
                procedure_run_id=run_a, node_order=0, owner_agent_id="agent-a", ctx=ctx,
                write_exact_json="not json",
            )
            assert bad_json.startswith("REFUSED:")
        finally:
            await _cleanup(pool, name_a)
            await _cleanup(pool, name_b)
            await pool.close()

    asyncio.run(_run())
