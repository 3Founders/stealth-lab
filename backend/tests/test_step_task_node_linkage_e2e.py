"""Live-database proving tests, this session's own two checks:

(1) PERSISTENT TASK IDENTITY <-> EXECUTION NODE LINK -- a stored
    procedure step that names a real `task_nodes.id`
    (`{"task_node_id": "<uuid>"}`) survives `steps_to_linear_nodes()` /
    `expand_procedure_steps()` onto `PlanNode.task_node_id` (new field,
    app/models/plan.py), and `bind_plan_implementations()` resolves and
    freezes a durable implementation for it WITHOUT the caller having to
    pass an explicit `task_node_ids={order: id}` map -- the real gap this
    session closed (see app/execution/implementation_executor.py's
    module docstring, path (a)). A step that names no task_node_id stays
    exactly as honest as before: `PlanNode.task_node_id is None`, nothing
    resolves, no fabrication.

(2) EXECUTOR EVIDENCE HONESTY -- `implementation_executor.plan_
    implementation_id()` never fabricates a shared identity when a
    graph's nodes disagree (some bound to different durable ids, or some
    bound and some not): it returns `None`, and `record_plan_execution`
    persists that honest `None` rather than defaulting to a placeholder.
    This locks in behavior that was ALREADY correct (no fix needed here)
    -- see the session investigation for the full trace confirming no
    call site defaults/fabricates an implementation identity.

Requires a real DATABASE_URL, skips (not fails) without one -- same
pattern as test_plan_implementation_binding_e2e.py.
"""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution.implementation_executor import (
    bind_plan_implementations,
    plan_implementation_id,
)
from app.execution.implementation_registry import activate, register
from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps, steps_to_linear_nodes
from app.services.access import AccessScope
from app.services.procedures import capture_procedure, get_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "step-tnid-e2e"


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")
    # task_nodes/procedures/execution_plans are append-only/FK-referenced
    # by prior runs -- left alone, same discipline as the sibling e2e file.


async def _task_node(pool, name: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
        name, f"skill_{name}",
    )
    return str(row["id"])


async def _procedure_with_step_task_node(pool, name: str, task_node_id: str) -> dict:
    captured = await capture_procedure(
        pool, name=name, goal="do the real thing",
        steps=[{"order": 0, "goal": "do the real thing", "task_node_id": task_node_id}],
        provenance="system_pending_review", scope_type="global",
        created_by=PREFIX,
    )
    payload = await get_procedure(pool, captured["id"])
    assert payload is not None
    return payload


# ---------------------------------------------------------------------
# (1a) step-level task_node_id survives steps_to_linear_nodes() -- pure,
# no pool needed for this half.
# ---------------------------------------------------------------------


def test_step_task_node_id_lifts_into_plan_node_offline():
    tid = str(uuid4())
    nodes = steps_to_linear_nodes([
        {"order": 0, "goal": "do the real thing", "task_node_id": tid},
        {"order": 1, "goal": "a step naming no task node at all"},
    ])
    assert nodes[0].task_node_id == tid
    assert nodes[1].task_node_id is None


# ---------------------------------------------------------------------
# (1b) real DB path: expand_procedure_steps -> compile_plan ->
# bind_plan_implementations resolves automatically off node.task_node_id,
# with NO explicit task_node_ids map passed -- the real fix.
# ---------------------------------------------------------------------


def test_bind_resolves_from_step_task_node_id_without_explicit_map():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_id = await _task_node(pool, f"{PREFIX}-task-{uuid4()}")
            procedure = await _procedure_with_step_task_node(
                pool, f"{PREFIX}-proc-{uuid4()}", task_id,
            )

            impl = await register(
                pool, name=f"{PREFIX}-impl-{uuid4()}", kind="tool", provider="graphify",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            await activate(pool, impl["id"])

            nodes = await expand_procedure_steps(
                pool, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], steps=procedure["steps"],
            )
            assert nodes[0].task_node_id == task_id

            compiled = compile_plan(
                procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"],
                procedure_row_id=procedure["id"],
                procedure_payload=procedure,
                task_description=f"{PREFIX} auto-bind task {uuid4()}",
                nodes=nodes,
                extractor_version=f"{PREFIX}@1",
                created_by=PREFIX,
            )

            # THE REAL PROOF: no task_node_ids= kwarg at all.
            bound = await bind_plan_implementations(pool, compiled, scope=scope)
            assert bound.graph.nodes[0].implementation_id == impl["id"]
            assert bound is not compiled

            persisted, was_new = await persist_compiled_plan(pool, bound)
            assert was_new is True
            assert persisted.graph.nodes[0].implementation_id == impl["id"]
            assert persisted.graph.nodes[0].task_node_id == task_id

            row = await pool.fetchrow(
                "SELECT nodes FROM task_graphs WHERE id = $1", persisted.graph.id,
            )
            stored_nodes = json.loads(row["nodes"]) if isinstance(row["nodes"], str) else row["nodes"]
            assert stored_nodes[0]["implementation_id"] == impl["id"]
            assert stored_nodes[0]["task_node_id"] == task_id

            # Single-node graph agrees -> plan_implementation_id names it,
            # and record_plan_execution persists that real identity (not
            # a placeholder) onto the executions row.
            assert plan_implementation_id(persisted) == impl["id"]
            execution_id = await record_plan_execution(
                pool, compiled=persisted, outcome="success", created_by=PREFIX,
                implementation_id=plan_implementation_id(persisted),
            )
            exec_row = await pool.fetchrow(
                "SELECT implementation_id FROM executions WHERE id = $1", execution_id,
            )
            assert str(exec_row["implementation_id"]) == impl["id"]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# (1c) a step naming NO task_node_id: unchanged, honest no-op -- proves
