"""Live-database proving tests for app/execution/implementation_executor.py::
bind_plan_implementations. Requires a real DATABASE_URL, skips (not fails)
without one -- same pattern as test_implementation_executor_e2e.py.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution.implementation_executor import bind_plan_implementations
from app.execution.implementation_registry import activate, register
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.services.access import AccessScope
from app.services.procedures import capture_procedure, get_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "plan-impl-bind-e2e"


async def _cleanup(pool) -> None:
    # execution_plans/task_graphs are append-only/frozen by trigger
    # (Band 1.7) -- never deleted. Each test compiles against a unique
    # (uuid4-suffixed) task_description, so content_hash never collides
    # across runs and leftover rows are harmless.
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")
    # task_nodes/procedures are left alone too: execution_plans rows from a
    # prior run (append-only, never deleted) may still FK-reference them.


async def _task_node(pool, name: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
        name, f"skill_{name}",
    )
    return str(row["id"])


async def _procedure(pool, name: str) -> dict:
    captured = await capture_procedure(
        pool, name=name, goal="do the real thing",
        steps=[{"order": 0, "goal": "do the real thing"}],
        provenance="system_pending_review", scope_type="global",
        created_by=PREFIX,
    )
    payload = await get_procedure(pool, captured["id"])
    assert payload is not None
    return payload


def _compile_for(procedure: dict, task_description: str):
    return compile_plan(
        procedure_id=procedure["procedure_id"],
        procedure_version=procedure["version"],
        procedure_row_id=procedure["id"],
        procedure_payload=procedure,
        task_description=task_description,
        nodes=[{"order": 0, "goal": "do the real thing", "deps": []}],
        extractor_version="plan_implementation_binding_e2e@1",
        created_by=PREFIX,
    )


# ---------------------------------------------------------------------
# (a) real implementation registered+activated -> persisted plan carries it
# ---------------------------------------------------------------------


def test_bound_implementation_survives_persist_when_one_is_registered_and_active():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_id = await _task_node(pool, f"{PREFIX}-task")
            procedure = await _procedure(pool, f"{PREFIX}-proc-bound")

            impl = await register(
                pool, name=f"{PREFIX}-impl", kind="tool", provider="graphify",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            await activate(pool, impl["id"])

            compiled = _compile_for(procedure, f"{PREFIX} bound task {uuid4()}")
            bound = await bind_plan_implementations(
                pool, compiled, scope=scope, task_node_ids={0: task_id},
            )
            assert bound.graph.nodes[0].implementation_id == impl["id"]
            assert bound is not compiled

            persisted, was_new = await persist_compiled_plan(pool, bound)
            assert was_new is True
            assert persisted.graph.nodes[0].implementation_id == impl["id"]

            # Read back for real -- the persisted row itself, not the
            # in-memory object, carries the durable id.
            row = await pool.fetchrow(
                "SELECT nodes FROM task_graphs WHERE id = $1", persisted.graph.id,
            )
            assert row is not None
            import json
            stored_nodes = json.loads(row["nodes"]) if isinstance(row["nodes"], str) else row["nodes"]
            assert stored_nodes[0]["implementation_id"] == impl["id"]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# (b) nothing registered -> implementation_id stays None, unchanged behavior
# ---------------------------------------------------------------------


def test_no_registered_implementation_leaves_id_none_unchanged_behavior():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_id = await _task_node(pool, f"{PREFIX}-empty-task")
            procedure = await _procedure(pool, f"{PREFIX}-proc-empty")

            compiled = _compile_for(procedure, f"{PREFIX} unbound task {uuid4()}")
            bound = await bind_plan_implementations(
                pool, compiled, scope=scope, task_node_ids={0: task_id},
            )
            # No active implementation registered for task_id -- true no-op.
            assert bound is compiled
            assert bound.graph.nodes[0].implementation_id is None

            persisted, was_new = await persist_compiled_plan(pool, bound)
            assert was_new is True
            assert persisted.graph.nodes[0].implementation_id is None

            row = await pool.fetchrow(
                "SELECT nodes FROM task_graphs WHERE id = $1", persisted.graph.id,
            )
            assert row is not None
            import json
            stored_nodes = json.loads(row["nodes"]) if isinstance(row["nodes"], str) else row["nodes"]
            assert stored_nodes[0]["implementation_id"] is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
