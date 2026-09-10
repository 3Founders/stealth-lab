"""
MCP hardening B3/B4/B32: ProcedureRun identity (migration 51's columns on
execution_runs) + `continue_run`'s next-action-packet assembly
(app/execution/durable_resume.py::get_run_context).

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.durable_graph import create_pending_run
from app.execution.durable_resume import get_run_context
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)

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
    # A row this test made is_engineering_fixture=false (see
    # _make_verified_approved) can survive the DELETE above if it got
    # referenced by a frozen execution_plans row (every test in this file
    # compiles+persists a real plan) -- restore the correct flag on any
    # such survivor rather than leaving it permanently mis-tagged as
    # "real" in the shared corpus (confirmed live: this exact leftover
    # state leaked into an unrelated test's tier-1 match this session).
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM route_decisions WHERE task_description LIKE $1", f"%{name_prefix}%")


async def _make_verified_approved(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    if "embedding" in kwargs and "embedding_model_id" not in kwargs:
        kwargs["embedding_model_id"] = Embedder().embedding_model_id()
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review",
        scope_type="global", **kwargs,
    )
    row_id = result["id"]
    # Same live-DB is_engineering_fixture default-drift workaround as
    # test_route_decision_e2e.py -- a separate, already-flagged bug, not
    # touched here; see that file's comment for the full explanation.
    await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", row_id)
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    # capture_procedure()'s own return value carries only {id, procedure_id}
    # -- fetch the full row (version, steps, preconditions, ...) since every
    # caller here needs `version` for expand_procedure_steps/compile_plan.
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id))


async def _compile_and_persist(pool, procedure: dict, task_description: str):
    steps = procedure.get("steps") or [{"order": 0, "goal": task_description}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled_plan = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=task_description, nodes=nodes,
        extractor_version="test_procedure_run_e2e@1", created_by="test",
    )
    compiled_plan, _ = await persist_compiled_plan(pool, compiled_plan)
    return compiled_plan


def test_start_run_request_id_is_idempotent_create_or_return():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-runidem-{run_id}"
        request_id = f"idem-{run_id}"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(name, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, embedding=vec, steps=[{"order": 0, "goal": "step one"}],
            )
            compiled_plan = await _compile_and_persist(pool, procedure, name)

            first_id = await create_pending_run(
                pool, compiled_plan, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], created_by="tester",
                request_id=request_id,
            )
            second_id = await create_pending_run(
                pool, compiled_plan, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], created_by="tester",
                request_id=request_id,
            )
            assert first_id == second_id

            node_count = await pool.fetchval(
                "SELECT count(*) FROM execution_run_nodes WHERE execution_run_id = $1", first_id,
            )
            assert node_count == 1, "the second create_pending_run call must not duplicate nodes"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_create_pending_run_stays_pending_and_carries_identity_fields():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-pendingrun-{run_id}"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(name, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, embedding=vec, steps=[{"order": 0, "goal": "step one"}],
            )
            compiled_plan = await _compile_and_persist(pool, procedure, name)

            pending_id = await create_pending_run(
                pool, compiled_plan, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], created_by="tester",
                workspace_id="ws-1", trace_id="trace-1",
            )
            row = await pool.fetchrow("SELECT * FROM execution_runs WHERE id = $1", pending_id)
            assert row["status"] == "pending"
            assert row["workspace_id"] == "ws-1"
            assert row["trace_id"] == "trace-1"
            assert row["final_execution_id"] is None
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_get_run_context_reports_current_node_and_unknown_precondition():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-runcontext-{run_id}"
        never_asserted_subject = f"project:runcontext-probe-{run_id}"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(name, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, embedding=vec,
                steps=[{"order": 0, "goal": "do the first thing"},
                       {"order": 1, "goal": "do the second thing"}],
                preconditions=[
                    {"subject": never_asserted_subject, "predicate": "quota", "object": "available"},
                ],
            )
            compiled_plan = await _compile_and_persist(pool, procedure, name)
            pending_id = await create_pending_run(
                pool, compiled_plan, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], created_by="tester",
            )

            context = await get_run_context(pool, pending_id)
            assert context["procedure_run_id"] == pending_id
            assert context["procedure_id"] == str(procedure["procedure_id"])
            assert context["current_phase_or_node"] == "node:0"
            assert context["objective"] == "do the first thing"
            assert context["allowed_branches"] == []
            assert context["blocking_unknowns"]
            assert context["blocking_unknowns"][0]["subject"] == never_asserted_subject
            assert context["blocking_unknowns"][0]["status"] == "UNKNOWN"
            statuses = {p["subject"]: p["status"] for p in context["required_preconditions"]}
            assert statuses[never_asserted_subject] == "UNKNOWN"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_get_run_context_returns_none_for_missing_run():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            context = await get_run_context(pool, str(uuid4()))
            assert context is None
        finally:
            await pool.close()

    asyncio.run(_run())


def test_continue_run_mcp_tool_roundtrips_through_plan_only():
    """End-to-end through the real MCP surface: find_best_way(mode='plan_only')
    now returns a real procedure_run_id, and continue_run(procedure_run_id)
    returns a matching next-action packet -- no re-search needed."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-continuerun-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"roll out the new caching layer safely ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "add cache layer"}],
            )

            ctx = _FakeContext(pool)
            # repo_path supplied to route around a confirmed, separately
            # flagged pre-existing bug in _authorize_repo_execution (it
            # refuses repo_path=None even in local mode) -- unrelated to
            # this test's own subject.
            with tempfile.TemporaryDirectory() as repo_dir:
                result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
            payload = json.loads(result)
            assert payload["procedure_run_id"], "plan_only must return a real procedure_run_id"

            continue_result = await srv.continue_run(payload["procedure_run_id"], ctx)
            context = json.loads(continue_result)
            assert context["procedure_run_id"] == payload["procedure_run_id"]
            assert context["procedure_id"] == payload["procedure_id"]
            assert context["status"] == "pending"

            missing = await srv.continue_run(str(uuid4()), ctx)
            assert missing.startswith("REFUSED:")
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
