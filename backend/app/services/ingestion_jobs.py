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

# --------------------------------------------------------------------------
# IngestionContext for the TRACE / execution-derived path (Gate G1, §A1).
#
# The document path (skill_ingestion.compile_skill_artifact) already opens
# one. The trace path did not, so every observation / claim / procedure it
# produced carried a NULL ingestion_context_id -- unanswerable "who / under
# what scope / by which extractor produced this". The unit is the SESSION:
# a trace's events arrive as many jobs, and episodes group by session, so
# one context spans a whole session's ingestion. It is resolved from the
# DB (not an in-process cache) so it survives worker restarts and stays
# idempotent -- never a fabricated duplicate.
# --------------------------------------------------------------------------
TRACE_INGESTION_EXTRACTOR = "trace_ingestion"
TRACE_INGESTION_EXTRACTOR_VERSION = "deterministic_v1"


async def resolve_trace_ingestion_context(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    owner_id: Optional[str] = None,
    visibility: str = "public",
) -> Optional[str]:
    """The open IngestionContext for one trace session, opening one on first
    call. Returns None only if `session_id` is falsy. Idempotent: a second
    call for the same session returns the same row (looked up by
    source_uri), so concurrent workers converge instead of duplicating."""
    if not session_id:
        return None
    from app.services.ingestion_context import open_ingestion_context

    source_uri = f"session:{session_id}"
    existing = await pool.fetchval(
        "SELECT id FROM ingestion_contexts "
        "WHERE source_uri = $1 AND status = 'open' "
        "ORDER BY started_at DESC LIMIT 1",
        source_uri,
    )
    if existing is not None:
        return str(existing)
    return await open_ingestion_context(
        pool,
        source_type="trace",
        source_uri=source_uri,
        source_hash=None,
        extractor_id=TRACE_INGESTION_EXTRACTOR,
        extractor_version=TRACE_INGESTION_EXTRACTOR_VERSION,
        actor_id=owner_id or TRACE_INGESTION_EXTRACTOR,
        scope_type="session",
        scope_entity_id=str(session_id),
        classification="EXECUTION_DERIVED",
        visibility=visibility,
        owner_id=owner_id,
    )


def _general_compute_client() -> Optional[Any]:
    """One shared, general-purpose chat-completions client for the
    capability-abstraction / admission-escalation model calls this worker
    makes -- General Compute (`app.debate.panel.OpenAICompatAgent`'s same
    OpenAI-compatible construction) is this codebase's existing named
    tier for "some general-purpose hosted model," reused here rather than
    inventing a second provider concept. Returns None (never fails the
    ingestion job) if General Compute isn't configured -- compile_skill_
    artifact's own client=None path already handles that by honestly
    abstaining from capability-statement generation, exactly as it did
    before this function existed."""
    from app.config import settings

    if not settings.general_compute_api_key or not settings.general_compute_judge_model:
        return None
    from openai import OpenAI

    return OpenAI(
        api_key=settings.general_compute_api_key,
        base_url=settings.general_compute_base_url,
    )


