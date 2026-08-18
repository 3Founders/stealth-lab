"""
The worker half of the ingestion pipeline (ticket 16, memory-substrate
map). Reads what the collector appended to its local file and does the
actual, durable database write -- this is the boundary ticket 16 calls
out: "the durable step is the local append, and the job table exists to
track compilation work." Everything from here downstream (normalization,
episode assembly) is replayable, per spec.md's own requirement; only the
raw persistence below is treated as the one irreversible step.

Idempotent by design, not by tracking an offset: every event carries a
real dedup_key (computed by the collector), and every insert here uses
INSERT ... ON CONFLICT (dedup_key) DO NOTHING RETURNING id. Re-running
this against the same file (e.g. after a crash, or just because it's
simpler than maintaining a separate cursor) re-processes already-seen
lines harmlessly. Simpler and more robust than a hand-maintained offset
file that could itself drift or get corrupted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

SCHEMA_VERSION = "1"


def _read_records(file_path: Path) -> list[dict]:
    if not file_path.exists():
        return []
    lines = file_path.read_text().splitlines()
    if lines and lines[0].startswith('{"_drop_count"'):
        lines = lines[1:]
    return [json.loads(l) for l in lines if l.strip()]


async def _ensure_trace_header(conn: asyncpg.Connection, trace_id: str, session_id: str,
                                started_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (trace_id) DO NOTHING",
        trace_id, session_id, started_at, SCHEMA_VERSION,
    )


async def _insert_event(conn: asyncpg.Connection, record: dict) -> str | None:
    """Returns the real inserted trace_events.id, or None if this
    dedup_key was already present (a real, confirmed no-op, not assumed)."""
    event = record["event"]
    ts_raw = event.get("timestamp")
    timestamp = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(timezone.utc)

    return await conn.fetchval(
        """
        INSERT INTO trace_events (
            trace_id, session_id, sequence, event_type, "timestamp",
            actor_id, tool_name, tool_call_id, tool_input, tool_output,
            success, dedup_key, schema_version
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (dedup_key) DO NOTHING
        RETURNING id
        """,
        record.get("trace_id") or record["session_id"],
        record["session_id"],
        record["sequence"],
        record["event_type"],
        timestamp,
        event.get("actor_id"),
        event.get("tool_name"),
        event.get("tool_call_id"),
        json.dumps(event.get("tool_input")) if event.get("tool_input") is not None else None,
        json.dumps(event.get("tool_output")) if event.get("tool_output") is not None else None,
        event.get("success"),
        record["dedup_key"],
        SCHEMA_VERSION,
    )


async def process_collector_file(pool: asyncpg.Pool, file_path: Path) -> dict:
    """
    Real, testable entry point. Processes every record currently in the
    collector file: ensures a trace header exists, inserts the event
    (idempotently), and queues a downstream job for each real (not
    duplicate) insert. Returns real counts, not estimates.
    """
    records = _read_records(file_path)
    inserted = 0
    skipped_duplicate = 0

    for record in records:
        trace_id = record.get("trace_id") or record["session_id"]
        session_id = record["session_id"]
        event = record["event"]
        ts_raw = event.get("timestamp")
        started_at = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            async with conn.transaction():
                await _ensure_trace_header(conn, trace_id, session_id, started_at)
                new_id = await _insert_event(conn, record)
                if new_id is not None:
                    inserted += 1
                    await conn.execute(
                        "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
                        "normalize_trace_event",
                        json.dumps({"trace_event_id": str(new_id), "dedup_key": record["dedup_key"]}),
                    )
                else:
                    skipped_duplicate += 1

    return {"records_seen": len(records), "inserted": inserted, "skipped_duplicate": skipped_duplicate}
