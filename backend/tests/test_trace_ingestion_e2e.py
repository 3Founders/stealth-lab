"""
Real, live-database tests for the collector -> worker path. Requires a
real DATABASE_URL, same pattern as tests/test_schema_drift.py -- skips,
does not fail, when one isn't configured.
"""
import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from app.services.trace_collector import append_event
from app.services.trace_worker import process_collector_file

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool: asyncpg.Pool, session_id: str) -> None:
    async with pool.acquire() as conn:
        # ingestion_jobs first -- it references trace_events.id via the
        # payload JSON, not a real FK, so nothing enforces this order at
        # the database level, but deleting trace_events first would leave
        # orphaned job rows behind with no way to find them by session_id
        # afterward (this exact bug: an earlier version of this cleanup
        # skipped this table entirely, and leftover job rows from prior
        # runs accumulated and made this test fail on a second run).
        await conn.execute(
            "DELETE FROM ingestion_jobs WHERE payload->>'dedup_key' IN "
            "(SELECT dedup_key FROM trace_events WHERE session_id = $1)", session_id,
        )
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", session_id)


def test_collector_then_worker_real_end_to_end(tmp_path: Path):
    """The real, full path: append via the collector, process via the
    worker, confirm real rows exist in the live database with the
    correct content -- not just that the functions run without error."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-e2e-001"
        try:
            await _cleanup(pool, session_id)

            f = tmp_path / "events.jsonl"
            append_event(
                {"event_type": "PostToolUse", "tool_name": "Read",
                 "tool_output": {"content": "print('hi')"}},
                f, session_id=session_id, event_type="PostToolUse", sequence=0,
            )
            append_event(
                {"event_type": "PostToolUse", "tool_name": "Bash",
                 "tool_output": {"stdout": "AKIAIOSFODNN7EXAMPLE"}},
                f, session_id=session_id, event_type="PostToolUse", sequence=1,
            )

            result = await process_collector_file(pool, f)
            assert result["records_seen"] == 2
            assert result["inserted"] == 2
            assert result["skipped_duplicate"] == 0

            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT sequence, tool_name, tool_output FROM trace_events "
                    "WHERE session_id = $1 ORDER BY sequence", session_id,
                )
                assert len(rows) == 2
                assert rows[0]["tool_name"] == "Read"
                # Real, important check: the raw secret must never have
                # reached the database at all -- redaction ran before
                # persistence, confirmed here against the actual stored row.
                assert "AKIAIOSFODNN7EXAMPLE" not in str(rows[1]["tool_output"])
                assert "[REDACTED:aws_access_key]" in str(rows[1]["tool_output"])

                trace_row = await conn.fetchrow(
                    "SELECT trace_id, session_id FROM agent_traces WHERE session_id = $1",
                    session_id,
                )
                assert trace_row is not None

                job_rows = await conn.fetch(
                    "SELECT job_type, status FROM ingestion_jobs WHERE payload->>'dedup_key' IN "
                    "(SELECT dedup_key FROM trace_events WHERE session_id = $1)", session_id,
                )
                assert len(job_rows) == 2
                assert all(j["status"] == "pending" for j in job_rows)
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_reprocessing_the_same_file_is_a_true_noop(tmp_path: Path):
    """Real proof of the idempotency design: processing the exact same
    file twice must not create duplicate rows or duplicate jobs."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-idempotent-002"
        try:
            await _cleanup(pool, session_id)

            f = tmp_path / "events.jsonl"
            append_event(
                {"event_type": "PostToolUse", "n": 1},
                f, session_id=session_id, event_type="PostToolUse", sequence=0,
            )

            first = await process_collector_file(pool, f)
            assert first["inserted"] == 1

            second = await process_collector_file(pool, f)
            assert second["inserted"] == 0
            assert second["skipped_duplicate"] == 1

            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT count(*) FROM trace_events WHERE session_id = $1", session_id
                )
                assert count == 1, "real re-run must not create a duplicate row"
                job_count = await conn.fetchval(
                    "SELECT count(*) FROM ingestion_jobs ij "
                    "JOIN trace_events te ON ij.payload->>'trace_event_id' = te.id::text "
                    "WHERE te.session_id = $1", session_id,
                )
                assert job_count == 1, "real re-run must not create a duplicate job"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_multiple_events_share_one_trace_header(tmp_path: Path):
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-header-003"
        try:
            await _cleanup(pool, session_id)
            f = tmp_path / "events.jsonl"
            for i in range(3):
                append_event(
                    {"event_type": "PostToolUse", "n": i},
                    f, session_id=session_id, event_type="PostToolUse", sequence=i,
                )
            await process_collector_file(pool, f)

            async with pool.acquire() as conn:
                header_count = await conn.fetchval(
                    "SELECT count(*) FROM agent_traces WHERE session_id = $1", session_id
                )
                assert header_count == 1, "3 events in the same session should share ONE trace header"
                event_count = await conn.fetchval(
                    "SELECT count(*) FROM trace_events WHERE session_id = $1", session_id
                )
                assert event_count == 3
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())