async def handle_ingest_skill_package(pool: asyncpg.Pool, payload: dict) -> None:
    """Ingest exactly one immutable skill package, retryably and idempotently."""
    from app.config import settings
    from app.services.embeddings import Embedder
    from app.services.ingestion_sources import GitHubSkillCorpusSource
    from app.services.ingestion_sources.base import SourceRef
    from app.services.ingestion_sources.manifest import CorpusSourceSpec
    from app.services.skill_ingestion import compile_skill_artifact

    commit = str(payload["commit"])
    path = str(payload["path"])

    spec = CorpusSourceSpec(
        id=str(payload["source_id"]),
        priority=int(payload.get("priority", 1)),
        type=payload.get("source_type", "github"),
        repo=str(payload["repo"]),
        path=payload.get("subtree"),
        expected_format=payload.get("expected_format", "skill_repository"),

        # IMPORTANT:
        # Reconstruct the worker adapter from the immutable commit
        # discovered and stored in the queued job, not from HEAD/main.
        ref=commit,
    )

    adapter = GitHubSkillCorpusSource(spec)
    uri = str(payload.get("uri") or f"https://github.com/{adapter.slug}/blob/{commit}/{path}")
    artifact = adapter.fetch(SourceRef(
        uri=uri, repository=adapter.slug, path=path, commit=commit,
        source_id=spec.id,
    ))
    client = _general_compute_client()
    await compile_skill_artifact(
        pool, artifact, embedder=Embedder(rate_limit_pool=pool),
        created_by="structured_skill_ingestion_worker",
        client=client,
        admission_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
        capability_llm_model=settings.general_compute_judge_model or "gemma-4-31B-it",
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
        "SELECT id, session_id, event_type, tool_name, tool_input, tool_output, "
        "       owner_id, visibility::text AS visibility "
        "FROM trace_events WHERE id = $1",
        trace_event_id,
    )
    if row is None:
        log.info("normalize_trace_event: trace_event %s no longer exists, skipping", trace_event_id)
        return

    # G1: one IngestionContext per session; every observation this handler
    # persists is stamped with it, and the id rides the promote job so the
    # derived claim carries it too.
    ingestion_context_id = await resolve_trace_ingestion_context(
        pool, str(row["session_id"]) if row["session_id"] else "",
        owner_id=row["owner_id"], visibility=row["visibility"],
    )

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
        # persist_observation takes no ingestion_context_id kwarg -- stamp
        # it in a follow-up UPDATE, the same pattern skill_ingestion uses
        # for the document Observation.
        if ingestion_context_id is not None:
            await pool.execute(
                "UPDATE observations SET ingestion_context_id = $1::uuid WHERE id = $2::uuid",
                ingestion_context_id, observation_id,
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
                "ingestion_context_id": ingestion_context_id,
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
        return

    # G1: carry the session's IngestionContext onto the derived claim. The
    # id rides the payload from handle_normalize_trace_event; a legacy job
    # without it leaves the column NULL rather than paying for a lookup
    # (the recovery sweep re-enqueues with a fresh payload).
    ingestion_context_id = payload.get("ingestion_context_id")
    if ingestion_context_id:
        await pool.execute(
            "UPDATE knowledge_nodes SET ingestion_context_id = $1::uuid "
            "WHERE id = $2::uuid AND ingestion_context_id IS NULL",
            ingestion_context_id, claim_id,
        )


JOB_HANDLERS: dict[str, JobHandler] = {
    "normalize_trace_event": handle_normalize_trace_event,
    "promote_observation_to_claim": handle_promote_observation_to_claim,
    "ingest_skill_package": handle_ingest_skill_package,
    # 'extract_procedure_from_episode' is registered further down, right
    # after its handler is defined -- that handler sits below the sweep it
    # belongs with, and a forward reference here would be a NameError at
    # import time. Registration is asserted by a test either way.
}


async def enqueue_skill_package_jobs(
    pool: asyncpg.Pool, *, source_spec: dict, refs: list[dict],
) -> int:
    """Queue one deduplicated job per package for distributed workers."""
    queued = 0
    for ref in refs:
        payload = {**source_spec, **ref}
        # Older workers wrote json.dumps(payload) through asyncpg's JSONB
        # codec, producing a JSON *string* rather than an object. Decode
        # that legacy shape while checking idempotency, then write the new
        # object shape below. Without this compatibility expression every
        # rerun misses the existing job and floods the shared queue.
        exists = await pool.fetchval(
            "SELECT 1 FROM ingestion_jobs WHERE job_type='ingest_skill_package' "
            "AND (CASE WHEN jsonb_typeof(payload)='string' "
            "THEN (payload #>> '{}')::jsonb ELSE payload END)->>'source_id'=$1 "
            "AND (CASE WHEN jsonb_typeof(payload)='string' "
            "THEN (payload #>> '{}')::jsonb ELSE payload END)->>'commit'=$2 "
            "AND (CASE WHEN jsonb_typeof(payload)='string' "
            "THEN (payload #>> '{}')::jsonb ELSE payload END)->>'path'=$3 "
            "AND status IN ('pending','processing','done') LIMIT 1",
            str(payload["source_id"]), str(payload["commit"]), str(payload["path"]),
        )
        if exists:
            continue
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2::jsonb)",
            # create_pool() registers a JSONB encoder, so pass the object
            # itself. json.dumps(payload) would be encoded a second time.
            "ingest_skill_package", payload,
        )
        queued += 1
    return queued


