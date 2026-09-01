"""
Live-database proving test for app/execution/implementation_registry.py
+ db/33_implementation_registry.sql. Requires a real DATABASE_URL, skips
(not fails) without one -- same pattern as every other *_e2e.py file.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.execution.implementation_registry import (
    activate,
    deprecate,
    disable,
    get,
    get_for_task,
    list_implementations,
    quarantine,
    register,
    resolve,
    verify,
)
from app.services.access import AccessScope

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "impl-registry-e2e"


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


def test_register_get_list_lifecycle_transitions_round_trip():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()

            row = await register(
                pool, name=f"{PREFIX}-graphify-query-graph", kind="tool", provider="graphify",
                created_by=PREFIX, description="real live proving row",
                locator={"protocol": "mcp", "server": "graphify", "tool": "query_graph"},
                requirements={"network": True, "credentials": ["graphify"]},
            )
            assert row["status"] == "candidate"
            assert row["verification_status"] == "unverified"

            fetched = await get(pool, row["id"], scope=scope)
            assert fetched is not None
            assert fetched["name"] == f"{PREFIX}-graphify-query-graph"
            assert fetched["locator"]["tool"] == "query_graph"

            listed = await list_implementations(pool, scope=scope, provider="graphify")
            assert any(r["id"] == row["id"] for r in listed)

            activated = await activate(pool, row["id"])
            assert activated["status"] == "active"

            verified = await verify(pool, row["id"])
            assert verified["verification_status"] == "verified"

            deprecated = await deprecate(pool, row["id"])
            assert deprecated["status"] == "deprecated"
            assert deprecated["deprecated_at"] is not None

            re_disabled = await disable(pool, row["id"])
            assert re_disabled["status"] == "disabled"
            assert re_disabled["disabled_at"] is not None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_identity_uniqueness_is_enforced_by_the_database():
    async def _run():
        import asyncpg

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            await register(
                pool, name=f"{PREFIX}-dup", kind="tool", provider="p", created_by=PREFIX, version=1,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await register(
                    pool, name=f"{PREFIX}-dup", kind="tool", provider="p", created_by=PREFIX, version=1,
                )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_for_task_and_resolve_use_a_real_task_node_link():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()

            task_row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
                f"{PREFIX}-understand-repo", f"skill_{PREFIX}",
            )
            task_id = str(task_row["id"])

            tool_impl = await register(
                pool, name=f"{PREFIX}-tool-impl", kind="tool", provider="graphify",
                created_by=PREFIX, task_node_ids=[task_id],
            )
            frontier_impl = await register(
                pool, name=f"{PREFIX}-frontier-impl", kind="frontier", provider="local",
                created_by=PREFIX, task_node_ids=[task_id],
            )

            # Neither is active yet -- get_for_task's default status='active'
            # filter must exclude both real, linked, still-candidate rows.
            none_active = await get_for_task(pool, task_id, scope=scope)
            assert none_active == []

            all_linked = await get_for_task(pool, task_id, scope=scope, status=None)
            assert {r["id"] for r in all_linked} == {tool_impl["id"], frontier_impl["id"]}

            await activate(pool, tool_impl["id"])
            await activate(pool, frontier_impl["id"])

            active_now = await get_for_task(pool, task_id, scope=scope)
            assert {r["id"] for r in active_now} == {tool_impl["id"], frontier_impl["id"]}

            resolved = await resolve(pool, task_id, scope=scope, hint_kinds=("frontier", "tool"))
            assert resolved["id"] == frontier_impl["id"], "hint preference order must be honored"

            resolved_fallback = await resolve(pool, task_id, scope=scope, hint_kinds=("slm", "tool"))
            assert resolved_fallback["id"] == tool_impl["id"], "first hinted kind with a real match wins"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_quarantine_and_visibility_filtering():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            private_impl = await register(
                pool, name=f"{PREFIX}-private", kind="tool", provider="p", created_by=PREFIX,
                visibility="private", owner_id="owner-a",
            )
            quarantined = await quarantine(pool, private_impl["id"])
            assert quarantined["status"] == "quarantined"
            assert quarantined["disabled_at"] is not None

            anon_scope = AccessScope.anonymous()
            assert await get(pool, private_impl["id"], scope=anon_scope) is None, (
                "a private implementation must be invisible to an anonymous viewer -- "
                "visibility_predicate() applied, not bypassed"
            )

            owner_scope = AccessScope.for_user("owner-a")
            visible_to_owner = await get(pool, private_impl["id"], scope=owner_scope)
            assert visible_to_owner is not None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
