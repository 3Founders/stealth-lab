"""
MCP hardening B4: execution_runs.status is already a real state machine
(migration 36's own comment: "pending -> running -> (succeeded | failed |
paused | cancelled)") -- what was missing was a single, DB-enforced
transition guard so an invalid edge (e.g. succeeded -> running, or
running -> pending) can never be taken regardless of which caller issues
the UPDATE. Migration 60 adds `trg_execution_runs_status_transition_fence`,
additive to the existing table (no new state-machine object).

Skips itself when DATABASE_URL is unset, self-cleaning by row id.
"""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

from app.db.session import create_pool  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402


async def _cleanup_procedure(pool, row_id) -> None:
    """`execution_plans` is frozen (Band 1.7) -- once a real plan row
    references this procedure, DELETE cannot remove it. Restore
    is_engineering_fixture=true on any such pinned survivor so it does
    not linger in the shared corpus as if it were real content (same
    hygiene bug found and fixed this session in
    test_find_best_way_plan_only_e2e.py -- fixed here from the start
    instead of accumulating the same debris)."""
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


async def _fresh_run_row(pool) -> str:
    """A minimal, real execution_runs row with valid FK targets -- reuses
    an existing task_graphs/execution_plans row's ids rather than
    duplicating _plan_chain from test_durable_run_e2e.py, since this test
    only needs a row to UPDATE, never a real drive."""
    res = await capture_procedure(
        pool, name=f"ert-e2e-{uuid.uuid4().hex[:8]}", goal="status transition fence probe",
        steps=[{"order": 0, "goal": "a"}],
        provenance="prior_library", scope_type="global", created_by="ert_e2e",
        embedding=[0.01] * 1024,
    )
    proc_id, row_id = res["procedure_id"], res["id"]
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "ert-e2e",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
        run_id = await c.fetchval(
            "INSERT INTO execution_runs (execution_plan_id, task_graph_id, procedure_id, "
            " procedure_version, status, created_by) "
            "VALUES ($1,$2,$3,$4,'pending','ert_e2e') RETURNING id",
            plan_id, graph_id, proc_id, pv,
        )
    return str(run_id), row_id


@pytest.mark.asyncio
async def test_valid_transitions_are_allowed_through_the_full_lifecycle():
    pool = await create_pool(statement_cache_size=0)
    try:
        run_id, row_id = await _fresh_run_row(pool)
        # pending -> running -> paused -> running -> succeeded
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        await pool.execute("UPDATE execution_runs SET status='paused' WHERE id=$1", run_id)
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        await pool.execute("UPDATE execution_runs SET status='succeeded' WHERE id=$1", run_id)
        status = await pool.fetchval("SELECT status FROM execution_runs WHERE id=$1", run_id)
        assert status == "succeeded"
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_running_to_failed_is_allowed():
    pool = await create_pool(statement_cache_size=0)
    try:
        run_id, row_id = await _fresh_run_row(pool)
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        await pool.execute("UPDATE execution_runs SET status='failed' WHERE id=$1", run_id)
        # failed -> running (retry/resume) is also a real, exercised edge.
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        status = await pool.fetchval("SELECT status FROM execution_runs WHERE id=$1", run_id)
        assert status == "running"
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_backwards_transition_is_rejected():
    pool = await create_pool(statement_cache_size=0)
    try:
        run_id, row_id = await _fresh_run_row(pool)
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        with pytest.raises(asyncpg.exceptions.CheckViolationError, match="invalid execution_runs status transition"):
            await pool.execute("UPDATE execution_runs SET status='pending' WHERE id=$1", run_id)
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_terminal_state_cannot_be_left():
    pool = await create_pool(statement_cache_size=0)
    try:
        run_id, row_id = await _fresh_run_row(pool)
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        await pool.execute("UPDATE execution_runs SET status='succeeded' WHERE id=$1", run_id)
        with pytest.raises(asyncpg.exceptions.CheckViolationError, match="invalid execution_runs status transition"):
            await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        with pytest.raises(asyncpg.exceptions.CheckViolationError, match="invalid execution_runs status transition"):
            await pool.execute("UPDATE execution_runs SET status='failed' WHERE id=$1", run_id)
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()


@pytest.mark.asyncio
async def test_touch_only_update_with_unchanged_status_is_unaffected():
    """The fence must only fire when status itself changes -- a lease
    renewal / worker_id touch on a 'running' row is the most common
    UPDATE this table sees and must never be blocked."""
    pool = await create_pool(statement_cache_size=0)
    try:
        run_id, row_id = await _fresh_run_row(pool)
        await pool.execute("UPDATE execution_runs SET status='running' WHERE id=$1", run_id)
        await pool.execute(
            "UPDATE execution_runs SET worker_id='w-2', lease_expires_at=now() WHERE id=$1", run_id,
        )
        status = await pool.fetchval("SELECT status FROM execution_runs WHERE id=$1", run_id)
        assert status == "running"
    finally:
        await pool.execute("DELETE FROM execution_runs WHERE id=$1", run_id)
        await _cleanup_procedure(pool, row_id)
        await pool.close()
