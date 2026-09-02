"""
§3 E2E: the production tier-2 execution helper (`run_graph_durably`, which
MCP find_best_way / reproduce_procedure now call instead of the in-memory
graph executor) runs a compiled plan as a DURABLE run, and a real
interrupted run resumes through the SAME helper -- not a low-level
durable_run call.

Proves: one execution_run per graph; implementation/procedure version
pinned; per-node state persisted; crash -> resume re-runs only the
incomplete node; exactly ONE immutable `executions` row (no double
record_plan_execution); verification/first-pass state.

Skips without DATABASE_URL.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

from app.db.session import create_pool  # noqa: E402
from app.execution.durable_graph import run_graph_durably  # noqa: E402
from app.execution.durable_run import WorkerLost, run_status  # noqa: E402
from app.execution.graph_executor import NodeResult  # noqa: E402
from app.execution.plan_persistence import persist_compiled_plan  # noqa: E402
from app.execution.plans import compile_plan  # noqa: E402
from app.models.plan import PlanNode  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.02] * 1024


async def _compiled(pool):
    res = await capture_procedure(
        pool, name=f"dg-e2e-{uuid.uuid4().hex[:8]}", goal="durable graph probe",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
        provenance="prior_library", scope_type="global", created_by="dg_e2e",
        embedding=await _FakeEmbedder().embed_one("x"),
    )
    payload = {
        "id": uuid.UUID(res["id"]), "procedure_id": uuid.UUID(res["procedure_id"]),
        "version": 1, "name": "dg-e2e", "goal": "durable graph probe",
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
        task_description="durable graph e2e", nodes=nodes,
        extractor_version="find_best_way_plan_compiler@1", created_by="dg_e2e",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    return compiled, res["procedure_id"]


@pytest.mark.asyncio
async def test_run_graph_durably_creates_durable_run_and_resumes_through_the_same_helper():
    pool = await create_pool(statement_cache_size=0)
    try:
        compiled, proc_id = await _compiled(pool)
        calls: list[tuple[int, int]] = []
        crashed = {"done": False}

        async def node_runner(node) -> NodeResult:
            calls.append((node.order, len([c for c in calls if c[0] == node.order]) + 1))
            if node.order == 1 and not crashed["done"]:
                crashed["done"] = True
                raise WorkerLost("worker died inside node B")
            return NodeResult(status="success", notes=f"node {node.order} ok")

        # --- run 1: crashes mid node B; propagates WorkerLost ---
        with pytest.raises(WorkerLost):
            await run_graph_durably(
                pool, compiled, node_runner,
                procedure_id=proc_id, procedure_version=1,
                created_by="dg_e2e", scope_type="global",
            )

        # find the run we just started
        async with pool.acquire() as c:
            run_id = str(await c.fetchval(
                "SELECT id FROM execution_runs WHERE execution_plan_id=$1 ORDER BY created_at DESC LIMIT 1",
                str(compiled.plan.id)))
        st = await run_status(pool, run_id)
        by = {n["node_order"]: n for n in st["nodes"]}
        assert by[0]["status"] == "succeeded"
        assert by[1]["status"] == "running"          # crashed mid-node
        assert by[2]["status"] == "pending"
        # exactly zero executions rows so far (run not terminal)
        async with pool.acquire() as c:
            n_exec = await c.fetchval(
                "SELECT count(*) FROM executions WHERE execution_plan_id=$1", str(compiled.plan.id))
        assert n_exec == 0

        # --- resume THROUGH THE SAME PRODUCT HELPER ---
        res = await run_graph_durably(
            pool, compiled, node_runner,
            procedure_id=proc_id, procedure_version=1,
            created_by="dg_e2e", scope_type="global",
            resume_run_id=run_id,
        )
        assert res.outcome == "success"
        assert res.run_id == run_id and res.resume_count == 1

        st2 = await run_status(pool, run_id)
        by2 = {n["node_order"]: n for n in st2["nodes"]}
        assert by2[0]["status"] == by2[1]["status"] == by2[2]["status"] == "succeeded"
        assert by2[1]["attempt_count"] == 2 and by2[1]["first_pass_success"] is False
        assert by2[0]["first_pass_success"] is True
        # A was NOT re-run
        assert [c for c in calls if c[0] == 0] == [(0, 1)]

        # exactly ONE immutable executions row, pinned to the procedure version
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT procedure_id, procedure_version, outcome FROM executions WHERE execution_plan_id=$1",
                str(compiled.plan.id))
        assert len(rows) == 1
        assert str(rows[0]["procedure_id"]) == proc_id and rows[0]["procedure_version"] == 1
        assert rows[0]["outcome"] == "success"
        assert res.final_execution_id  # the executions row id is recorded on the run

        # a second resume of the now-terminal run is an idempotent no-op
        calls_before = len(calls)
        res3 = await run_graph_durably(
            pool, compiled, node_runner, procedure_id=proc_id, procedure_version=1,
            created_by="dg_e2e", scope_type="global", resume_run_id=run_id)
        assert res3.status == "succeeded" and len(calls) == calls_before
    finally:
        await pool.close()
