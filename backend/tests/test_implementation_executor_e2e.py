"""
Live-database proving tests for app/execution/implementation_executor.py.
Requires a real DATABASE_URL, skips (not fails) without one -- same
pattern as test_implementation_registry_e2e.py.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.execution.graph_executor import NodeResult
from app.execution.implementation_executor import (
    bind_implementation,
    execute_implementation,
    resolve_implementation_for_node,
)
from app.execution.implementation_registry import activate, register
from app.models.plan import PlanNode
from app.services.access import AccessScope

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "impl-exec-e2e"


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
        name, f"skill_{name}",
    )
    return str(row["id"])


def _node(order: int = 0, goal: str = "do it", **kw) -> PlanNode:
    return PlanNode(order=order, goal=goal, **kw)


# ---------------------------------------------------------------------
# (a) directive Sec 90 -- multiple implementations, resolve narrows to one
# ---------------------------------------------------------------------


def test_multiple_implementations_resolve_and_bind_exactly_one():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_id = await _task_node(pool, f"{PREFIX}-multi-task")

            tool_impl = await register(
                pool, name=f"{PREFIX}-tool", kind="tool", provider="graphify",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            det_impl = await register(
                pool, name=f"{PREFIX}-det", kind="deterministic", provider="local",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            frontier_impl = await register(
                pool, name=f"{PREFIX}-frontier", kind="frontier", provider="local",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            for impl in (tool_impl, det_impl, frontier_impl):
                await activate(pool, impl["id"])

            node = _node(implementation_hint=("deterministic", "tool", "frontier"))
            resolved = await resolve_implementation_for_node(
                pool, node, task_id, scope=scope,
            )
            assert resolved is not None
            assert resolved["id"] == det_impl["id"], "preference order must be honored"

            bound = bind_implementation(node, resolved)
            assert bound.implementation_id == det_impl["id"]
            assert bound.implementation_id not in (tool_impl["id"], frontier_impl["id"])
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# (b) directive Sec 91 -- implementation change / replay does not
#     silently upgrade an already-frozen node
# ---------------------------------------------------------------------


def test_frozen_node_does_not_silently_upgrade_when_a_new_version_is_registered():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_id = await _task_node(pool, f"{PREFIX}-replay-task")

            v1 = await register(
                pool, name=f"{PREFIX}-replay-impl", kind="tool", provider="graphify",
                created_by=PREFIX, version=1, task_node_ids=[task_id],
            )
            await activate(pool, v1["id"])

            node = _node()
            resolved_v1 = await resolve_implementation_for_node(pool, node, task_id, scope=scope)
            assert resolved_v1["id"] == v1["id"]
            frozen_node = bind_implementation(node, resolved_v1)
            assert frozen_node.implementation_id == v1["id"]

            # A new version is registered and activated for the SAME
            # (name, provider) identity AFTER the node above was already frozen.
            v2 = await register(
                pool, name=f"{PREFIX}-replay-impl", kind="tool", provider="graphify",
                created_by=PREFIX, version=2, task_node_ids=[task_id],
            )
            await activate(pool, v2["id"])
            assert v2["id"] != v1["id"]

            # The already-frozen node's own implementation_id, looked up
            # again via implementation_registry.get(), must still resolve
            # to v1's row -- replay must not silently upgrade.
            from app.execution.implementation_registry import get as get_implementation

            still_v1 = await get_implementation(pool, frozen_node.implementation_id, scope=scope)
            assert still_v1 is not None
            assert still_v1["id"] == v1["id"]
            assert still_v1["version"] == 1
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# (c) directive Sec 88 -- frontier still works with no hint/id at all
# ---------------------------------------------------------------------


def test_no_hint_no_id_node_still_executes_through_real_frontier_path():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            node = _node(order=3, goal="a step that needs a real repo")
            context = {
                "task_description": "some task",
                "repo_path": "C:/definitely/not/a/real/directory/xyz123",
                "model": "irrelevant-model", "max_steps": 1, "node_notes": [],
            }
            assert node.implementation_id is None
            assert node.implementation_hint is None

            result = await execute_implementation(
                pool, node, context, scope=AccessScope.unrestricted(),
            )
            assert isinstance(result, NodeResult)
            assert result.status == "failure"
            assert "REFUSED" in result.notes
        finally:
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# (d) directive Sec 89 -- deterministic works through the registry
# ---------------------------------------------------------------------


def test_deterministic_implementation_runs_a_real_sandboxed_script_through_the_registry():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            task_id = await _task_node(pool, f"{PREFIX}-det-task")

            det_impl = await register(
                pool, name=f"{PREFIX}-det-run", kind="deterministic", provider="local",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            await activate(pool, det_impl["id"])

            node = _node(implementation_hint=("deterministic",))
            resolved = await resolve_implementation_for_node(pool, node, task_id, scope=scope)
            assert resolved is not None and resolved["id"] == det_impl["id"]
            bound = bind_implementation(node, resolved)

            context = {"code": "open('e2e_out.txt', 'w').write('real deterministic run')"}
            result = await execute_implementation(pool, bound, context, scope=scope)

            assert result.status == "success"
            assert result.data["exit_code"] == 0
            assert result.data["output_files"] == {"e2e_out.txt": b"real deterministic run"}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
