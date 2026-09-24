from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from app.db.session import create_pool
from app.services import product_model as pm
from app.services.access import AccessScope
from app.services.goals import normalize_goal_name, search_goals
from app.services.search_projection import project_object


DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)


async def _insert_goal(pool, name, owner, resolved_at):
    goal_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO goals (id, canonical_name, normalized_name, status, provenance, "
        "owner_id, visibility, scope_type, scope_entity_id, resolved_at) "
        "VALUES ($1,$2,$3,'active','system_pending_review',$4,'private','user',$4,$5)",
        goal_id,
        name,
        normalize_goal_name(name),
        owner,
        resolved_at,
    )
    return goal_id


async def _project(pool, goal_ids):
    async with pool.acquire() as conn:
        async with conn.transaction():
            for goal_id in goal_ids:
                await project_object(conn, "goal", goal_id)


async def _cleanup(pool, goal_ids):
    await pool.execute("DELETE FROM goal_search_index WHERE goal_id = ANY($1::uuid[])", goal_ids)
    await pool.execute("DELETE FROM goals WHERE id = ANY($1::uuid[])", goal_ids)


def test_live_goal_list_find_and_search_support_resolution_filters_and_pagination():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        owner = f"goal-resolution-read-{uuid.uuid4().hex[:10]}"
        token = f"zzsharedtoken{uuid.uuid4().hex[:8]}"
        goal_ids = []
        try:
            datetime_resolved = datetime.now(timezone.utc)
            resolved_id = await _insert_goal(
                pool, f"{token} resolved", owner, datetime_resolved,
            )
            unresolved_a = await _insert_goal(pool, f"{token} unresolved-a", owner, None)
            unresolved_b = await _insert_goal(pool, f"{token} unresolved-b", owner, None)
            goal_ids = [resolved_id, unresolved_a, unresolved_b]
            await _project(pool, goal_ids)
            scope = AccessScope.for_user(owner)

            all_goals, all_has_more = await pm.list_goals(
                pool, scope=scope, status="active", limit=10,
            )
            resolved_goals, resolved_has_more = await pm.list_goals(
                pool, scope=scope, status="active", resolved=True, limit=10,
            )
            unresolved_goals, unresolved_has_more = await pm.list_goals(
                pool, scope=scope, status="active", resolved=False, limit=10,
            )
            page, page_has_more = await pm.list_goals(
                pool, scope=scope, status="active", limit=1, offset=1,
            )

            assert {goal["id"] for goal in all_goals} == set(goal_ids)
            assert all_has_more is False
            assert [goal["id"] for goal in resolved_goals] == [resolved_id]
            assert resolved_has_more is False
            assert {goal["id"] for goal in unresolved_goals} == {unresolved_a, unresolved_b}
            assert unresolved_has_more is False
            assert len(page) == 1
            assert page_has_more is True
            assert all("embedding" not in goal for goal in all_goals)
            assert next(goal for goal in all_goals if goal["id"] == resolved_id)["resolved_at"] == datetime_resolved.isoformat()

            found_all, found_all_more = await pm.find_goal(
                pool, token, scope=scope, limit=10,
            )
            found_resolved, found_resolved_more = await pm.find_goal(
                pool, token, scope=scope, resolved="resolved", limit=10,
            )
            found_unresolved, found_unresolved_more = await pm.find_goal(
                pool, token, scope=scope, resolved="unresolved", limit=1, offset=1,
            )
            assert {goal["id"] for goal in found_all} == set(goal_ids)
            assert found_all_more is False
            assert [goal["id"] for goal in found_resolved] == [resolved_id]
            assert found_resolved_more is False
            assert len(found_unresolved) == 1
            assert found_unresolved_more is True
            assert all("embedding" not in goal for goal in found_all)

            search_all = await search_goals(pool, query_text=token, scope=scope, limit=10)
            search_resolved = await search_goals(
                pool, query_text=token, scope=scope, resolved="resolved", limit=10,
            )
            search_unresolved = await search_goals(
                pool, query_text=token, scope=scope, resolved="unresolved", limit=10,
            )
            assert {goal["id"] for goal in search_all} == set(goal_ids)
            assert [goal["id"] for goal in search_resolved] == [resolved_id]
            assert {goal["id"] for goal in search_unresolved} == {unresolved_a, unresolved_b}
            assert all("embedding" not in goal for goal in search_all)
        finally:
            if goal_ids:
                await _cleanup(pool, goal_ids)
            await pool.close()

    asyncio.run(_run())
