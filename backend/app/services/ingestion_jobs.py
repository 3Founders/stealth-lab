"""
The job consumer half of the ingestion pipeline (ticket 16 completes
here). trace_worker.py's process_collector_file() already writes real
ingestion_jobs rows of type 'normalize_trace_event' for every real
(non-duplicate) trace_events insert -- that part was built. Nothing ever
read them: process_pending_jobs() below is the missing consumer, and
handle_normalize_trace_event() is the one handler currently registered.

Closes the second half of the gap independently confirmed by reading
source rather than assumed: extract_deterministic_observations()
(observations.py) and persist_observation() are both real, tested, pure/
near-pure functions with ZERO non-test callers before this module. This
file gives them a caller; it does not change their behavior.

SKIP LOCKED, not a status='processing' pre-scan: the standard Postgres
job-queue idiom, safe for the future multi-worker deployment
ingestion_jobs' own comment already anticipates ("SKIP LOCKED makes a
future multi-worker deployment safe without redesign, even though
milestone 1 runs exactly one in-process worker" -- 12_trace_ingestion_
pipeline.sql). One job = one transaction, so a crash mid-job leaves it
'processing' rather than lost -- see requeue_stuck_jobs() for the
recovery path, which is deliberately manual/explicit rather than a
silent timeout-based requeue (a job stuck because of a genuine bug
should not retry forever unattended).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import asyncpg

from app.services.observations import (
    extract_deterministic_observations,
    persist_observation,
)

log = logging.getLogger(__name__)

# job_type registry -- deliberately a plain dict, not a class hierarchy;
# ingestion_jobs.job_type is TEXT, uncomstrained, per that column's own
# comment ("episode assembly (ticket 11) will add its own"). A handler
# takes (pool, payload) and does its own transaction(s); this module
# does not wrap handlers in a transaction itself because a handler like
# this one needs the trace_events read and the observation write to be
# separately committable (persist_observation manages its own
# transaction already).
JobHandler = Any


async def handle_normalize_trace_event(pool: asyncpg.Pool, payload: dict) -> None:
    """
    The one handler wired today. Loads the real trace_events row the
    job's payload points at, runs it through extract_deterministic_
    observations() (pure function, observations.py, already tested
    standalone), and persists whatever it finds via persist_observation()
    (also already tested standalone -- this function is pure wiring,
    not new extraction logic).

    Model-based extraction (extract_model_observation) is deliberately
    NOT called here -- that's an LLM call per event, a different cost/
    latency class from this deterministic pass, and handoff item 1's own
    concern. A future 'extract_model_observation' job_type can be queued
    separately once that stub is filled in, without touching this
    handler.

    No-op, not an error, if the trace_events row is gone (deleted, or a
    stale job re-run after a real cleanup) -- nothing to extract from is
    a legitimate terminal state, not a failure.
    """
    trace_event_id = payload.get("trace_event_id")
    if not trace_event_id:
        raise ValueError(f"normalize_trace_event payload missing trace_event_id: {payload!r}")

    row = await pool.fetchrow(
        "SELECT id, event_type, tool_name, tool_input, tool_output, "
        "       owner_id, visibility::text AS visibility "
        "FROM trace_events WHERE id = $1",
        trace_event_id,
    )
    if row is None:
        log.info("normalize_trace_event: trace_event %s no longer exists, skipping", trace_event_id)
        return

    trace_event = dict(row)
    # tool_input comes back from asyncpg as a str (JSONB decoded to text
    # by default in this codebase's connection setup) or already a dict
    # depending on codec registration -- extract_deterministic_
    # observations() already handles both (observations.py:60-61), so no
    # decoding is duplicated here.
    observations = extract_deterministic_observations(trace_event)
    for obs in observations:
        await persist_observation(
            pool,
            observation_type=obs["observation_type"],
            label=obs["label"],
            extractor_kind="deterministic",
            event_ids=[str(trace_event_id)],
            properties=obs.get("properties"),
            owner_id=row["owner_id"],
            visibility=row["visibility"],
        )


JOB_HANDLERS: dict[str, JobHandler] = {
    "normalize_trace_event": handle_normalize_trace_event,
}


async def claim_jobs(pool: asyncpg.Pool, *, limit: int) -> list[dict]:
    """
    Real SKIP LOCKED claim: marks up to `limit` pending jobs 'processing'
    and returns them, atomically, safe under concurrent workers even
    though only one runs today. asyncpg.Record -> dict so callers don't
    hold the connection/row open past this function's own transaction.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, job_type, payload, attempts FROM ingestion_jobs "
                "WHERE status = 'pending' "
                "ORDER BY id "
                "LIMIT $1 "
                "FOR UPDATE SKIP LOCKED",
                limit,
            )
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE ingestion_jobs SET status = 'processing', claimed_at = now() "
                "WHERE id = ANY($1::bigint[])",
                ids,
            )
    return [dict(r) for r in rows]


async def process_pending_jobs(pool: asyncpg.Pool, *, limit: int = 500) -> dict:
    """
    Real entry point: claim up to `limit` pending jobs, run each through
    its registered handler, mark done/failed individually. One job's
    failure (unknown job_type, bad payload, handler exception) does not
    stop the batch -- A4's lesson applied here too: a single malformed
    job must not permanently stall every job after it in the same run.

    Returns real counts, not estimates, same discipline as
    process_collector_file()'s own return value.
    """
    jobs = await claim_jobs(pool, limit=limit)
    done = 0
    failed = 0
    unknown_type = 0

    for job in jobs:
        job_id = job["id"]
        job_type = job["job_type"]
        payload = job["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)

        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            unknown_type += 1
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'failed', "
                "attempts = attempts + 1, last_error = $2, completed_at = now() "
                "WHERE id = $1",
                job_id, f"no handler registered for job_type={job_type!r}",
            )
            continue

        try:
            await handler(pool, payload)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one
            # job's handler raising must not crash the batch loop; the
            # real error is preserved in last_error for later inspection.
            failed += 1
            log.warning("ingestion job %s (%s) failed: %s", job_id, job_type, exc)
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'failed', "
                "attempts = attempts + 1, last_error = $2, completed_at = now() "
                "WHERE id = $1",
                job_id, repr(exc),
            )
        else:
            done += 1
            await pool.execute(
                "UPDATE ingestion_jobs SET status = 'done', "
                "attempts = attempts + 1, completed_at = now() "
                "WHERE id = $1",
                job_id,
            )

    return {
        "claimed": len(jobs),
        "done": done,
        "failed": failed,
        "unknown_type": unknown_type,
    }


async def requeue_stuck_jobs(pool: asyncpg.Pool, *, older_than_minutes: int = 30) -> int:
    """
    Manual/explicit recovery for jobs left 'processing' by a worker that
    crashed mid-job (the one gap SKIP LOCKED itself doesn't close -- it
    protects against two workers claiming the SAME row, not against a
    claimed row never being finished). Deliberately not run
    automatically inside process_pending_jobs() -- a job stuck because of
    a genuine handler bug should surface via last_error and be looked
    at, not silently retry forever on every run.
    """
    result = await pool.execute(
        "UPDATE ingestion_jobs SET status = 'pending', claimed_at = NULL "
        "WHERE status = 'processing' "
        "AND claimed_at < now() - ($1 || ' minutes')::interval",
        str(older_than_minutes),
    )
    # asyncpg execute() returns a string like "UPDATE 3"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
