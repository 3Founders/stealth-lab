from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.goals import normalize_goal_name
from app.services.procedures import capture_procedure, record_execution_outcome
from app.services.product_model import associate_solution

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)


async def _capture(pool, prefix, owner):
    return await capture_procedure(
        pool,
        name=f"{prefix}-procedure",
        goal=f"complete {prefix} outcome reliably",
        steps=[{"order": 0, "goal": f"complete {prefix} outcome"}],
        provenance="system_pending_review",
        scope_type="user",
        scope_entity_id=owner,
        visibility="private",
        owner_id=owner,
        created_by="goal-resolution-e2e",
        judge_mode="none",
    )


async def _insert_goal(pool, name, owner, *, resolved_at=None):
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


async def _cleanup(pool, prefix, goal_ids):
    await pool.execute("DELETE FROM solutions WHERE goal_id = ANY($1::uuid[])", goal_ids)
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM goal_search_index WHERE goal_id = ANY($1::uuid[])", goal_ids)
    await pool.execute("DELETE FROM goals WHERE id = ANY($1::uuid[])", goal_ids)


async def _record_successes(pool, procedure_row_id, count, start=0):
    result = None
    for index in range(start, start + count):
        result = await record_execution_outcome(
            pool,
            procedure_row_id=procedure_row_id,
            success=True,
            context_key=f"goal-resolution-context-{index}",
        )
    return result


def test_below_threshold_leaves_direct_goal_unresolved():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        prefix = f"goal-resolution-below-{uuid.uuid4().hex[:10]}"
        owner = prefix
        procedure = None
        goal_ids = []
        try:
            procedure = await _capture(pool, prefix, owner)
            direct_goal_id = str(
                await pool.fetchval(
                    "SELECT achieves_goal_id FROM procedures WHERE id = $1::uuid",
                    procedure["id"],
                )
            )
            goal_ids.append(direct_goal_id)
            result = await _record_successes(pool, procedure["id"], 9)
            assert result["verification_state"] == "candidate"
            assert await pool.fetchval(
                "SELECT resolved_at FROM goals WHERE id = $1::uuid", direct_goal_id
            ) is None
        finally:
            if goal_ids:
                await _cleanup(pool, prefix, goal_ids)
            await pool.close()

    asyncio.run(_run())


def test_exact_promotion_resolves_all_links_and_preserves_timestamps():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        prefix = f"goal-resolution-exact-{uuid.uuid4().hex[:10]}"
        owner = prefix
        goal_ids = []
        try:
            procedure = await _capture(pool, prefix, owner)
            direct_goal_id = str(
                await pool.fetchval(
                    "SELECT achieves_goal_id FROM procedures WHERE id = $1::uuid",
                    procedure["id"],
                )
            )
            solution_goal_ids = [
                await _insert_goal(pool, f"{prefix}-solution-a", owner),
                await _insert_goal(pool, f"{prefix}-solution-b", owner),
            ]
            goal_ids = [direct_goal_id, *solution_goal_ids]
            for goal_id in solution_goal_ids:
                await associate_solution(
                    pool,
                    goal_id=goal_id,
                    solution_type="procedure",
                    target_id=procedure["procedure_id"],
                    provenance="system_pending_review",
                    proposer="goal-resolution-e2e",
                )

            result = await _record_successes(pool, procedure["id"], 10)
            assert result["verification_state"] == "verified"
            first = {
                row["id"]: row["resolved_at"]
                for row in await pool.fetch(
                    "SELECT id, resolved_at FROM goals WHERE id = ANY($1::uuid[])", goal_ids
                )
            }
            assert set(first) == set(goal_ids)
            assert all(value is not None for value in first.values())

            await record_execution_outcome(
                pool, procedure_row_id=procedure["id"], success=True, context_key="later-success",
            )
            await record_execution_outcome(
                pool,
                procedure_row_id=procedure["id"],
                success=False,
                context_key="later-failure",
                failure_class="external_failure",
            )
            after = {
                row["id"]: row["resolved_at"]
                for row in await pool.fetch(
                    "SELECT id, resolved_at FROM goals WHERE id = ANY($1::uuid[])", goal_ids
                )
            }
            assert after == first
        finally:
            if goal_ids:
                await _cleanup(pool, prefix, goal_ids)
            await pool.close()

    asyncio.run(_run())


def test_self_reported_outcomes_never_resolve_goal():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        prefix = f"goal-resolution-self-report-{uuid.uuid4().hex[:10]}"
        owner = prefix
        goal_ids = []
        try:
            procedure = await _capture(pool, prefix, owner)
            direct_goal_id = str(
                await pool.fetchval(
                    "SELECT achieves_goal_id FROM procedures WHERE id = $1::uuid",
                    procedure["id"],
                )
            )
            goal_ids.append(direct_goal_id)
            result = None
            for index in range(12):
                result = await record_execution_outcome(
                    pool,
                    procedure_row_id=procedure["id"],
                    success=True,
                    context_key=f"self-report-{index}",
                    execution_verified=False,
                )
            assert result["verification_state"] == "candidate"
            assert await pool.fetchval(
                "SELECT resolved_at FROM goals WHERE id = $1::uuid", direct_goal_id
            ) is None
        finally:
            if goal_ids:
                await _cleanup(pool, prefix, goal_ids)
            await pool.close()

    asyncio.run(_run())


def test_concurrent_final_verifications_preserve_first_resolution_timestamp():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=5)
        prefix = f"goal-resolution-concurrent-{uuid.uuid4().hex[:10]}"
        owner = prefix
        goal_ids = []
        try:
            procedure = await _capture(pool, prefix, owner)
            direct_goal_id = str(
                await pool.fetchval(
                    "SELECT achieves_goal_id FROM procedures WHERE id = $1::uuid",
                    procedure["id"],
                )
            )
            goal_ids.append(direct_goal_id)
            await _record_successes(pool, procedure["id"], 9)
            await asyncio.gather(
                record_execution_outcome(
                    pool,
                    procedure_row_id=procedure["id"],
                    success=True,
                    context_key="concurrent-a",
                ),
                record_execution_outcome(
                    pool,
                    procedure_row_id=procedure["id"],
                    success=True,
                    context_key="concurrent-b",
                ),
            )
            row = await pool.fetchrow(
                "SELECT verification_state FROM procedures WHERE id = $1::uuid", procedure["id"]
            )
            resolved_at = await pool.fetchval(
                "SELECT resolved_at FROM goals WHERE id = $1::uuid", direct_goal_id
            )
            assert row["verification_state"] == "verified"
            assert resolved_at is not None
            await asyncio.sleep(0.01)
            assert await pool.fetchval(
                "SELECT resolved_at FROM goals WHERE id = $1::uuid", direct_goal_id
            ) == resolved_at
        finally:
            if goal_ids:
                await _cleanup(pool, prefix, goal_ids)
            await pool.close()

    asyncio.run(_run())
