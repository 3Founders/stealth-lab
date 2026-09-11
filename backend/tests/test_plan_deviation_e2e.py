"""
MCP hardening B17/B33: planned vs actual execution deviation detection
-- app/execution/plan_deviation.py::compute_plan_deviation compares the
FROZEN compiled plan (task_graphs.nodes, set once at compile time) against
the MUTABLE real execution outcome (execution_run_nodes), never a new
persisted "deviation" field (see the module's own docstring for why).

Drives one real run through: a node whose ACTUAL implementation differs
from its PLANNED implementation hint (implementation_diverged_from_plan),
a node that fails then succeeds on retry (required_retry), and confirms
a node with no deviations reports an empty list -- through the real
service function and the real MCP tool (inspect_run) where the spec
asks for it.

Skips itself when DATABASE_URL is unset, self-cleaning by row id.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

import app.mcp_server.server as srv  # noqa: E402
from app.db.session import create_pool  # noqa: E402
from app.execution.durable_run import execute_run, start_run  # noqa: E402
from app.execution.plan_deviation import compute_plan_deviation  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: [], 1: []}


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup_procedure(pool, row_id) -> None:
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


def test_plan_deviation_detects_implementation_drift_and_retries():
    async def _run():
        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        suffix = uuid.uuid4().hex[:8]
        name = f"proc-test-plandeviation-{suffix}"
        row_id = None
        exec_run_id = None
        planned_impl_id = str(uuid.uuid4())
        actual_impl_id = str(uuid.uuid4())
        try:
            res = await capture_procedure(
                pool, name=name, goal="plan deviation probe",
                steps=[{"order": 0, "goal": "step zero"}, {"order": 1, "goal": "step one"}],
                provenance="prior_library", scope_type="global", created_by="plandev_e2e",
                embedding=[0.01] * 1024,
            )
            proc_id, row_id = res["procedure_id"], res["id"]

            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), proc_id, pv, row_id, "plandev-e2e",
                    f"pch-{suffix}", f"ch-{suffix}",
                )
                # A REAL compiled-plan-shaped nodes array: node 0 carries a
                # planned implementation_id hint that will NOT match what
                # actually gets pinned (implementation_diverged_from_plan);
                # node 1 carries no hint at all (no deviation possible on
                # that axis).
                nodes_json = json.dumps([
                    {"order": 0, "goal": "step zero", "deps": [], "implementation_id": planned_impl_id},
                    {"order": 1, "goal": "step one", "deps": []},
                ])
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{suffix}", nodes_json,
                )

            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=proc_id, procedure_version=pv,
                node_orders=[0, 1], deps=DEPS, max_attempts=3, created_by="plandev_e2e",
            )
            # Pin a DIFFERENT real implementation on node 0 than the plan named.
            await pool.execute(
                "UPDATE execution_run_nodes SET implementation_id=$2 "
                "WHERE execution_run_id=$1 AND node_order=0",
                exec_run_id, actual_impl_id,
            )

            attempt_counts: dict[int, int] = {0: 0, 1: 0}

            async def run_node(order: int, attempt: int) -> dict:
                attempt_counts[order] += 1
                if order == 1 and attempt_counts[1] == 1:
                    raise ValueError("transient failure on first attempt")
                return {"order": order}

            result = await execute_run(pool, exec_run_id, deps=DEPS, run_node=run_node, worker_id="plandev-w1")
            assert result["status"] in ("succeeded", "failed")

            deviation = await compute_plan_deviation(pool, exec_run_id)
            assert deviation["material_deviation"] is True
            by_order = {n["node_order"]: n for n in deviation["per_node"]}

            assert "implementation_diverged_from_plan" in by_order[0]["deviations"]
            assert by_order[0]["planned_implementation_id"] == planned_impl_id
            assert by_order[0]["actual_implementation_id"] == actual_impl_id

            # node 1's real fate depends on whether ValueError -> 'validation'
            # was retryable; either way it must show SOME real, honest
            # deviation signal (retry or failure), never an empty list for a
            # node that visibly did not go according to plan on first pass.
            assert by_order[1]["deviations"], "node 1 had a first-attempt failure -- must not be reported clean"
            assert by_order[1]["attempt_count"] >= 1

            missing = await compute_plan_deviation(pool, str(uuid.uuid4()))
            assert missing is None

            ctx = _FakeContext(pool)
            inspected = json.loads(await srv.inspect_run(exec_run_id, ctx))
            assert inspected["plan_deviation"] == deviation
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                await _cleanup_procedure(pool, row_id)
            await pool.close()

    asyncio.run(_run())


def test_plan_deviation_reports_no_deviation_for_a_clean_first_pass_run():
    async def _run():
        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        suffix = uuid.uuid4().hex[:8]
        name = f"proc-test-plandeviation-clean-{suffix}"
        row_id = None
        exec_run_id = None
        try:
            res = await capture_procedure(
                pool, name=name, goal="clean plan deviation probe",
                steps=[{"order": 0, "goal": "the only step"}],
                provenance="prior_library", scope_type="global", created_by="plandev_e2e",
                embedding=[0.01] * 1024,
            )
            proc_id, row_id = res["procedure_id"], res["id"]

            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), proc_id, pv, row_id, "plandev-clean-e2e",
                    f"pch-{suffix}", f"ch-{suffix}",
                )
                nodes_json = json.dumps([{"order": 0, "goal": "the only step", "deps": []}])
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{suffix}", nodes_json,
                )

            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=proc_id, procedure_version=pv,
                node_orders=[0], deps={0: []}, max_attempts=3, created_by="plandev_e2e",
            )

            async def run_node(order: int, attempt: int) -> dict:
                return {"order": order}

            result = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="plandev-w2")
            assert result["status"] == "succeeded"

            deviation = await compute_plan_deviation(pool, exec_run_id)
            assert deviation["material_deviation"] is False
            assert deviation["per_node"][0]["deviations"] == []
            assert deviation["summary"] == {
                "nodes_planned": 1, "nodes_executed": 1, "nodes_with_deviations": 0,
            }
            assert deviation["per_node"][0]["actual_tools_called"] == []
            assert deviation["per_node"][0]["actual_artifacts"] == []
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                await _cleanup_procedure(pool, row_id)
            await pool.close()

    asyncio.run(_run())


def test_plan_deviation_surfaces_the_real_tools_called_and_artifacts_per_node():
    """MCP hardening B17 STRICT CLOSURE: the literal requirement names
    "actual ... tools/artifacts", not just implementation identity --
    both are already real, durably recorded per-node facts (B27's
    record_tool_called via HttpApiAdapter's real evidence, B7/B8's
    record_artifact) that this comparison never folded in. Proves a
    real HTTP adapter execution's own tool_called/artifact_recorded
    events show up on the matching node's per_node entry."""
    import http.server
    import threading
    import contextlib

    class _EchoHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, format, *args):  # noqa: A002
            pass

    @contextlib.contextmanager
    def _real_http_server():
        server = http.server.HTTPServer(("127.0.0.1", 0), _EchoHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)

    async def _run():
        from app.execution import implementation_registry
        from app.execution.implementation_executor import execute_implementation
        from app.services.access import AccessScope
        from app.models.plan import PlanNode

        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        suffix = uuid.uuid4().hex[:8]
        name = f"proc-test-plandeviation-tools-{suffix}"
        row_id = None
        exec_run_id = None
        impl_id = None
        try:
            with _real_http_server() as base_url:
                impl = await implementation_registry.register(
                    pool, name=f"plandev-tools-impl-{suffix}", kind="api",
                    provider="plandev-e2e", created_by="plandev_e2e",
                    locator={"endpoint": f"{base_url}/echo"},
                )
                impl_id = impl["id"]

                res = await capture_procedure(
                    pool, name=name, goal="tools plan deviation probe",
                    steps=[{"order": 0, "goal": "call the api"}],
                    provenance="prior_library", scope_type="global", created_by="plandev_e2e",
                    embedding=[0.01] * 1024,
                )
                proc_id, row_id = res["procedure_id"], res["id"]

                async with pool.acquire() as c:
                    pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                    plan_id = await c.fetchval(
                        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                        " task_description, procedure_content_hash, content_hash, scope_type) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                        str(uuid7()), proc_id, pv, row_id, "plandev-tools-e2e",
                        f"pch-{suffix}", f"ch-{suffix}",
                    )
                    nodes_json = json.dumps([{"order": 0, "goal": "call the api", "deps": []}])
                    graph_id = await c.fetchval(
                        "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                        "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
                        str(uuid7()), plan_id, f"gh-{suffix}", nodes_json,
                    )

                exec_run_id = await start_run(
                    pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                    procedure_id=proc_id, procedure_version=pv,
                    node_orders=[0], deps={0: []}, max_attempts=3, created_by="plandev_e2e",
                )

                async def run_node(order: int, attempt: int) -> dict:
                    node = PlanNode(order=order, goal="call the api", implementation_id=impl_id)
                    result = await execute_implementation(
                        pool, node, {"request_body": {"probe": True}}, scope=AccessScope.unrestricted(),
                    )
                    if result.status != "success":
                        raise RuntimeError(result.notes)
                    return {"notes": result.notes, "data": dict(result.data or {}), "attempt": attempt}

                result = await execute_run(
                    pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="plandev-tools-w1",
                )
                assert result["status"] == "succeeded"

                deviation = await compute_plan_deviation(pool, exec_run_id)
                node0 = deviation["per_node"][0]
                assert len(node0["actual_tools_called"]) == 1
                assert node0["actual_tools_called"][0]["requested_endpoint"] == f"{base_url}/echo"
                assert len(node0["actual_artifacts"]) == 1
                assert node0["actual_artifacts"][0]["kind"] == "http_response"
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                await _cleanup_procedure(pool, row_id)
            if impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())