async def resume_failed_skill_jobs(
    pool: asyncpg.Pool, *, include_embedding_failures: bool = False,
) -> int:
    """Requeue fixable skill failures without creating an API-quota storm.

    Provider-exhaustion rows stay failed by default until an operator has
    restored quota or deliberately selected a provider. Source/fetch/parser
    failures can be retried through the normal --resume path.
    """
    result = await pool.execute(
        "UPDATE ingestion_jobs SET status='pending', claimed_at=NULL, completed_at=NULL "
        "WHERE job_type='ingest_skill_package' AND status='failed' "
        "AND ($1 OR last_error IS NULL OR last_error NOT LIKE 'EmbeddingError(%')",
        include_embedding_failures,
    )
    tail = result.rsplit(" ", 1)[-1]
    return int(tail) if tail.isdigit() else 0


# The episode-arrived-late recovery path. Ordering matters here and there
# is no way around it: resolve_justification_episode() runs at ENQUEUE
# time, so an observation whose session has not been through episode
# assembly yet resolves None, the job completes as 'done' having correctly
# done nothing, and nothing ever revisits it.
#
# Measured on real dogfooding data 2026-08-29: ingestion ran before
# assembly, so all 3,106 promotion jobs resolved a NULL episode and the
# substrate ended with ONE claim instead of ~3,106. The handler was right,
# the queue was right, and the loop still did not run -- the gap was
# purely that nothing re-offers work once the missing anchor appears.
#
# Deliberately a re-ENQUEUE rather than a retry: the original jobs are
# genuinely 'done' (they did the correct thing with the information that
# existed), and rewriting terminal job rows would destroy the record of
# what actually happened. A new job for new information is the honest
# shape, and it keeps ingestion_jobs append-only in spirit with the rest
# of the substrate.
_PENDING_PROMOTION_SQL = """
WITH anchor AS (
    -- The observation's EARLIEST event, matching
    -- resolve_justification_episode()'s own anchor choice exactly.
    SELECT DISTINCT ON (oe.observation_id)
           oe.observation_id, oe.event_id, te.session_id, te."timestamp"
    FROM observation_events oe
    JOIN trace_events te ON te.id = oe.event_id
    ORDER BY oe.observation_id, te."timestamp" ASC
)
, candidate AS (
    SELECT a.observation_id, a.event_id, a."timestamp",
           (SELECT ep.id FROM episodes ep
             WHERE ep.session_id = a.session_id
               AND ep.start_ts <= a."timestamp"
               AND (ep.end_ts IS NULL OR a."timestamp" <= ep.end_ts)
               AND ep.t_invalid IS NULL
             -- Same NULLS LAST / start_ts DESC innermost-wins ordering as
             -- resolve_justification_episode(). If one changes, both must.
             ORDER BY ep.parent_episode_id NULLS LAST, ep.start_ts DESC
             LIMIT 1) AS episode_id
    FROM anchor a
    WHERE NOT EXISTS (
            -- already produced a claim: nothing owed
            SELECT 1 FROM claim_sources cs WHERE cs.observation_id = a.observation_id)
      AND NOT EXISTS (
            -- a promotion is already queued for it: never double-enqueue,
            -- because each promotion costs one real embedding call
            SELECT 1 FROM ingestion_jobs j
             WHERE j.job_type = 'promote_observation_to_claim'
               AND j.status IN ('pending', 'processing')
               AND j.payload->>'observation_id' = a.observation_id::text)
)
SELECT observation_id, event_id, episode_id
FROM candidate
-- ANCHORED ROWS FIRST, and this ordering is load-bearing, not cosmetic.
-- Found by running the first cut against the real corpus: ordering purely
-- by newest-first spent the entire budget on the live session's own tail
-- (examined 25, enqueued 0, still_unanchored 25) because the newest
-- observations are exactly the ones episode assembly has not reached yet.
-- A bounded sweep must spend its limit on work it can actually complete;
-- the unanchored frontier is still counted and reported, just not
-- allowed to crowd out the backlog.
ORDER BY (episode_id IS NULL), "timestamp" DESC
LIMIT $1
"""


