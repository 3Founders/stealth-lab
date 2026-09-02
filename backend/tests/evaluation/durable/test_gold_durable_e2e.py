"""
This suite's own gold/EvalResult-shaped layer over durable retry/resume
(task spec §6-§7), against real PostgreSQL.

Does NOT duplicate the product's own already-thorough proofs
(tests/test_durable_run_e2e.py, test_durable_graph_e2e.py,
test_durable_resume_e2e.py, and their offline counterparts) -- it exercises
the SAME real entrypoints those files proved correct, asking this suite's
own questions of them: retry exhaustion, an unknown/unmodeled failure
class, side-effecting-node parking, implementation pinning surviving a
resume, and that REST + MCP are two thin skins over one shared service.

Real entrypoint discipline: the full-chain scenario goes through
`durable_graph.run_graph_durably` -- the one production bridge MCP
`find_best_way` tier-2 / `reproduce_procedure` call instead of the
in-memory `graph_executor` (see durable_graph.py's own docstring) -- not a
lower-level `durable_run` call. A literal `find_best_way` MCP tool
invocation would additionally require a live coding-agent sandbox + repo
(explicitly out of scope for this pass, and already documented as
"not measured, requires sandbox" in .scratch/final-v1-perf-sanity.md);
`run_graph_durably` is the entire durable-specific code `find_best_way`
tier-2 delegates to, so exercising it directly covers every durable
semantic without needing that sandbox.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py file
in this suite.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.execution import durable_resume as dres  # noqa: E402
from app.execution.durable_graph import run_graph_durably  # noqa: E402
from app.execution.durable_run import (  # noqa: E402
    ResumeInProgress,
    WorkerLost,
    _claim_run,
    _release_run,
    execute_run,
    resume_run,
    retry_node,
    run_status,
    start_run,
)
from app.execution.graph_executor import NodeResult  # noqa: E402
from app.execution.implementation_executor import execute_implementation  # noqa: E402
from app.execution.implementation_registry import register  # noqa: E402
from app.execution.plan_persistence import persist_compiled_plan  # noqa: E402
from app.execution.plans import compile_plan  # noqa: E402
from app.models.plan import PlanNode  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.authn import Actor, reset_current_actor, set_current_actor  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: [], 1: [0], 2: [1]}  # 0 -> 1 -> 2


def _tag() -> str:
    return uuid.uuid4().hex[:8]


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.011] * 1024


async def _procedure(pool, tag):
    res = await capture_procedure(
        pool, name=f"gold-durable-{tag}", goal="gold durable eval probe",
        steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
        provenance="prior_library", scope_type="global", created_by="gold_durable",
        embedding=await _FakeEmbedder().embed_one(tag),
    )
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", res["id"])
    return res["procedure_id"], res["id"], pv


async def _plan_chain(pool, tag, *, max_attempts=3):
    """A real execution_plans + task_graphs row (minimal fixture shape,
    matching tests/test_durable_run_e2e.py::_plan_chain exactly)."""
    proc_id, row_id, pv = await _procedure(pool, tag)
    async with pool.acquire() as c:
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, f"gold-durable-{tag}",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
    run_id = await start_run(
        pool, execution_plan_id=plan_id, task_graph_id=graph_id,
        procedure_id=proc_id, procedure_version=pv,
        node_orders=[0, 1, 2], deps=DEPS, max_attempts=max_attempts, created_by="gold_durable",
    )
    return run_id, proc_id, pv, plan_id, graph_id


# ---------------------------------------------------------------------------
# Full chain, through the real production bridge (run_graph_durably), not a
# lower-level helper: A succeeds, B crashes mid-node, worker lost, resume
# through the SAME bridge, A not re-run, B retries, C waits on B then runs,
# terminal lineage is exactly one immutable executions row.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_full_chain_a_succeeds_b_crashes_resume_c_waits_terminal_lineage():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        res = await capture_procedure(
            pool, name=f"gold-durable-chain-{tag}", goal="gold durable chain",
            steps=[{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}, {"order": 2, "goal": "c"}],
            provenance="prior_library", scope_type="global", created_by="gold_durable",
            embedding=await _FakeEmbedder().embed_one(tag),
        )
        payload = {
            "id": uuid.UUID(res["id"]), "procedure_id": uuid.UUID(res["procedure_id"]),
            "version": 1, "name": f"gold-durable-chain-{tag}", "goal": "gold durable chain",
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
            task_description=f"gold-durable-chain-{tag}", nodes=nodes,
            extractor_version="find_best_way_plan_compiler@1", created_by="gold_durable",
        )
        compiled, _ = await persist_compiled_plan(pool, compiled)

        calls: list[tuple[int, int]] = []
        crashed = {"done": False}
        c_ran_before_b_succeeded = {"violated": False}
        b_succeeded_once = {"done": False}

        async def node_runner(node) -> NodeResult:
            calls.append((node.order, len([c for c in calls if c[0] == node.order]) + 1))
            if node.order == 2 and not b_succeeded_once["done"]:
                c_ran_before_b_succeeded["violated"] = True  # dependency violated, should never happen
            if node.order == 1:
                if not crashed["done"]:
                    crashed["done"] = True
                    raise WorkerLost("worker died inside node B")
                b_succeeded_once["done"] = True
            return NodeResult(status="success", notes=f"node {node.order} ok")

        with pytest.raises(WorkerLost):
            await run_graph_durably(
                pool, compiled, node_runner,
                procedure_id=res["procedure_id"], procedure_version=1,
                created_by="gold_durable", scope_type="global",
            )

        async with pool.acquire() as c:
            run_id = str(await c.fetchval(
                "SELECT id FROM execution_runs WHERE execution_plan_id=$1 ORDER BY created_at DESC LIMIT 1",
                str(compiled.plan.id)))
        st = await run_status(pool, run_id)
        by = {n["node_order"]: n for n in st["nodes"]}
        assert by[0]["status"] == "succeeded"  # A succeeded before the crash
        assert by[1]["status"] == "running"    # B crashed mid-node -- state persisted
        assert by[2]["status"] == "pending"    # C waits on B, never touched

        result = await run_graph_durably(
            pool, compiled, node_runner,
            procedure_id=res["procedure_id"], procedure_version=1,
            created_by="gold_durable", scope_type="global",
            resume_run_id=run_id,
        )
        assert result.outcome == "success"
        assert result.resume_count == 1
        assert not c_ran_before_b_succeeded["violated"], "C ran before B's dependency was satisfied"
        assert [c for c in calls if c[0] == 0] == [(0, 1)]        # A NOT re-run
        assert (1, 2) in calls and (1, 3) not in calls            # B retried exactly once more
        assert len([c for c in calls if c[0] == 2]) == 1          # C ran exactly once

        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT procedure_id, procedure_version, outcome FROM executions WHERE execution_plan_id=$1",
                str(compiled.plan.id))
        assert len(rows) == 1  # exactly one immutable terminal execution row
        assert str(rows[0]["procedure_id"]) == res["procedure_id"] and rows[0]["procedure_version"] == 1
        assert rows[0]["outcome"] == "success"
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Retry exhaustion: a RETRYABLE error class that exhausts every attempt
# without operator intervention stays failed -- resume alone can't revive it.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_exhaustion_leaves_node_failed_without_operator_intervention():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        run_id, proc_id, pv, plan_id, graph_id = await _plan_chain(pool, tag, max_attempts=2)
        attempts = {"count": 0}

        async def run_node(order, attempt):
            if order == 1:
                attempts["count"] += 1
                raise TimeoutError("always times out")  # retryable, but will exhaust both attempts
            return {"order": order}

        res = await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w1")
        assert res["status"] == "failed"
        by = {n["node_order"]: n for n in res["nodes"]}
        assert by[1]["status"] == "failed" and by[1]["error_class"] == "timeout"
        assert by[1]["attempt_count"] == 2  # both attempts consumed inline
        assert attempts["count"] == 2

        # A plain resume cannot revive an exhausted node (attempt_count >= max_attempts
        # even though the error class itself is retryable) -- proves retry is BOUNDED,
        # not "retryable class == infinite retries".
        r2 = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w2")
        assert r2["status"] == "failed"
        assert {n["node_order"]: n["status"] for n in r2["nodes"]}[1] == "failed"
        assert attempts["count"] == 2  # resume did not attempt it again

        # Only an explicit, forced operator retry (bumping max_attempts) can proceed.
        r3 = await retry_node(pool, run_id, 1, deps=DEPS, run_node=run_node, worker_id="w3", force=True)
        assert attempts["count"] == 3  # the forced retry DID attempt it once more
        assert r3["status"] == "failed"  # still fails (run_node always raises) -- not silently marked success
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Unknown / unmodeled failure class: classify_error's safe default ("logic")
# must not be silently retried.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_failure_class_is_not_silently_retried():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        run_id, proc_id, pv, plan_id, graph_id = await _plan_chain(pool, tag, max_attempts=3)
        calls = {"count": 0}

        class _WeirdUnclassifiableError(Exception):
            pass

        async def run_node(order, attempt):
            if order == 1:
                calls["count"] += 1
                raise _WeirdUnclassifiableError("something nobody wrote a classifier rule for")
            return {"order": order}

        res = await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w1")
        assert res["status"] == "failed"
        by = {n["node_order"]: n for n in res["nodes"]}
        assert by[1]["error_class"] == "logic"  # the safe unrecognized-failure default
        assert by[1]["attempt_count"] == 1      # NOT retried inline
        assert calls["count"] == 1

        r2 = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w2")
        assert r2["status"] == "failed"
        assert calls["count"] == 1  # a plain resume did not retry an unrecognized failure class either
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Side-effecting node crash: parks conservatively (run 'paused', node
# 'resumable') rather than blind-rerunning a side-effecting step.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_side_effecting_node_crash_parks_instead_of_blind_rerun():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        proc_id, row_id, pv = await _procedure(pool, tag)
        async with pool.acquire() as c:
            plan_id = await c.fetchval(
                "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                " task_description, procedure_content_hash, content_hash, scope_type) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                str(uuid7()), proc_id, pv, row_id, f"gold-durable-side-{tag}",
                f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
            )
            graph_id = await c.fetchval(
                "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
            )
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=3, created_by="gold_durable",
            side_effecting={1},  # node B is side-effecting (e.g. "send the email")
        )
        calls = {1: 0}
        should_crash = {"on": True}

        async def run_node(order, attempt):
            if order == 1:
                calls[1] += 1
                if should_crash["on"]:
                    raise WorkerLost("worker died mid-send")
            return {"order": order}

        with pytest.raises(WorkerLost):
            await execute_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w1")
        assert calls[1] == 1

        # A resume must PARK a crashed side-effecting node, not blind-rerun it.
        parked = await resume_run(pool, run_id, deps=DEPS, run_node=run_node, worker_id="w2")
        assert parked["status"] == "paused"
        by = {n["node_order"]: n for n in parked["nodes"]}
        assert by[1]["status"] == "resumable"
        assert calls[1] == 1  # NOT re-invoked automatically

        # Only an explicit retry_node (the operator's deliberate decision) proceeds.
        should_crash["on"] = False  # the world is fixed; this attempt should succeed
        done = await retry_node(pool, run_id, 1, deps=DEPS, run_node=run_node, worker_id="w3")
        assert calls[1] == 2
        assert done["status"] == "succeeded"
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Concurrent resume refusal (this suite's own instance of the product's
# already-proven scenario).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_resume_is_refused_not_duplicated():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        run_id, proc_id, pv, plan_id, graph_id = await _plan_chain(pool, tag)
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
        async def ok_node(order, attempt):
            return {"order": order}
        done = await execute_run(pool, run_id, deps=DEPS, run_node=ok_node, worker_id="workerC")
        assert done["status"] == "succeeded"
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Duplicate resume of an already-terminal run is a no-op: no duplicate
# executions row (duplicate-evidence prevention).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_resume_produces_no_duplicate_evidence():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        # execute_run/resume_run only append an `executions` row when a
        # `compiled` plan is supplied (durable_run.py's own docstring) --
        # a real compiled+persisted plan is required here so there is
        # actual evidence to prove is NOT duplicated.
        res = await capture_procedure(
            pool, name=f"gold-durable-dup-{tag}", goal="gold durable dup-evidence probe",
            steps=[{"order": 0, "goal": "a"}], provenance="prior_library",
            scope_type="global", created_by="gold_durable",
            embedding=await _FakeEmbedder().embed_one(tag),
        )
        payload = {
            "id": uuid.UUID(res["id"]), "procedure_id": uuid.UUID(res["procedure_id"]),
            "version": 1, "name": f"gold-durable-dup-{tag}", "goal": "gold durable dup-evidence probe",
            "steps": [{"order": 0, "goal": "a"}],
        }
        compiled = compile_plan(
            procedure_id=payload["procedure_id"], procedure_version=1,
            procedure_row_id=payload["id"], procedure_payload=payload,
            task_description=f"gold-durable-dup-{tag}", nodes=[PlanNode(order=0, goal="a", deps=[])],
            extractor_version="find_best_way_plan_compiler@1", created_by="gold_durable",
        )
        compiled, _ = await persist_compiled_plan(pool, compiled)
        run_id = await start_run(
            pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
            procedure_id=res["procedure_id"], procedure_version=1,
            node_orders=[0], deps={0: []}, created_by="gold_durable",
        )

        async def ok_node(order, attempt):
            return {"order": order}

        first = await execute_run(pool, run_id, deps={0: []}, run_node=ok_node, worker_id="w1", compiled=compiled)
        assert first["status"] == "succeeded"

        for worker in ("w2", "w3", "w4"):
            again = await resume_run(
                pool, run_id, deps={0: []}, run_node=ok_node, worker_id=worker, compiled=compiled,
            )
            assert again["status"] == "succeeded" and "no-op" in again["note"]

        async with pool.acquire() as c:
            n_exec = await c.fetchval(
                "SELECT count(*) FROM executions WHERE execution_plan_id=$1", str(compiled.plan.id))
        assert n_exec == 1  # three redundant resumes -> still exactly one evidence row
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Implementation binding is pinned at compile time and is NEVER silently
# re-resolved to a newer Implementation across a resume.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_implementation_binding_survives_resume_unchanged_despite_a_newer_registration():
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        proc_id, row_id, pv = await _procedure(pool, tag)
        impl_v1 = await register(
            pool, name=f"gold-durable-impl-{tag}", kind="deterministic",
            provider="local", created_by="gold_durable", version=1,
        )
        payload = {
            "id": uuid.UUID(row_id), "procedure_id": uuid.UUID(proc_id),
            "version": pv, "name": f"gold-durable-impl-{tag}", "goal": "pin check",
            "steps": [{"order": 0, "goal": "a"}],
        }
        nodes = [PlanNode(order=0, goal="a", deps=[], implementation_id=str(impl_v1["id"]))]
        compiled = compile_plan(
            procedure_id=payload["procedure_id"], procedure_version=pv,
            procedure_row_id=payload["id"], procedure_payload=payload,
            task_description=f"gold-durable-impl-{tag}", nodes=nodes,
            extractor_version="durable_resume_e2e@1", created_by="gold_durable",
        )
        compiled, _ = await persist_compiled_plan(pool, compiled)

        run_id = await start_run(
            pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0], deps={0: []}, created_by="gold_durable",
        )

        # Simulate the world moving on: a NEWER implementation registered
        # AFTER this plan was already frozen.
        impl_v2 = await register(
            pool, name=f"gold-durable-impl-{tag}", kind="deterministic",
            provider="local", created_by="gold_durable", version=2,
        )
        assert str(impl_v2["id"]) != str(impl_v1["id"])

        async def run_node(order, attempt):
            raise WorkerLost("crash before we can observe the binding")

        with pytest.raises(WorkerLost):
            await execute_run(pool, run_id, deps={0: []}, run_node=run_node, worker_id="w1")

        # Resume through the context-free surface, which REBUILDS the
        # CompiledPlan from the persisted (frozen, trigger-immutable)
        # execution_plans/task_graphs rows -- never from "whatever the
        # registry's current best implementation is now".
        run_row, rebuilt, deps, node_rows = await dres._rebuild(pool, run_id)
        bound_order0 = {n.order: n.implementation_id for n in rebuilt.graph.nodes}[0]
        assert bound_order0 == str(impl_v1["id"])
        assert bound_order0 != str(impl_v2["id"])
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# REST + MCP: one shared service, real authz/cross-user refusal, terminal
# idempotency, retry policy can't be bypassed via the surface either.
# ---------------------------------------------------------------------------
async def _rest_client(pool):
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


class _McpCtx:
    def __init__(self, pool):
        class _RC:
            pass
        self.request_context = _RC()
        self.request_context.lifespan_context = {"pool": pool}


@pytest.mark.asyncio
async def test_rest_and_mcp_are_two_skins_over_the_same_durable_resume_service():
    import app.mcp_server.server as srv

    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    try:
        proc_id, row_id, pv = await _procedure(pool, tag)
        impl = await register(
            pool, name=f"gold-durable-surface-{tag}", kind="deterministic",
            provider="local", created_by="gold_durable",
        )
        # NOTE: passed as a plain Python list, not json.dumps()'d text --
        # create_pool() registers a JSONB codec that serializes Python
        # objects itself; handing it already-encoded JSON text double-encodes
        # the column (the codec then wraps the string AGAIN), which is
        # exactly the bug this comment is here to keep someone from
        # reintroducing (matches tests/test_durable_resume_e2e.py's own
        # _persist_plan, which passes `nodes` as a bare list for the same
        # reason).
        nodes_list = [
            {"order": 0, "goal": "a", "deps": [], "parameters": {"code": "print('ok')"},
             "implementation_id": str(impl["id"])},
            {"order": 1, "goal": "b", "deps": [0], "parameters": {"code": "print('ok')"},
             "implementation_id": str(impl["id"])},
        ]
        async with pool.acquire() as c:
            plan_id = await c.fetchval(
                "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                " task_description, procedure_content_hash, content_hash, scope_type, extractor_version) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'global',$8) RETURNING id",
                str(uuid7()), proc_id, pv, row_id, f"gold-durable-surface-{tag}",
                f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}", "durable_resume_e2e@1",
            )
            graph_id = await c.fetchval(
                "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                "VALUES ($1,$2,$3,$4) RETURNING id",
                str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}", nodes_list,
            )
        run_id = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1], deps={0: [], 1: [0]}, max_attempts=2, created_by="owner-gold",
        )
        by_order = {n["order"]: PlanNode(**n) for n in nodes_list}

        async def _cb(node_order, attempt):
            node = by_order[node_order]
            result = await execute_implementation(
                pool, node, {"code": node.parameters["code"]}, scope=AccessScope.unrestricted(),
            )
            if getattr(result, "status", None) == "success":
                return {"attempt": attempt}
            raise RuntimeError(f"node {node_order} failed: {result.notes!r}")

        drive = await execute_run(pool, run_id, deps={0: [], 1: [0]}, run_node=_cb, worker_id="w-init")
        assert drive["status"] == "succeeded"

        rest = await _rest_client(pool)
        async with rest:
            r = await rest.get(f"/v1/runs/{run_id}")
            assert r.status_code == 200 and r.json()["status"] == "succeeded"
            # cross-user refusal via REST
            r_403 = await rest.post(f"/v1/runs/{run_id}/resume", headers={"X-Viewer-Id": "someone-else"})
            assert r_403.status_code == 403

        ctx = _McpCtx(pool)
        mcp_status = json.loads(await srv.inspect_run(run_id, ctx))
        assert mcp_status["status"]["status"] == "succeeded"
        # same shared service under both surfaces: identical terminal status
        assert mcp_status["status"]["status"] == r.json()["status"]

        # cross-user refusal via MCP (a resolved but different identity)
        token = set_current_actor(Actor(subject="mallory"))
        try:
            mcp_refused = await srv.resume_execution_run(run_id, ctx)
        finally:
            reset_current_actor(token)
        assert mcp_refused.startswith("REFUSED: not your run")

        # terminal-run idempotency via MCP, as the real owner
        token = set_current_actor(Actor(subject="owner-gold"))
        try:
            mcp_noop = json.loads(await srv.resume_execution_run(run_id, ctx))
            assert mcp_noop["status"] == "succeeded" and "no-op" in mcp_noop["note"]

            # a successful node cannot be retried, even by the real owner
            mcp_retry_succeeded_node = json.loads(await srv.retry_run_node(run_id, 0, ctx))
            assert mcp_retry_succeeded_node["status"] == "succeeded"
            assert "terminal" in mcp_retry_succeeded_node["note"]
        finally:
            reset_current_actor(token)
    finally:
        await pool.close()
