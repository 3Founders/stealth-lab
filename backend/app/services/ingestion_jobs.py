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
    promote_observation_to_claim,
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
    # by default in this codebase's connection setup), already a dict
    # depending on codec registration, or -- the case this comment used
    # to miss -- a DOUBLE-encoded str, which decodes to a str again and
    # was throwing 'str' object has no attribute 'get' on 32 of 3313 real
    # jobs. extract_deterministic_observations() now routes all three
    # through _decode_json_field(), so no decoding is duplicated here.
    observations = extract_deterministic_observations(trace_event)
    for obs in observations:
        observation_id = await persist_observation(
            pool,
            observation_type=obs["observation_type"],
            label=obs["label"],
            extractor_kind="deterministic",
            event_ids=[str(trace_event_id)],
            properties=obs.get("properties"),
            owner_id=row["owner_id"],
            visibility=row["visibility"],
        )
        # The other half of the observation -> claim hop. Same enqueue
        # idiom trace_worker.py:303-307 uses to create THIS job, kept
        # deliberately identical so there is one pattern to learn.
        #
        # Option B: resolve the containing episode HERE, at enqueue time,
        # so the job payload carries a real anchor. task_ids stays empty
        # until an observation->task_node mapping exists; the episode is
        # what makes the claim writable in the meantime. A None episode
        # (assembly hasn't run for this session yet) is enqueued anyway so
        # the queue reflects the real backlog -- the handler no-ops on it.
        justification_episode_id = await resolve_justification_episode(
            pool, observation_id,
        )
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
            "promote_observation_to_claim",
            json.dumps({
                "observation_id": observation_id,
                "trace_event_id": str(trace_event_id),
                "task_ids": [],
                "justification_episode_id": (
                    str(justification_episode_id) if justification_episode_id else None
                ),
            }),
        )


async def resolve_justification_episode(pool: asyncpg.Pool, observation_id: str):
    """The episode that contains an observation's earliest event, or None.

    Option B's new hop: an observation is anchored to the episode its
    events fall inside, which is what lets a trace-derived claim exist at
    all without a task_node.

    NESTING -- the design call, and a CORRECTION to the kickoff's proposed
    SQL. Episodes nest (parent + child covering the same instant), and the
    intent is that the INNERMOST/most specific one wins. The kickoff
    proposed `ORDER BY parent_episode_id NULLS FIRST, start_ts DESC`, but
    that is inverted: a PARENT is exactly the row whose parent_episode_id
    IS NULL, so NULLS FIRST selects the OUTERMOST episode. Ordering here is
    therefore NULLS LAST -- children (non-null parent) sort ahead of
    parents -- with start_ts DESC as the tiebreaker among siblings, picking
    the latest-starting and thus tightest-fitting span. Proven by a test
    against a real nested fixture rather than trusted.

    Returns None when nothing contains the event -- e.g. the trace was
    ingested but episode assembly has not run for that session yet. That
    is a legitimate no-op (blocking question 2's stated default), not an
    error and not a retry trigger.
    """
    anchor = await pool.fetchrow(
        "SELECT te.session_id, te.timestamp "
        "FROM observation_events oe "
        "JOIN trace_events te ON te.id = oe.event_id "
        "WHERE oe.observation_id = $1::uuid "
        "ORDER BY te.timestamp ASC LIMIT 1",
        observation_id,
    )
    if anchor is None:
        return None

    return await pool.fetchval(
        "SELECT id FROM episodes "
        "WHERE session_id = $1 "
        "  AND start_ts <= $2 "
        "  AND (end_ts IS NULL OR $2 <= end_ts) "
        "  AND t_invalid IS NULL "
        "ORDER BY parent_episode_id NULLS LAST, start_ts DESC "
        "LIMIT 1",
        anchor["session_id"], anchor["timestamp"],
    )


async def handle_promote_observation_to_claim(pool: asyncpg.Pool, payload: dict) -> None:
    """
    The observation -> claim hop, previously the break in the founding
    loop. promote_observation_to_claim() (observations.py) was real and
    tested but had ZERO production callers -- and registering it alone
    would not have helped, because nothing ever created work for it
    either. This handler plus handle_normalize_trace_event's enqueue are
    the two halves; one without the other is inert.

    NAMING: no job_type for this existed anywhere -- the three test files
    that exercise promote_observation_to_claim() call the function
    directly and never enqueue it, and the only job_type string in the
    repo is 'normalize_trace_event'. So this name is new by necessity,
    chosen to mirror the established handler/job_type pairing exactly
    rather than to invent a scheme.

    WHY THE PRE-CHECK BEFORE CALLING PROMOTE: claims.py:179-180 computes
    an embedding (`embedder or Embedder()` -- a real Voyage call) BEFORE
    claims.py:184-189 checks that `task_ids` resolve to live task_nodes
    and returns None if they don't. So an unresolvable promotion spends
    one API call per observation to produce nothing. Checking here first
    keeps a task-less observation free rather than merely useless.

    HONEST LIMIT, stated plainly because it bounds what this closes:
    nothing currently maps a trace-derived observation to a task_node.
    The observations table carries no task, trace, or session column, and
    capture_claim() hard-requires at least one live `task_nodes.skill_ref`
    match. So on a substrate populated only by trace ingestion this
    handler correctly promotes nothing. `task_ids` therefore rides in the
    job payload: when a real observation->task mapping exists, only the
    ENQUEUE site changes, not this handler.
    """
    observation_id = payload.get("observation_id")
    if not observation_id:
        raise ValueError(
            f"promote_observation_to_claim payload missing observation_id: {payload!r}"
        )

    task_ids = payload.get("task_ids") or []
    justification_episode_id = payload.get("justification_episode_id")

    # Option B: EITHER anchor is sufficient. Bail only when there is nothing
    # to anchor the claim to at all -- capture_claim would return None in
    # that case anyway, but only AFTER computing an embedding (a real Voyage
    # call), so checking here keeps an unanchorable observation free rather
    # than merely useless.
    if not task_ids and not justification_episode_id:
        log.debug(
            "promote_observation_to_claim: observation %s has neither task_ids "
            "nor a justification episode; skipping before embedding spend",
            observation_id,
        )
        return

    # Only the task_node path needs pre-validating: an episode id came from
    # our own resolution query against a live episodes row, whereas task_ids
    # are caller-supplied skill_refs that may match nothing. When task_ids
    # resolve to nothing but an episode IS present, that is the ordinary
    # episode-justified case -- proceed with an empty task list rather than
    # skipping.
    if task_ids:
        live = await pool.fetch(
            "SELECT 1 FROM task_nodes WHERE skill_ref = ANY($1::text[]) AND t_invalid IS NULL",
            task_ids,
        )
        if not live:
            if not justification_episode_id:
                log.debug(
                    "promote_observation_to_claim: none of %r resolve to a live "
                    "task_node and no episode; skipping observation %s before "
                    "embedding spend", task_ids, observation_id,
                )
                return
            task_ids = []

    claim_id = await promote_observation_to_claim(
        pool,
        observation_id=str(observation_id),
        task_ids=list(task_ids),
        justification_episode_id=justification_episode_id,
    )
    if claim_id is None:
        log.info(
            "promote_observation_to_claim: observation %s produced no claim "
            "(missing or out of scope)", observation_id,
        )


JOB_HANDLERS: dict[str, JobHandler] = {
    "normalize_trace_event": handle_normalize_trace_event,
    "promote_observation_to_claim": handle_promote_observation_to_claim,
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