async def enqueue_pending_claim_promotions(
    pool: asyncpg.Pool, *, limit: int = 100
) -> dict:
    """Re-offer observations whose justifying episode arrived after their
    original promotion job already completed.

    Returns real counts: {"examined", "enqueued", "still_unanchored"}.

    BOUNDED AND OPT-IN ON PURPOSE. Every job this creates ends in
    capture_claim(), which computes an embedding -- a real, paid Voyage
    call, one per observation. An unbounded sweep over a dogfooding
    corpus is thousands of calls nobody asked for, so the caller must
    choose a limit and `run_ingestion.py` only calls this behind an
    explicit flag. `limit` caps rows examined AND enqueued together;
    newest observations first, since those are the ones a user is most
    likely to be waiting on.

    Idempotent: an observation with a claim, or with a promotion already
    pending/processing, is skipped. Running it twice in a row enqueues
    nothing the second time.
    """
    if limit <= 0:
        # A true no-op, not an empty result: the default path must not
        # even pay for the sweep query.
        return {"examined": 0, "enqueued": 0, "still_unanchored": 0}

    rows = await pool.fetch(_PENDING_PROMOTION_SQL, limit)
    enqueued = 0
    unanchored = 0
    for r in rows:
        if r["episode_id"] is None:
            # Assembly still has not covered this session. Correct no-op,
            # counted rather than hidden so the caller can see the real
            # size of the remaining backlog.
            unanchored += 1
            continue
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
            "promote_observation_to_claim",
            json.dumps({
                "observation_id": str(r["observation_id"]),
                "trace_event_id": str(r["event_id"]),
                "task_ids": [],
                "justification_episode_id": str(r["episode_id"]),
                # Distinguishes a recovery enqueue from the original
                # inline one when reading the job table by hand later.
                "requeued_after_episode_assembly": True,
            }),
        )
        enqueued += 1
    return {
        "examined": len(rows),
        "enqueued": enqueued,
        "still_unanchored": unanchored,
    }