# the new field doesn't change behavior for the overwhelming-majority
# case of a plain goal-string step.
# ---------------------------------------------------------------------


def test_step_without_task_node_id_still_resolves_nothing():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            captured = await capture_procedure(
                pool, name=f"{PREFIX}-proc-plain-{uuid4()}", goal="do the real thing",
                steps=[{"order": 0, "goal": "do the real thing"}],
                provenance="system_pending_review", scope_type="global",
                created_by=PREFIX,
            )
            procedure = await get_procedure(pool, captured["id"])
            assert procedure is not None

            nodes = await expand_procedure_steps(
                pool, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], steps=procedure["steps"],
            )
            assert nodes[0].task_node_id is None

            compiled = compile_plan(
                procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"],
                procedure_row_id=procedure["id"],
                procedure_payload=procedure,
                task_description=f"{PREFIX} plain task {uuid4()}",
                nodes=nodes,
                extractor_version=f"{PREFIX}@1",
                created_by=PREFIX,
            )
            bound = await bind_plan_implementations(pool, compiled, scope=scope)
            # True no-op: nothing resolved anywhere -> same object back.
            assert bound is compiled
            assert bound.graph.nodes[0].implementation_id is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# (2) EXECUTOR EVIDENCE HONESTY: a graph whose nodes disagree on bound
# implementation identity gets an honest None from plan_implementation_id
# -- never an arbitrary pick -- and record_plan_execution persists that
# real None rather than a fabricated placeholder. Locks in already-
# correct behavior (no fix was needed for this check).
# ---------------------------------------------------------------------


def test_disagreeing_nodes_record_honest_none_not_a_placeholder():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_a = await _task_node(pool, f"{PREFIX}-task-a-{uuid4()}")
            task_b = await _task_node(pool, f"{PREFIX}-task-b-{uuid4()}")

            impl_a = await register(
                pool, name=f"{PREFIX}-impl-a-{uuid4()}", kind="tool", provider="graphify",
                created_by=PREFIX, task_node_ids=[task_a],
            )
            await activate(pool, impl_a["id"])
            impl_b = await register(
                pool, name=f"{PREFIX}-impl-b-{uuid4()}", kind="tool", provider="graphify",
                created_by=PREFIX, task_node_ids=[task_b],
            )
            await activate(pool, impl_b["id"])

            captured = await capture_procedure(
                pool, name=f"{PREFIX}-proc-two-step-{uuid4()}", goal="do two real things",
                steps=[
                    {"order": 0, "goal": "first real thing", "task_node_id": task_a},
                    {"order": 1, "goal": "second real thing", "task_node_id": task_b},
                ],
                provenance="system_pending_review", scope_type="global",
                created_by=PREFIX,
            )
            procedure = await get_procedure(pool, captured["id"])
            assert procedure is not None

            nodes = await expand_procedure_steps(
                pool, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], steps=procedure["steps"],
            )
            compiled = compile_plan(
                procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"],
                procedure_row_id=procedure["id"],
                procedure_payload=procedure,
                task_description=f"{PREFIX} disagreeing task {uuid4()}",
                nodes=nodes,
                extractor_version=f"{PREFIX}@1",
                created_by=PREFIX,
            )
            bound = await bind_plan_implementations(pool, compiled, scope=scope)
            assert bound.graph.nodes[0].implementation_id == impl_a["id"]
            assert bound.graph.nodes[1].implementation_id == impl_b["id"]

            # Two real, DIFFERENT bound ids -> honest None, never a guess.
            assert plan_implementation_id(bound) is None

            persisted, _ = await persist_compiled_plan(pool, bound)
            execution_id = await record_plan_execution(
                pool, compiled=persisted, outcome="success", created_by=PREFIX,
                implementation_id=plan_implementation_id(persisted),
            )
            exec_row = await pool.fetchrow(
                "SELECT implementation_id FROM executions WHERE id = $1", execution_id,
            )
            assert exec_row["implementation_id"] is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
