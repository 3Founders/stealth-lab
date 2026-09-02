"""
final-V1 §2 E2E: the retry/resume REST + MCP surface over the proven
durable-run service, against real PostgreSQL.

Proves, end to end:
  * a deterministic-kind run that failed a node -> the surface
    (retry_run_node_by_id / REST POST /v1/runs/{id}/nodes/{n}/retry)
    re-executes it FOR REAL, C then runs, exactly one executions row;
  * GET /v1/runs/{id} shows status / attempt_count / error_class /
    first_pass_success / the plan's implementation binding;
  * a coding-agent-plan run (extractor_version 'find_best_way_plan_compiler@1')
    -> resume returns needs_product_context, NOT a fabricated success;
  * resume/retry by a DIFFERENT resolved identity than created_by
    -> NotYourRun (service) / 403 (REST);
  * a terminal (succeeded) run -> resume is a no-op; retry of a
    succeeded node -> "already succeeded -- terminal".

Skips itself when DATABASE_URL is unset (offline-suite convention).
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.execution import durable_resume as dres  # noqa: E402
from app.execution.durable_run import execute_run, run_status, start_run  # noqa: E402
from app.execution.implementation_registry import register  # noqa: E402
from app.execution.implementation_executor import execute_implementation  # noqa: E402
from app.models.plan import PlanNode  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: [], 1: [0], 2: [1]}  # 0 -> 1 -> 2
OK_CODE = "print('ok')"


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.013] * 1024


async def _procedure(pool):
    res = await capture_procedure(
        pool, name=f"dres-e2e-{uuid.uuid4().hex[:8]}", goal="durable resume probe",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
        provenance="prior_library", scope_type="global", created_by="dres_e2e",
        embedding=await _FakeEmbedder().embed_one("x"),
    )
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", res["id"])
    return res["procedure_id"], res["id"], pv


async def _det_impl(pool) -> str:
    row = await register(
        pool, name=f"dres-e2e-det-{uuid.uuid4().hex[:8]}", kind="deterministic",
        provider="local", created_by="dres_e2e",
    )
    return str(row["id"])


async def _persist_plan(pool, proc_id, pv, row_id, *, extractor_version, nodes):
    async with pool.acquire() as c:
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type, extractor_version) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global',$8) RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "dres-e2e",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}", extractor_version,
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,$4) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}", nodes,
        )
    return str(plan_id), str(graph_id)


def _node(order, goal, deps, code, impl_id):
    return {"order": order, "goal": goal, "deps": deps,
            "parameters": {"code": code}, "implementation_id": impl_id}


async def _solo_client(pool):
    import httpx
    from fastapi import FastAPI
    from app.api import runs as runs_api

    app = FastAPI()

    @app.middleware("http")
    async def _attach_pool(request, call_next):
        request.app.state.pool = pool
        return await call_next(request)

    app.include_router(runs_api.router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deterministic_run_recovers_through_the_retry_surface_and_status_is_reported():
    pool = await create_pool(statement_cache_size=0)
    marker = Path(tempfile.gettempdir()) / f"dres_marker_{uuid.uuid4().hex}"
    if marker.exists():
        marker.unlink()
    b_code = f"import os, sys\nsys.exit(0 if os.path.exists(r'{marker}') else 1)"
    try:
        proc_id, row_id, pv = await _procedure(pool)
        impl_id = await _det_impl(pool)
        nodes = [
            _node(0, "a", [], OK_CODE, impl_id),
            _node(1, "b", [0], b_code, impl_id),
            _node(2, "c", [1], OK_CODE, impl_id),
        ]
        plan_id, graph_id = await _persist_plan(
            pool, proc_id, pv, row_id, extractor_version="durable_resume_e2e@1", nodes=nodes,
        )
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=3, created_by="dres_e2e",
        )

        # --- initial run: drive through the REAL deterministic executor;
        #     B exits 1 (marker absent) -> fails, C blocked.
        by_order = {n["order"]: PlanNode(**n) for n in nodes}

        async def _cb(node_order: int, attempt: int) -> dict:
            node = by_order[node_order]
            res = await execute_implementation(
                pool, node, {"code": node.parameters["code"]}, scope=AccessScope.unrestricted(),
            )
            if getattr(res, "status", None) == "success":
                return {"attempt": attempt}
            raise RuntimeError(f"node {node_order} failed: {res.notes!r}")

        first = await execute_run(pool, run_id, deps=DEPS, run_node=_cb, worker_id="w-init")
        assert first["status"] == "failed", first
        by = {n["node_order"]: n for n in first["nodes"]}
        assert by[0]["status"] == "succeeded"
        assert by[1]["status"] == "failed" and by[1]["error_class"] is not None
        assert by[2]["status"] == "blocked"

        # exactly zero executions rows so far (run not succeeded/terminal-append)
        async with pool.acquire() as c:
            n_exec = await c.fetchval(
                "SELECT count(*) FROM executions WHERE execution_plan_id=$1", plan_id)
        assert n_exec == 0

        # --- fix the world, then RECOVER THROUGH THE SURFACE (no callback) ---
        marker.write_text("go")
        recovered = await dres.retry_run_node_by_id(
            pool, run_id, 1, worker_id="w-surface", actor_id="dres_e2e",
        )
        assert recovered["status"] == "succeeded", recovered
        rby = {n["node_order"]: n for n in recovered["nodes"]}
        assert rby[0]["status"] == rby[1]["status"] == rby[2]["status"] == "succeeded"
        assert rby[1]["attempt_count"] == 2 and rby[1]["first_pass_success"] is False
        assert rby[0]["first_pass_success"] is True  # A was not re-run

        # exactly ONE immutable executions row, pinned to the procedure version
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT procedure_id, procedure_version, outcome FROM executions "
                "WHERE execution_plan_id=$1", plan_id)
        assert len(rows) == 1
        assert str(rows[0]["procedure_id"]) == proc_id and rows[0]["procedure_version"] == pv
        assert rows[0]["outcome"] == "success"

        # --- GET status via the service surface ---
        st = await dres.run_status_by_id(pool, run_id)
        assert st["status"] == "succeeded"
        assert st["created_by"] == "dres_e2e"
        sby = {n["node_order"]: n for n in st["nodes"]}
        assert sby[1]["attempt_count"] == 2
        assert sby[1]["error_class"] is not None
        assert st["first_pass_success"] is False

        hist = await dres.node_history_by_id(pool, run_id)
        hby = {n["node_order"]: n for n in hist["nodes"]}
        # the plan's implementation binding is surfaced (run-node column is
        # NULL until durable_run pins one; the plan node carries it)
        assert (hby[1]["implementation_id"] == uuid.UUID(impl_id)
                or hby[1].get("plan_implementation_id") == impl_id)

        # --- terminal run: resume is a no-op; retry of a succeeded node is refused ---
        noop = await dres.resume_run_by_id(pool, run_id, worker_id="w-x", actor_id="dres_e2e")
        assert noop["status"] == "succeeded" and "no-op" in noop["note"]
        term = await dres.retry_run_node_by_id(pool, run_id, 0, worker_id="w-x", actor_id="dres_e2e")
        assert term["status"] == "succeeded" and "terminal" in term["note"]

        # --- REST leg: GET + a 403 for a different resolved identity ---
        client = await _solo_client(pool)
        async with client:
            r = await client.get(f"/v1/runs/{run_id}")
            assert r.status_code == 200 and r.json()["status"] == "succeeded"
            r_nodes = await client.get(f"/v1/runs/{run_id}/nodes")
            assert r_nodes.status_code == 200
            assert {n["node_order"] for n in r_nodes.json()["nodes"]} == {0, 1, 2}
            # a different, resolvable identity may not mutate this run
            r_403 = await client.post(
                f"/v1/runs/{run_id}/resume", headers={"X-Viewer-Id": "someone-else"})
            assert r_403.status_code == 403, r_403.text
            r_missing = await client.get(f"/v1/runs/{uuid7()}")
            assert r_missing.status_code == 404
    finally:
        if marker.exists():
            marker.unlink()
        await pool.close()


@pytest.mark.asyncio
async def test_coding_agent_plan_run_returns_needs_product_context_not_a_fake_success():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, row_id, pv = await _procedure(pool)
        # coding-agent compiler + nodes with NO durable implementation binding
        nodes = [
            {"order": 0, "goal": "a", "deps": []},
            {"order": 1, "goal": "b", "deps": [0]},
            {"order": 2, "goal": "c", "deps": [1]},
        ]
        plan_id, graph_id = await _persist_plan(
            pool, proc_id, pv, row_id,
            extractor_version="find_best_way_plan_compiler@1", nodes=nodes,
        )
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, created_by="dres_e2e",
        )

        resumed = await dres.resume_run_by_id(
            pool, run_id, worker_id="w-surface", actor_id="dres_e2e",
        )
        assert resumed["status"] == "needs_product_context"
        assert resumed["run_id"] == run_id
        assert "find_best_way" in resumed["detail"]

        retried = await dres.retry_run_node_by_id(
            pool, run_id, 1, worker_id="w-surface", actor_id="dres_e2e",
        )
        assert retried["status"] == "needs_product_context"

        # the run was NOT advanced to any success state
        st = await run_status(pool, run_id)
        assert st["status"] in ("pending", "running")
        assert all(n["status"] in ("pending", "blocked") for n in st["nodes"])

        # REST resume returns the structured refusal with HTTP 200 (it is a
        # real answer, not an error)
        client = await _solo_client(pool)
        async with client:
            r = await client.post(f"/v1/runs/{run_id}/resume", headers={"X-Viewer-Id": "dres_e2e"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "needs_product_context"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_a_different_resolved_identity_cannot_resume_or_retry_another_users_run():
    pool = await create_pool(statement_cache_size=0)
    try:
        proc_id, row_id, pv = await _procedure(pool)
        impl_id = await _det_impl(pool)
        nodes = [_node(0, "a", [], OK_CODE, impl_id),
                 _node(1, "b", [0], OK_CODE, impl_id),
                 _node(2, "c", [1], OK_CODE, impl_id)]
        plan_id, graph_id = await _persist_plan(
            pool, proc_id, pv, row_id, extractor_version="durable_resume_e2e@1", nodes=nodes,
        )
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, created_by="owner-alice",
        )

        with pytest.raises(dres.NotYourRun):
            await dres.resume_run_by_id(pool, run_id, worker_id="w", actor_id="mallory")
        with pytest.raises(dres.NotYourRun):
            await dres.retry_run_node_by_id(pool, run_id, 1, worker_id="w", actor_id="mallory")

        # the real owner (resolved) is allowed; unknown identity is allowed too
        ok = await dres.resume_run_by_id(pool, run_id, worker_id="w", actor_id="owner-alice")
        assert ok["status"] in ("succeeded", "running", "paused", "failed")
    finally:
        await pool.close()