# ---------------------------------------------------------------------
# claim -> procedure candidate. The last manual hop in the founding loop.
#
# THE QUALITY GATE, and why it is this and not a round number.
#
# Measured over all 230 episodes on the real corpus that contain at least
# one observation (2026-08-29):
#     n_obs        p25=4  median=8  p75=20  max=517
#     >=2 distinct observation_types :  79
#     containing a test_run          :  15
#     containing a commit_made       :   3
#     completion signal (either)     :  17
#     this gate, all three clauses   :  16   (7% of episodes)
#
# 1. EXPLICIT GOAL + OUTCOME REQUIRED. A test *command* or a commit is
#    not a successful outcome. The row must carry a declared goal (episode
#    metadata or agent_traces.intent) and explicitly passing test_run
#    observations, with no failed or ungraded test. Both values are copied into the job
#    payload and revalidated by the worker. This is a correctness
#    requirement: extract_procedure() refuses anything whose
#    evidence.outcome != "success" (V5_evidence_sufficiency). Inferring
#    either field here would manufacture the evidence V5 is meant to
#    require, so episodes lacking either stay unextracted.
#
# 2. n_obs >= 5. p25 is 4, so this drops the bottom quartile. Below five
#    observations there is not enough tool sequence for
#    derive_step_skeleton() to produce a step list worth reviewing.
#
# 3. n_types >= 2. THE FILTER THAT KILLS THE GARBAGE. A single-type
#    episode is "edited six files" or "ran six commands" -- no task shape
#    at all. This is what excludes the "Modified check3.py"-shaped claims
#    the corpus audit flagged as near-worthless: those live in
#    file_touched-only episodes and would burn a real LLM call to produce
#    a candidate nobody would approve.
#
# The V4 validator is a deliberate SECOND line of defence behind this
# gate, not a replacement for it: if grounded_hybrid_v1 degrades to
# deterministic_v1 (no client, API failure, ABSTAIN), capability_statement
# becomes goal_text verbatim, V4 sees the evidence token in it and
# refuses, and no procedures row is written. Garbage in therefore costs at
# most one call and still cannot produce a garbage row -- but the gate is
# what stops us making the call at all.
#
# ONE EXTRACTION PER EPISODE, not per claim: the episode is the unit of
# work extract_procedure() actually consumes (SessionEvidenceSource reads
# the whole session window). Several claims sharing an episode would
# otherwise each pay for the same extraction.
_PENDING_EXTRACTION_SQL = """
WITH claim_episode AS (
    -- claims that have a justifying episode and no procedure yet
    SELECT DISTINCT el.episode_id
    FROM episode_links el
    JOIN knowledge_nodes k ON k.id = el.target_id
     AND k.node_type = 'claim' AND k.t_invalid IS NULL
    WHERE el.target_table = 'knowledge_nodes'
),
profile AS (
    SELECT ep.id AS episode_id, ep.session_id,
           count(DISTINCT o.id) AS n_obs,
           count(DISTINCT o.observation_type) AS n_types,
           count(DISTINCT o.id) FILTER (
               WHERE o.observation_type = 'test_run'
                 AND o.properties->>'passed' = 'true'
           ) AS passing_tests,
           count(DISTINCT o.id) FILTER (
               WHERE o.observation_type = 'test_run'
                 AND o.properties->>'passed' = 'false'
           ) AS failing_tests,
           count(DISTINCT o.id) FILTER (
               WHERE o.observation_type = 'test_run'
                 AND (o.properties->>'passed') IS DISTINCT FROM 'true'
                 AND (o.properties->>'passed') IS DISTINCT FROM 'false'
           ) AS unknown_tests,
           COALESCE(
               NULLIF(BTRIM(ep.metadata->>'declared_goal'), ''),
               NULLIF(BTRIM(ep.metadata->>'goal'), ''),
               NULLIF(BTRIM(ep.metadata->>'intent'), ''),
               NULLIF(BTRIM(ep.metadata->>'user_goal'), ''),
               (SELECT NULLIF(BTRIM(at.intent), '')
                  FROM agent_traces at
                 WHERE at.session_id = ep.session_id
                   AND at.intent IS NOT NULL
                 ORDER BY at.started_at ASC
                 LIMIT 1)
           ) AS goal_text
    FROM episodes ep
    JOIN trace_events te ON te.session_id = ep.session_id
         AND te."timestamp" >= ep.start_ts
         AND (ep.end_ts IS NULL OR te."timestamp" <= ep.end_ts)
    JOIN observation_events oe ON oe.event_id = te.id
    JOIN observations o ON o.id = oe.observation_id
    WHERE ep.t_invalid IS NULL
      AND ep.id IN (SELECT episode_id FROM claim_episode)
    GROUP BY ep.id, ep.session_id
)
SELECT p.episode_id, p.session_id, p.n_obs, p.n_types,
       p.passing_tests, p.failing_tests, p.unknown_tests, p.goal_text
FROM profile p
WHERE p.goal_text IS NOT NULL   -- clause 1: exact source-supplied goal
  AND p.passing_tests > 0       -- clause 2: explicit success evidence
  AND p.failing_tests = 0       -- no known failed test may be called success
  AND p.unknown_tests = 0       -- no ungraded test may be called success
  AND p.n_obs   >= $2           -- clause 3: enough sequence to derive from
  AND p.n_types >= $3           -- clause 4: an actual task shape
  AND NOT EXISTS (
        -- idempotency: this episode already produced a procedure
        SELECT 1 FROM procedures pr
         WHERE pr.t_invalid IS NULL
           AND pr.source_episode_ids @> ARRAY[p.episode_id])
  AND NOT EXISTS (
        -- never double-enqueue: each job is a real paid LLM call
        SELECT 1 FROM ingestion_jobs j
         WHERE j.job_type = 'extract_procedure_from_episode'
           AND j.status IN ('pending', 'processing')
           AND j.payload->>'episode_id' = p.episode_id::text)
ORDER BY p.n_obs DESC
LIMIT $1
"""

MIN_OBSERVATIONS_TO_EXTRACT = 5
MIN_OBSERVATION_TYPES_TO_EXTRACT = 2


async def enqueue_pending_procedure_extractions(
    pool: asyncpg.Pool, *, limit: int = 10,
) -> dict:
    """Enqueue procedure extraction for claim-justifying episodes that
    clear the quality gate above. Returns {"examined", "enqueued"}.

    BOUNDED AND OPT-IN, same shape as enqueue_pending_claim_promotions:
    every job this creates ends in one real grounded_hybrid_v1 LLM call,
    so the caller must choose a limit and run_ingestion.py only reaches
    this behind an explicit --extract-limit flag. Richest episodes first
    (n_obs DESC): if the budget is small, spend it where there is most to
    extract from.

    Idempotent: an episode that already produced a live procedure, or that
    already has an extraction pending/processing, is skipped.
    """
    if limit <= 0:
        return {"examined": 0, "enqueued": 0}

    rows = await pool.fetch(
        _PENDING_EXTRACTION_SQL, limit,
        MIN_OBSERVATIONS_TO_EXTRACT, MIN_OBSERVATION_TYPES_TO_EXTRACT,
    )
    for r in rows:
        await pool.execute(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
            "extract_procedure_from_episode",
            json.dumps({
                "episode_id": str(r["episode_id"]),
                "session_id": r["session_id"],
                # Source facts selected by _PENDING_EXTRACTION_SQL, never
                # worker defaults. Durable jobs are revalidated below.
                "goal_text": r["goal_text"],
                "outcome": "success",
                # Carried for the audit trail: which facts let this episode
                # through the gate at enqueue time.
                "gate": {
                    "n_obs": r["n_obs"],
                    "n_types": r["n_types"],
                    "passing_tests": r["passing_tests"],
                    "failing_tests": r["failing_tests"],
                    "unknown_tests": r["unknown_tests"],
                },
            }),
        )
    return {"examined": len(rows), "enqueued": len(rows)}


