"""
Real, live-database tests for ingestion_jobs.py -- the consumer for
ingestion_jobs rows that trace_worker.py's process_collector_file()
writes and, before this module existed, nothing ever read. Same pattern
as test_observations_e2e.py / test_trace_ingestion_e2e.py: requires a
real DATABASE_URL, skips (not fails) without one.

The point of this file: prove the end-to-end link trace_events ->
ingestion_jobs -> observations actually closes, not just that each piece
works in isolation (each piece already had its own tests before this).
"""
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.ingestion_jobs import (
    claim_jobs,
    process_pending_jobs,
    requeue_stuck_jobs,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

SESSION_ID = "ingjob-test-session-001"
TRACE_ID = "ingjob-test-trace-001"


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM ingestion_jobs WHERE payload::text LIKE $1",
            f"%{SESSION_ID}%",
        )
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", SESSION_ID,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", SESSION_ID)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", SESSION_ID)


async def _seed_event(pool: asyncpg.Pool, *, dedup_key: str, tool_name: str, tool_input: dict) -> str:
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
        "VALUES ($1, $2, now(), '1') ON CONFLICT (trace_id) DO NOTHING",
        TRACE_ID, SESSION_ID,
    )
    event_id = await pool.fetchval(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
        "\"timestamp\", tool_name, tool_input, dedup_key, schema_version) "
        "VALUES ($1,$2,0,'PostToolUse',now(),$3,$4,$5,'1') "
        "RETURNING id",
        TRACE_ID, SESSION_ID, tool_name, tool_input, dedup_key,
    )
    job_id = await pool.fetchval(
        "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2) RETURNING id",
        "normalize_trace_event",
        __import__("json").dumps({"trace_event_id": str(event_id), "dedup_key": dedup_key}),
    )
    return str(event_id), job_id


def test_normalize_trace_event_job_produces_a_real_observation():
    """
    The core claim of step 1: a queued normalize_trace_event job, once
    processed, turns a real trace_events row (an Edit tool call) into a
    real file_touched observation -- the link the handoff didn't
    mention was missing.
    """
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            event_id, job_id = await _seed_event(
                pool, dedup_key="ingjob-test-dedup-1", tool_name="Edit",
                tool_input={"file_path": "ingjob_test_file.py"},
            )

            result = await process_pending_jobs(pool, limit=10)
            assert result["claimed"] == 1
            assert result["done"] == 1
            assert result["failed"] == 0

            job_status = await pool.fetchval(
                "SELECT status FROM ingestion_jobs WHERE id = $1", job_id,
            )
            assert job_status == "done"

            obs = await pool.fetchrow(
                "SELECT o.observation_type, o.properties->>'file_path' AS file_path "
                "FROM observations o "
                "JOIN observation_events oe ON oe.observation_id = o.id "
                "WHERE oe.event_id = $1",
                event_id,
            )
            assert obs is not None
            assert obs["observation_type"] == "file_touched"
            assert obs["file_path"] == "ingjob_test_file.py"
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_unknown_job_type_is_marked_failed_not_stuck():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            job_id = await pool.fetchval(
                "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2) RETURNING id",
                "some_future_job_type", '{"marker": "' + SESSION_ID + '"}',
            )

            result = await process_pending_jobs(pool, limit=10)
            assert result["unknown_type"] == 1

            row = await pool.fetchrow(
                "SELECT status, last_error FROM ingestion_jobs WHERE id = $1", job_id,
            )
            assert row["status"] == "failed"
            assert "some_future_job_type" in row["last_error"]
        finally:
            await pool.execute("DELETE FROM ingestion_jobs WHERE payload::text LIKE $1", f"%{SESSION_ID}%")
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_a_bad_event_id_does_not_stall_the_batch():
    """
    A4's lesson, applied at this layer: one job pointing at a trace_event
    that no longer exists must not stop other real jobs in the same
    batch from being processed.
    """
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            # A real, processable job.
            _, good_job_id = await _seed_event(
                pool, dedup_key="ingjob-test-dedup-2", tool_name="Edit",
                tool_input={"file_path": "ingjob_test_other.py"},
            )
            # A job pointing at a trace_event id that does not exist.
            import uuid
            bad_job_id = await pool.fetchval(
                "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2) RETURNING id",
                "normalize_trace_event",
                __import__("json").dumps({"trace_event_id": str(uuid.uuid4()), "dedup_key": "nonexistent"}),
            )

            result = await process_pending_jobs(pool, limit=10)
            assert result["claimed"] == 2
            assert result["done"] == 2  # missing row is a no-op, not a failure

            statuses = {
                good_job_id: None, bad_job_id: None,
            }
            for jid in statuses:
                statuses[jid] = await pool.fetchval(
                    "SELECT status FROM ingestion_jobs WHERE id = $1", jid,
                )
            assert statuses[good_job_id] == "done"
            assert statuses[bad_job_id] == "done"
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_requeue_stuck_jobs_only_touches_old_processing_rows():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            job_id = await pool.fetchval(
                "INSERT INTO ingestion_jobs (job_type, payload, status, claimed_at) "
                "VALUES ($1, $2, 'processing', now() - interval '1 hour') RETURNING id",
                "normalize_trace_event", '{"marker": "' + SESSION_ID + '"}',
            )
            recent_job_id = await pool.fetchval(
                "INSERT INTO ingestion_jobs (job_type, payload, status, claimed_at) "
                "VALUES ($1, $2, 'processing', now()) RETURNING id",
                "normalize_trace_event", '{"marker": "' + SESSION_ID + '"}',
            )

            n = await requeue_stuck_jobs(pool, older_than_minutes=30)
            assert n == 1

            old_status = await pool.fetchval("SELECT status FROM ingestion_jobs WHERE id = $1", job_id)
            recent_status = await pool.fetchval("SELECT status FROM ingestion_jobs WHERE id = $1", recent_job_id)
            assert old_status == "pending"
            assert recent_status == "processing"
        finally:
            await pool.execute("DELETE FROM ingestion_jobs WHERE payload::text LIKE $1", f"%{SESSION_ID}%")
            await pool.close()

    import asyncio
    asyncio.run(_run())
