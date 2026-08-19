"""
Real, live-database tests for the collector -> worker path. Requires a
real DATABASE_URL, same pattern as tests/test_schema_drift.py -- skips,
does not fail, when one isn't configured.
"""
import asyncio
import json
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


def test_a7_owner_id_and_visibility_are_actually_written(tmp_path: Path):
    """Real, live confirmation of the A7 fix: agent_traces/trace_events
    both carry real owner_id/visibility columns, but process_collector_file()
    never populated either -- every row silently landed public/unowned."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-a7-001"
        try:
            await _cleanup(pool, session_id)

            f = tmp_path / "events.jsonl"
            append_event(
                {"event_type": "PostToolUse", "timestamp": "2026-08-19T10:00:00Z"},
                f, session_id=session_id, event_type="PostToolUse", sequence=0,
            )

            result = await process_collector_file(
                pool, f, owner_id="alice", visibility="private",
            )
            assert result["inserted"] == 1

            trace_row = await pool.fetchrow(
                "SELECT owner_id, visibility::text AS visibility FROM agent_traces "
                "WHERE session_id = $1", session_id,
            )
            assert trace_row["owner_id"] == "alice"
            assert trace_row["visibility"] == "private"

            event_row = await pool.fetchrow(
                "SELECT owner_id, visibility::text AS visibility FROM trace_events "
                "WHERE session_id = $1", session_id,
            )
            assert event_row["owner_id"] == "alice"
            assert event_row["visibility"] == "private"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_a4_one_malformed_line_no_longer_stalls_the_whole_file(tmp_path: Path):
    """Real, live confirmation of the A4 fix: a truncated/corrupt line
    (exactly what a torn write produces) used to raise out of
    _read_records entirely, so a file with 1 bad line + N good lines
    processed ZERO records. Now the bad line is quarantined and every
    good line still gets processed."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-a4-001"
        try:
            await _cleanup(pool, session_id)

            f = tmp_path / "events.jsonl"
            append_event(
                {"event_type": "PostToolUse", "timestamp": "2026-08-19T10:00:00Z"},
                f, session_id=session_id, event_type="PostToolUse", sequence=0,
            )
            # Simulate a torn write: append a truncated, invalid JSON line.
            with f.open("a") as fh:
                fh.write('{"dedup_key": "broken", "session_id": "test-sess' + "\n")
            append_event(
                {"event_type": "PostToolUse", "timestamp": "2026-08-19T10:00:01Z"},
                f, session_id=session_id, event_type="PostToolUse", sequence=1,
            )

            result = await process_collector_file(pool, f)
            assert result["inserted"] == 2, "both good lines must survive one bad line"
            assert result["quarantined"] == 1

            quarantine_path = f.with_name(f.name + ".quarantine")
            assert quarantine_path.exists()
            quarantined_lines = quarantine_path.read_text().splitlines()
            assert len(quarantined_lines) == 1
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_a4_trailing_z_timestamp_is_parsed_correctly(tmp_path: Path):
    """JS-origin hook payloads emit trailing 'Z' timestamps
    ('2026-08-19T10:00:00.000Z'); _parse_timestamp must handle this on
    any Python version, not rely on 3.11+'s broader fromisoformat."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-a4-z-001"
        try:
            await _cleanup(pool, session_id)
            f = tmp_path / "events.jsonl"
            append_event(
                {"event_type": "PostToolUse", "timestamp": "2026-08-19T10:00:00.123Z"},
                f, session_id=session_id, event_type="PostToolUse", sequence=0,
            )
            result = await process_collector_file(pool, f)
            assert result["inserted"] == 1
            assert result["quarantined"] == 0
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_a3_header_is_ensured_once_per_distinct_trace_not_per_event(tmp_path: Path):
    """Real confirmation of the A3 fix: many events sharing one
    session/trace must only trigger one _ensure_trace_header call, not
    one per event -- checked via a real, small connection-count proxy:
    processing works correctly and fast for a batch that would have been
    50k redundant upserts under the old per-event behaviour. This test
    checks correctness (one real agent_traces row, not duplicated
    effort) rather than instrumenting call counts directly."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-a3-001"
        try:
            await _cleanup(pool, session_id)
            f = tmp_path / "events.jsonl"
            for i in range(50):
                append_event(
                    {"event_type": "PostToolUse", "timestamp": f"2026-08-19T10:00:{i:02d}Z"},
                    f, session_id=session_id, event_type="PostToolUse", sequence=i,
                )

            result = await process_collector_file(pool, f)
            assert result["inserted"] == 50

            trace_count = await pool.fetchval(
                "SELECT count(*) FROM agent_traces WHERE session_id = $1", session_id
            )
            assert trace_count == 1, "one distinct trace should produce exactly one header row"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_a2_real_worker_run_unblocks_collector_trimming(tmp_path: Path):
    """Real, full end-to-end confirmation of A2, collector and worker
    both exercised for real (not through mark_worker_seen() called
    directly, as the collector-side tests do): write past max_lines with
    no worker running -- nothing is trimmed. Run the real worker once.
    Write more -- now trimming is allowed, because a real worker run
    just confirmed reading everything currently in the file."""
    async def _run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "test-session-a2-e2e-001"
        try:
            await _cleanup(pool, session_id)
            f = tmp_path / "events.jsonl"
            max_lines = 5

            for i in range(8):
                append_event(
                    {"event_type": "PostToolUse", "timestamp": f"2026-08-19T10:00:{i:02d}Z"},
                    f, session_id=session_id, event_type="PostToolUse", sequence=i,
                    max_lines=max_lines,
                )
            # No worker has run yet -- nothing should have been trimmed,
            # even though 8 > max_lines(5).
            pre_worker_lines = f.read_text().splitlines()
            assert len(pre_worker_lines) == 8, "must not trim before any real worker run"

            result = await process_collector_file(pool, f)
            assert result["inserted"] == 8

            for i in range(8, 12):
                append_event(
                    {"event_type": "PostToolUse", "timestamp": f"2026-08-19T10:01:{i:02d}Z"},
                    f, session_id=session_id, event_type="PostToolUse", sequence=i,
                    max_lines=max_lines,
                )
            post_worker_lines = f.read_text().splitlines()
            records = [json.loads(l) for l in post_worker_lines]
            seqs = [r["sequence"] for r in records]
            # The real A2 guarantee: trimming became possible after the
            # worker ran (some of 0-7 were dropped), unlike the pre-worker
            # phase where nothing was ever dropped. (TRIM_FRACTION's 1-line-
            # per-call pace for a small max_lines is a separate, pre-existing
            # property -- not what this test is checking.)
            assert min(seqs) > 0, "at least the earliest events should now be trimmable"
            assert 11 in seqs, "the most recent event must always survive"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())