async def handle_extract_procedure_from_episode(
    pool: asyncpg.Pool, payload: dict,
) -> None:
    """Run the real extract_procedure() over a gated episode.

    The worker accepts only a source-derived `goal_text` and explicit
    `outcome="success"` payload produced by _PENDING_EXTRACTION_SQL. It
    never manufactures either value: old/manual jobs missing those facts
    fail before an extraction call or a persisted candidate.
    """
    episode_id = payload.get("episode_id")
    session_id = payload.get("session_id")
    goal_text = payload.get("goal_text")
    outcome = payload.get("outcome")
    if not episode_id or not session_id or not isinstance(goal_text, str) or not goal_text.strip():
        raise ValueError(
            "extract_procedure_from_episode payload missing source-derived ids or goal_text"
        )
    if outcome != "success":
        raise ValueError(
            "extract_procedure_from_episode requires an explicit successful outcome"
        )

    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    # EPISODE-WINDOWED, not session-wide. SessionEvidenceSource would be
    # the obvious choice and it is the WRONG one here: it reads every
    # observation and every tool call for the whole session_id and treats
    # episode_id as a label only. Proved by running it -- three different
    # gated episodes from one session produced three byte-identical
    # procedures (178 steps each, same capability_statement), because all
    # three extractions saw exactly the same session-wide evidence. A
    # multi-hour session flattened into one tool histogram also has no
    # semantic shape for grounded_hybrid_v1 to abstract, so even a
    # working LLM call returned the goal text unchanged.
    #
    # AgentRunEvidenceSource takes the evidence in memory, which lets the
    # window be applied HERE, in this lane, using the same public API
    # mcp_server/server.py already calls -- rather than reaching into
    # procedure_extraction/evidence.py, which this lane does not own.
    ep = await pool.fetchrow(
        "SELECT session_id, start_ts, end_ts, project_id FROM episodes "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        str(episode_id),
    )
    if ep is None:
        log.info("extract_procedure_from_episode: episode %s is gone; skipping",
                 episode_id)
        return

    window = (
        'te.session_id = $1 AND te."timestamp" >= $2 '
        'AND ($3::timestamptz IS NULL OR te."timestamp" <= $3)'
    )
    obs_rows = await pool.fetch(
        "SELECT DISTINCT o.id, o.observation_type, o.label, o.properties "
        "FROM observations o "
        "JOIN observation_events oe ON oe.observation_id = o.id "
        "JOIN trace_events te ON te.id = oe.event_id "
        f"WHERE {window} ORDER BY o.id",
        ep["session_id"], ep["start_ts"], ep["end_ts"],
    )
    tool_rows = await pool.fetch(
        "SELECT te.tool_name FROM trace_events te "
        f"WHERE {window} AND te.tool_name IS NOT NULL ORDER BY te.sequence ASC",
        ep["session_id"], ep["start_ts"], ep["end_ts"],
    )

    observations = [
        {"observation_type": r["observation_type"], "label": r["label"],
         "properties": dict(r["properties"] or {})}
        for r in obs_rows
    ]
    tool_sequence = [r["tool_name"] for r in tool_rows]

    source = AgentRunEvidenceSource(
        goal_text=goal_text.strip(), outcome=outcome, observations=observations,
        tool_sequence=tool_sequence, started_at=ep["start_ts"],
        project_id=ep["project_id"], episode_id=str(episode_id),
        session_id=ep["session_id"], steps_used=len(tool_sequence),
    )
    result = await extract_procedure(pool, source, client=_extraction_client())

    if result.validation_failures:
        # Not an error: the validators refusing a weak candidate is the
        # system working. Logged so a sweep's real yield is visible.
        log.info(
            "extract_procedure_from_episode: episode %s refused by validators: %s",
            episode_id, result.validation_failures,
        )
        return
    # ABSTENTION, caught after the fact on purpose. grounded_hybrid_v1
    # degrades to deterministic behaviour on ABSTAIN / unparseable
    # response, and deterministic_v1 sets capability_statement =
    # goal_text verbatim -- so an abstained extraction is exactly the row
    # whose capability_statement still equals the seed. V4 does not catch
    # it here because the seed is deliberately generic (a goal naming a
    # file WOULD be caught), so the check belongs to the caller that chose
    # the seed. Observed 1 of 3 on the real corpus: a 42-Bash-call episode
    # with no shape for the model to abstract.
    #
    # Closed rather than never-written because extract_procedure() persists
    # before returning and this lane does not own that function. Closing
    # the validity window is the substrate's own idiom anyway (nothing is
    # deleted), and it leaves the abstention itself on the record.
    if (result.extracted is not None
            and result.extracted.capability_statement == goal_text):
        await pool.execute(
            "UPDATE procedures SET t_invalid = now(), verification_state = 'retired' "
            "WHERE id = $1::uuid AND t_invalid IS NULL",
            str(result.version_row_id),
        )
        log.info(
            "extract_procedure_from_episode: episode %s ABSTAINED "
            "(capability_statement == goal seed); row %s retired immediately",
            episode_id, result.version_row_id,
        )
        return

    # G1: stamp the session's IngestionContext onto the new procedure
    # version row and any procedure-targeted evidence extract_procedure
    # wrote for it. Follow-up UPDATEs -- extract_procedure()/capture_procedure()
    # take no ingestion_context_id kwarg and this lane does not own them.
    ingestion_context_id = await resolve_trace_ingestion_context(
        pool, str(ep["session_id"]),
    )
    if ingestion_context_id is not None and result.version_row_id is not None:
        await pool.execute(
            "UPDATE procedures SET ingestion_context_id = $1::uuid "
            "WHERE id = $2::uuid AND ingestion_context_id IS NULL",
            ingestion_context_id, str(result.version_row_id),
        )
        await pool.execute(
            "UPDATE evidence SET ingestion_context_id = $1::uuid "
            "WHERE target_type = 'procedure' AND target_id = $2::uuid "
            "AND ingestion_context_id IS NULL",
            ingestion_context_id, str(result.version_row_id),
        )

    log.info(
        "extract_procedure_from_episode: episode %s -> procedure %s (by %s)",
        episode_id, result.procedure_id, result.extracted_by,
    )

    await _maybe_auto_synthesize(
        pool, episode_id=str(episode_id),
        scope_type="project" if ep["project_id"] else "global",
        scope_entity_id=ep["project_id"],
    )


