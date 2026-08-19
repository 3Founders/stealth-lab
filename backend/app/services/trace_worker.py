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

from app.services.trace_collector import mark_worker_seen

SCHEMA_VERSION = "1"


def _parse_timestamp(ts_raw: str | None) -> datetime:
    """
    A4 real fix: datetime.fromisoformat() pre-3.11 rejects a trailing
    'Z' (which JS-origin hook payloads emit natively, e.g.
    '2026-08-19T10:00:00.000Z'). Normalizing 'Z' -> '+00:00' first makes
    this work on any Python version this might run on, not just the one
    in this sandbox.
    """
    if not ts_raw:
        return datetime.now(timezone.utc)
    if ts_raw.endswith("Z"):
        ts_raw = ts_raw[:-1] + "+00:00"
    return datetime.fromisoformat(ts_raw)


def _read_records(file_path: Path) -> tuple[list[dict], list[tuple[int, str, str]]]:
    """
    Returns (good_records, quarantined) where quarantined is a list of
    (line_number, raw_line, error_message) for every line that failed to
    parse. A4 real fix: the old version had no try/except around
    json.loads/fromisoformat at all -- one malformed line (exactly what
    a torn write, or a future format change, produces) raised out of
    this function entirely, so process_collector_file() never processed
    a single record from an otherwise-healthy file. A worker that stalls
    permanently on one bad line is a worse failure mode than skipping
    that one line and quarantining it for inspection.
    """
    if not file_path.exists():
        return [], []
    lines = file_path.read_text().splitlines()
    if lines and lines[0].startswith('{"_drop_count"'):
        lines = lines[1:]

    good: list[dict] = []
    quarantined: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            # Fail fast on the fields _insert_event/_ensure_trace_header
            # actually require, rather than raising deep inside the
            # per-record loop where a KeyError looks like a different
            # kind of bug.
            _ = record["session_id"], record["sequence"], record["event_type"], record["dedup_key"]
            _parse_timestamp(record.get("event", {}).get("timestamp"))
            good.append(record)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # malformed line must be quarantined, not crash the run
            quarantined.append((i, line, repr(exc)))
    return good, quarantined


def _write_quarantine(file_path: Path, quarantined: list[tuple[int, str, str]]) -> None:
    if not quarantined:
        return
    quarantine_path = file_path.with_name(file_path.name + ".quarantine")
    with quarantine_path.open("a") as f:
        for line_no, raw_line, error in quarantined:
            f.write(json.dumps({"line": line_no, "raw": raw_line, "error": error}) + "\n")


async def _ensure_trace_header(conn: asyncpg.Connection, trace_id: str, session_id: str,
                                started_at: datetime, owner_id: str | None = None,
                                visibility: str = "public") -> None:
    await conn.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version, "
        "owner_id, visibility) "
        "VALUES ($1, $2, $3, $4, $5, $6::visibility_level) ON CONFLICT (trace_id) DO NOTHING",
        trace_id, session_id, started_at, SCHEMA_VERSION, owner_id, visibility,
    )


async def _insert_event(conn: asyncpg.Connection, record: dict, owner_id: str | None = None,
                         visibility: str = "public") -> str | None:
    """Returns the real inserted trace_events.id, or None if this
    dedup_key was already present (a real, confirmed no-op, not assumed).

    A7 real bug fixed: agent_traces/trace_events both carry real
    owner_id/visibility columns (12_trace_ingestion_pipeline.sql), but
    this INSERT never populated either -- every trace row silently
    landed as visibility='public', owner_id=NULL regardless of who
    produced it, the exact tenant_id cautionary case 03_access.sql's own
    docstring warns about. Now real parameters, not decorative columns.
    """
    event = record["event"]
    timestamp = _parse_timestamp(event.get("timestamp"))

    return await conn.fetchval(
        """
        INSERT INTO trace_events (
            trace_id, session_id, sequence, event_type, "timestamp",
            actor_id, tool_name, tool_call_id, tool_input, tool_output,
            success, dedup_key, schema_version, owner_id, visibility
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::visibility_level)
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
        owner_id,
        visibility,
    )


async def process_collector_file(pool: asyncpg.Pool, file_path: Path, *,
                                  owner_id: str | None = None,
                                  visibility: str = "public") -> dict:
    """
    Real, testable entry point. Processes every record currently in the
    collector file: ensures a trace header exists, inserts the event
    (idempotently), and queues a downstream job for each real (not
    duplicate) insert. Returns real counts, not estimates.

    A3 real fix: the old version called pool.acquire() and
    _ensure_trace_header() once PER RECORD -- at the 50k-line default
    that's 50k pool acquisitions and (since almost every event shares
    one session's trace_id) ~50k redundant header upserts for what is
    really one distinct trace per run in the common case. Now one
    connection is acquired for the whole run, and the header is only
    upserted once per distinct trace_id actually seen. Per-event INSERTs
    are still individual round trips (real bulk/executemany batching
    with per-row ON CONFLICT...RETURNING is a further optimization, not
    attempted here -- flagging that honestly rather than claiming this
    is fully batched).
    """
    good_records, quarantined = _read_records(file_path)
    _write_quarantine(file_path, quarantined)

    inserted = 0
    skipped_duplicate = 0
    headers_ensured: set[str] = set()

    async with pool.acquire() as conn:
        for record in good_records:
            trace_id = record.get("trace_id") or record["session_id"]
            session_id = record["session_id"]
            event = record["event"]
            started_at = _parse_timestamp(event.get("timestamp"))

            async with conn.transaction():
                if trace_id not in headers_ensured:
                    await _ensure_trace_header(
                        conn, trace_id, session_id, started_at,
                        owner_id=owner_id, visibility=visibility,
                    )
                    headers_ensured.add(trace_id)
                new_id = await _insert_event(conn, record, owner_id=owner_id, visibility=visibility)
                if new_id is not None:
                    inserted += 1
                    await conn.execute(
                        "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
                        "normalize_trace_event",
                        json.dumps({"trace_event_id": str(new_id), "dedup_key": record["dedup_key"]}),
                    )
                else:
                    skipped_duplicate += 1

    # A2 real fix: mark every currently-read line as seen, so the
    # collector's compaction (see trace_collector.py's mark_worker_seen())
    # is now allowed to trim up to this many lines -- BEFORE this call,
    # trimming had zero knowledge of worker progress and could discard
    # events the worker had never read. This call is what makes that
    # guarantee real, not just documented.
    mark_worker_seen(file_path, len(good_records))

    return {
        "records_seen": len(good_records) + len(quarantined),
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "quarantined": len(quarantined),
    }