# ---------------------------------------------------------------------
# Multi-episode generalization (L2), auto-discovered -- closes the real
# gap synthesis.py's own module docstring names and does not fill: that
# module's `synthesize_procedure()` had ZERO production callers before
# this pass (grepped this session), reachable only from tests supplying a
# hand-picked episode_ids list. This is the ONE natural trigger point a
# normal ingestion run already has after a real, successful single-
# episode extraction: the episode this job just turned into a procedure
# is exactly the "successful episode" synthesis.py's own docstring
# describes as its input.
#
# RETRIEVAL, NOT A MANUAL ID LIST. synthesis.py's own "What this does NOT
# do" section is explicit that candidate discovery is the CALLER's job --
# this function is that caller. No embedding column is ever populated for
# `procedures` today (grepped: extract_procedure()/capture_procedure()
# never pass one), so a real vector search has nothing to query; the
# honest substitute is a real SQL query over already-captured evidence:
# other LIVE procedures that are themselves still single-episode
# extractions (source_episode_ids length 1 -- i.e. not already folded
# into a synthesis result) sharing this episode's own real project scope.
# Coarse on purpose, per synthesis.py's own division of labor: its three
# real compatibility gates (structural tool-sequence alignment, verification
# agreement, predicate contradiction) are what actually decide merge-or-
# refuse, not this query -- a related-but-incompatible candidate found
# here is expected to come back refused, not silently merged.
MAX_SYNTHESIS_CANDIDATES = 4  # + this episode = 5, inside synthesis.py's own
# stated single-digit-N scale ("no support for more than a small number of
# episodes in one call" -- its module docstring, "What this does NOT do").


async def _discover_synthesis_candidates(
    pool: asyncpg.Pool, *, episode_id: str, scope_type: str,
    scope_entity_id: Optional[str], limit: int = MAX_SYNTHESIS_CANDIDATES,
) -> list[str]:
    rows = await pool.fetch(
        "SELECT source_episode_ids[1] AS episode_id FROM procedures "
        "WHERE t_invalid IS NULL AND array_length(source_episode_ids, 1) = 1 "
        "AND source_episode_ids[1] != $1::uuid "
        "AND scope_type = $2 AND scope_entity_id IS NOT DISTINCT FROM $3 "
        "ORDER BY t_created DESC LIMIT $4",
        episode_id, scope_type, scope_entity_id, limit,
    )
    return [str(r["episode_id"]) for r in rows]


async def _maybe_auto_synthesize(
    pool: asyncpg.Pool, *, episode_id: str, scope_type: str,
    scope_entity_id: Optional[str],
) -> None:
    """Synchronous invocation at the natural trigger point -- no new
    scheduler/queue, reuses `synthesize_procedure()` completely unchanged.
    A refusal (structural mismatch, verification disagreement, or a
    contradictory predicate) is the system working exactly as designed:
    the candidate batch is logged and left as distinct, un-blended
    single-episode procedures, never forced into one falsely-universal
    result."""
    from app.services.procedure_extraction.synthesis import (
        MIN_CANDIDATE_EPISODES,
        synthesize_procedure,
    )

    candidates = await _discover_synthesis_candidates(
        pool, episode_id=episode_id, scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )
    batch = [episode_id] + candidates
    if len(batch) < MIN_CANDIDATE_EPISODES:
        return

    synth = await synthesize_procedure(pool, batch)
    if synth.synthesized:
        log.info(
            "extract_procedure_from_episode: auto-discovered synthesis over %s -> "
            "generalized procedure %s (level %s)",
            synth.contributing_episode_ids, synth.procedure_id, synth.generalization_level,
        )
    else:
        log.info(
            "extract_procedure_from_episode: auto-discovered synthesis batch %s refused: %s",
            batch, synth.refusal_reason,
        )


def _extraction_client():
    """The same OpenAI-compatible client mcp_server/server.py builds for
    its own extract_procedure() call. Returns None when no key is
    configured, which makes GroundedHybridExtractor degrade to
    deterministic_v1 rather than fail -- and V4 then refuses the weak
    candidate, so a missing key costs nothing and writes nothing.
    """
    try:
        from openai import OpenAI

        from app.config import settings

        key = settings.general_compute_api_key
        if not key:
            return None
        return OpenAI(
            max_retries=0, api_key=key,
            base_url=settings.general_compute_base_url,
        )
    except Exception as exc:  # noqa: BLE001 -- never block ingestion on this
        log.warning("extraction client unavailable (%s); degrading to deterministic", exc)
        return None


# Registered here rather than in the literal above: the handler is defined
# below that dict, beside the sweep that feeds it.
JOB_HANDLERS["extract_procedure_from_episode"] = handle_extract_procedure_from_episode


async def claim_jobs(
    pool: asyncpg.Pool, *, limit: int, job_types: Optional[list[str]] = None,
    worker_id: Optional[str] = None,
) -> list[dict]:
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
                "AND ($1::text[] IS NULL OR job_type = ANY($1::text[])) "
                "ORDER BY id "
                "LIMIT $2 "
                "FOR UPDATE SKIP LOCKED",
                job_types, limit,
            )
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE ingestion_jobs SET status = 'processing', claimed_at = now(), "
                "claimed_by = $2 WHERE id = ANY($1::bigint[])",
                ids, worker_id,
            )
    return [dict(r) for r in rows]


async def process_pending_jobs(
    pool: asyncpg.Pool, *, limit: int = 500, job_types: Optional[list[str]] = None,
    worker_id: Optional[str] = None,
) -> dict:
    """
    Real entry point: claim up to `limit` pending jobs, run each through
    its registered handler, mark done/failed individually. One job's
    failure (unknown job_type, bad payload, handler exception) does not
    stop the batch -- A4's lesson applied here too: a single malformed
    job must not permanently stall every job after it in the same run.

    Returns real counts, not estimates, same discipline as
    process_collector_file()'s own return value.
    """
    jobs = await claim_jobs(pool, limit=limit, job_types=job_types, worker_id=worker_id)
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
        "worker_id": worker_id,
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
