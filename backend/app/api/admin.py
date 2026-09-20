"""
Manual loop trigger (MVP plan, Section 15 Phase B / v1.1+ scheduling gap).

Before this file, nothing in the application ever called
TriggerDetector.scan() or LoopOrchestrator.run() -- the loop existed as
code but nothing invoked it. For v0, invocation is a manually-called
endpoint rather than an in-process scheduler: matches the "don't build
infrastructure before you need it" discipline used everywhere else in
this codebase (Section 12). Call it from a cron job, a dashboard button,
or curl. An actual scheduler is a config change away, not a rewrite --
whatever calls this endpoint on a timer is the seam.

Default thresholds are deliberately low so a demo can produce a real
trigger without weeks of production data. Real calibration against a
workflow's actual baseline (Section 15.1, Phase B) replaces these before
this endpoint is trustworthy for anything but a demo.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import enforce_limits, make_cost_recorder, require_admin_api_key
from app.debate.panel import default_judge, default_layer2_agent, default_panel
from app.services.loop import LoopOrchestrator
from app.services.procedure_extraction.failure_handlers import run_failure_handlers
from app.services.procedure_extraction.registry import approve_extractor, create_extractor_version
from app.services.triggers import ThresholdRule, TriggerDetector

log = logging.getLogger(__name__)
# require_admin_api_key gates the WHOLE router in one place -- every real
# route in this file (scan, ingestion/process, extractors, reextract,
# index-lag, failure-routes/process) was previously reachable by anyone
# who could reach the server at all (app/api/deps.py's own get_scope
# resolves anonymous by design). See require_admin_api_key's own
# docstring for the fail-closed default and why a shared key, not a
# real identity check, is the deliberate interim posture.
router = APIRouter(
    prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin_api_key)],
)

_DEMO_RULES = [
    ThresholdRule(name="high_error_rate", metric="error_rate", threshold=0.15, min_samples=5),
    ThresholdRule(name="high_rework_rate", metric="rework_rate", threshold=0.20, min_samples=5),
]


async def get_pool(request: Request):
    return request.app.state.pool


class DebateOutcome(BaseModel):
    debate_id: UUID
    state: str
    termination_reason: Optional[str]
    candidates_proposed: int
    candidates_passed_layer1: int
    detail: Optional[str] = None


class ScanResponse(BaseModel):
    triggers_found: int
    debates_run: int
    outcomes: list[DebateOutcome] = []
    errors: list[str] = []


@router.post("/scan", response_model=ScanResponse)
async def run_scan(
    thresholds: Optional[list[ThresholdRule]] = None,
    pool=Depends(get_pool),
    scope_key: str = Depends(enforce_limits),
) -> ScanResponse:
    """
    Detect bottlenecks against current trace data and run the full loop
    (debate -> Layer 1 eval -> scorecard) for each newly-recorded trigger.
    Requires ANTHROPIC_API_KEY, FIREWORKS_API_KEY, OPENAI_API_KEY, and
    GOOGLE_API_KEY to be set -- this is the endpoint that makes real LLM
    calls, the one thing this whole project hasn't been able to verify
    live in this environment.
    """
    detector = TriggerDetector(pool)
    hits = await detector.scan(thresholds or _DEMO_RULES)
    recorded = await detector.record(hits)

    recorder = make_cost_recorder(pool, scope_key, operation="debate")
    try:
        orchestrator = LoopOrchestrator(
            pool, default_panel(), default_judge(),
            layer2_agent=default_layer2_agent(), on_call=recorder,
        )
    except Exception as exc:  # noqa: BLE001 -- most likely missing API keys
        raise HTTPException(
            500, f"could not construct debate panel (check API keys in .env): {exc}"
        ) from exc

    outcomes: list[DebateOutcome] = []
    errors: list[str] = []
    for trigger_id in recorded:
        try:
            await orchestrator.run(trigger_id)
            row = await pool.fetchrow(
                "SELECT d.id, d.state::text AS state, d.termination_reason "
                "FROM debates d JOIN triggers t ON t.debate_id = d.id "
                "WHERE t.id = $1", trigger_id,
            )
            if row:
                candidate_count = await pool.fetchval(
                    "SELECT COUNT(*) FROM candidates WHERE debate_id = $1", row["id"]
                )
                passed_count = await pool.fetchval(
                    "SELECT COUNT(*) FROM scorecards WHERE debate_id = $1 AND layer1_passed",
                    row["id"],
                )
                # The actual reason a debate closed -- including real agent
                # failure detail, not just "no candidates" -- lives in the
                # event log, not the debates row itself.
                detail_row = await pool.fetchrow(
                    "SELECT reason FROM debate_events WHERE debate_id = $1 "
                    "AND to_state IN ('REJECTED', 'PENDING_APPROVAL') "
                    "ORDER BY occurred_at DESC LIMIT 1",
                    row["id"],
                )
                outcomes.append(DebateOutcome(
                    debate_id=row["id"], state=row["state"],
                    termination_reason=row["termination_reason"],
                    candidates_proposed=candidate_count or 0,
                    candidates_passed_layer1=passed_count or 0,
                    detail=detail_row["reason"] if detail_row else None,
                ))
        except Exception as exc:  # noqa: BLE001
            log.error("debate failed for trigger %s: %s", trigger_id, exc)
            errors.append(f"trigger {trigger_id}: {exc}")

    return ScanResponse(
        triggers_found=len(recorded), debates_run=len(outcomes),
        outcomes=outcomes, errors=errors,
    )


class IngestionProcessResponse(BaseModel):
    trace_dir: str
    files_processed: int
    collector: dict
    jobs: dict
    requeued_promotions: Optional[dict] = None
    queued_extractions: Optional[dict] = None


@router.post("/ingestion/process", response_model=IngestionProcessResponse)
async def process_ingestion(
    promote_limit: int = 0,
    extract_limit: int = 0,
    job_limit: int = 500,
    pool=Depends(get_pool),
) -> IngestionProcessResponse:
    """
    Manual loop trigger, same seam as /v1/admin/scan and
    /v1/admin/failure-routes/process above: before this endpoint, the ONLY
    real caller of process_collector_file() / process_pending_jobs() /
    enqueue_pending_claim_promotions() / enqueue_pending_procedure_
    extractions() was scripts/run_ingestion.py -- a hand-run CLI a developer
    had to invoke with tuning flags after every session. Nothing turned
    "user does normal agent work" into "a procedure candidate exists in
    storage" without that manual step. This endpoint is
    scripts/run_ingestion.py's own `_run_once()` body, reachable over HTTP
    instead of a terminal: call it from a cron job, a dashboard button, or
    curl -- whatever calls this on a timer is the seam, exactly as this
    file's own module docstring already states for /scan.

    Drains real collector .jsonl files (STEALTHLAB_TRACE_DIR, else
    <CLAUDE_PROJECT_DIR or cwd>/.claude/traces) into trace_events, then
    drains ingestion_jobs: normalize_trace_event -> observations ->
    promote_observation_to_claim -> claims -> (gated)
    extract_procedure_from_episode -> procedure candidates. Every function
    called is imported unchanged from ingestion_jobs.py / trace_worker.py --
    this endpoint reimplements none of their logic, same discipline
    scripts/bootstrap.py's own docstring states for its sources.

    promote_limit/extract_limit default to 0 (disabled), matching
    run_ingestion.py's own --promote-limit/--extract-limit defaults: each
    promotion re-enqueue costs one real embedding call and each extraction
    costs one real grounded_hybrid_v1 LLM call, so spend stays opt-in and
    caller-bounded, never automatic on a bare sweep. No enforce_limits here
    for the same reason /failure-routes/process has none at its default
    (promote_limit=extract_limit=0): a bare call is pure DB read/recompute/
    write. Passing a positive extract_limit does spend real money -- the
    caller-supplied limit IS the cost control, identical to the CLI flag it
    replaces.
    """
    import os
    from pathlib import Path

    from app.services.ingestion_jobs import (
        enqueue_pending_claim_promotions,
        enqueue_pending_procedure_extractions,
        process_pending_jobs,
    )
    from app.services.trace_worker import process_collector_file

    # Same resolution order as hook_wrapper.py's / run_ingestion.py's own
    # _default_trace_dir() -- duplicated rather than imported, matching
    # run_ingestion.py's own stated reason: these are standalone entry
    # points that should not couple to each other's module.
    env_dir = os.environ.get("STEALTHLAB_TRACE_DIR")
    trace_dir = (
        Path(env_dir) if env_dir
        else Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())) / ".claude" / "traces"
    )

    collector_totals = {"records_seen": 0, "inserted": 0, "skipped_duplicate": 0, "quarantined": 0}
    files = sorted(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []
    for f in files:
        result = await process_collector_file(pool, f)
        for k in collector_totals:
            collector_totals[k] += result.get(k, 0)

    requeued = None
    if promote_limit > 0:
        requeued = await enqueue_pending_claim_promotions(pool, limit=promote_limit)

    extracted = None
    if extract_limit > 0:
        extracted = await enqueue_pending_procedure_extractions(pool, limit=extract_limit)

    job_totals = await process_pending_jobs(pool, limit=job_limit)

    return IngestionProcessResponse(
        trace_dir=str(trace_dir), files_processed=len(files),
        collector=collector_totals, jobs=job_totals,
        requeued_promotions=requeued, queued_extractions=extracted,
    )


class IngestionAutoStatusResponse(BaseModel):
    enabled: bool
    mode: str
    interval_seconds: int
    workspace: Optional[str] = None
    trace_dir: Optional[str] = None
    max_sessions: int
    promote_limit: int
    extract_limit: int
    job_limit: int
    run_count: int
    last_run_started_at: Optional[str] = None
    last_run_completed_at: Optional[str] = None
    last_result: Optional[dict] = None
    last_error: Optional[str] = None
    last_error_at: Optional[str] = None


@router.get("/ingestion/auto-status", response_model=IngestionAutoStatusResponse)
async def ingestion_auto_status(request: Request) -> IngestionAutoStatusResponse:
    """
    Read-only diagnostics for app/services/ingestion_scheduler.py's
    background loop -- lets an operator (or a test) see the loop is alive
    and what its most recent sweep did without reading logs. Populated at
    app startup (main.py's lifespan) regardless of whether the loop is
    enabled, so this endpoint always has something real to report, even
    with INGESTION_AUTO_ENABLED=false.
    """
    state = getattr(request.app.state, "ingestion_scheduler", None)
    if state is None:
        raise HTTPException(500, "ingestion scheduler state not initialized (app not started via lifespan)")
    return IngestionAutoStatusResponse(**state.as_dict())


class RegisterExtractorRequest(BaseModel):
    name: str
    description: str
    kind: str
    version: str
    config: Optional[dict] = None
    scope: Optional[dict] = None
    enable: bool = True


class RegisterExtractorResponse(BaseModel):
    extractor_id: str
    name: str
    version: str
    kind: str
    enabled: bool


@router.post("/extractors", response_model=RegisterExtractorResponse)
async def register_extractor(
    body: RegisterExtractorRequest, pool=Depends(get_pool),
) -> RegisterExtractorResponse:
    """
    The one missing production entry point for
    app.services.procedure_extraction.registry.py's own
    create_extractor_version()/approve_extractor() -- both real, already
    tested functions with, until this endpoint, NO caller outside the
    test suite anywhere in this codebase. Without a live row in
    procedure_extractors, registry.select_extractor() always returns
    None and every real extraction silently falls back to
    deterministic_v1, regardless of whether an LLM client is configured
    -- this is the endpoint that actually lets a real GroundedHybridExtractor
    variant (e.g. grounded_hybrid_v1) become selectable.

    Same seam discipline as every other endpoint in this file: no
    business logic here, just a real caller for two already-real,
    already-tested functions. `enable` defaults True so a single call is
    enough to make the new extractor immediately selectable -- pass
    False to register a candidate for comparison/review first, matching
    approve_extractor's own enable=False option for that case.
    """
    extractor_id = await create_extractor_version(
        pool, name=body.name, description=body.description, kind=body.kind,
        version=body.version, config=body.config, scope=body.scope,
    )
    await approve_extractor(
        pool, extractor_id=extractor_id, approver="admin_api", enable=body.enable,
    )
    return RegisterExtractorResponse(
        extractor_id=extractor_id, name=body.name, version=body.version,
        kind=body.kind, enabled=body.enable,
    )


class ReextractProcedureResponse(BaseModel):
    prior_row_id: str
    procedure_id: str
    new_version_row_id: Optional[str] = None
    new_version: Optional[int] = None
    extracted_by: Optional[str] = None
    abstained: bool = False
    validation_failures: list[str] = []


@router.post("/procedures/{procedure_row_id}/reextract", response_model=ReextractProcedureResponse)
async def reextract_procedure(
    procedure_row_id: str, pool=Depends(get_pool),
    scope_key: str = Depends(enforce_limits),
) -> ReextractProcedureResponse:
    """
    REAL GAP FOUND AND PARTIALLY CLOSED (2026-09-15): this endpoint spends
    a real LLM call (via extract_procedure() -> GroundedHybridExtractor)
    but had no `Depends(enforce_limits)` at all -- a violation of this
    codebase's own hard rule ("Endpoints that spend LLM money take
    Depends(enforce_limits)"). Added the rate-limit + budget-check gate.

    HONEST LIMITATION, not fixed here: `make_cost_recorder(...)` has
    nothing to thread into -- extract_procedure()/GroundedHybridExtractor
    accept no `on_call` hook at all (confirmed by grep: unlike DebateEngine/
    Layer1Evaluator/ChatService, which all do). So `enforce_limits`'s own
    CostGovernor.check_budget() will always see $0 recorded spend for
    THIS call shape specifically -- the request-RATE limit is real and
    enforced, but the dollar cap cannot see this endpoint's real cost yet.
    This is not new to this endpoint: the SAME gap already existed for
    every other real caller of extract_procedure() (find_best_way tier-2,
    report_execution, the background extraction job) before this session
    even started. Wiring a real on_call hook through GroundedHybridExtractor
    is a genuine, separate, larger change (it touches the shared strategy
    class every one of those callers uses) -- flagged, not silently
    left unmentioned.

    The real fix for episodes permanently stuck with a procedure written
    under a silent-fallback tag before ExtractionTransientFailure existed
    (app.services.procedure_extraction.schema): `enqueue_pending_
    procedure_extractions`'s gate excludes any episode with a live
    procedure regardless of which extractor produced it, so those rows
    were otherwise unreachable by any future, better extraction. This
    endpoint re-runs extraction for the SAME source episode against
    whichever extractor is currently selected, and on a real success
    calls `supersede_procedure()` (procedures.py) -- giving that function
    its first real, non-test, non-automatic caller -- to close the old
    row and append the new one. `supersede_procedure` is untouched;
    `create_extractor_version`/`approve_extractor` above are this file's
    own precedent for exposing an existing, well-tested service function
    with no other production entry point.

    Refuses (400) a procedure sourced from zero or more than one episode
    -- build_episode_evidence_source() reads exactly one episode's
    window, the same real constraint handle_extract_procedure_from_episode
    already has.

    On ExtractionTransientFailure (LLM call failed, response didn't
    parse), returns a real error and touches nothing -- the same
    no-silent-fallback guarantee as the background extraction path,
    never a partial/incorrect supersede.
    """
    from app.services.ingestion_jobs import _extraction_client, build_episode_evidence_source
    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.schema import ExtractionTransientFailure
    from app.services.procedures import supersede_procedure

    from app.services.shards import home_pool
    prior = await (await home_pool(pool, "procedure", procedure_row_id, by_row_id=True)).fetchrow(
        "SELECT id, procedure_id, goal, source_episode_ids, owner_id, visibility "
        "FROM procedures WHERE id = $1::uuid AND t_invalid IS NULL",
        procedure_row_id,
    )
    if prior is None:
        raise HTTPException(404, f"no live procedure row {procedure_row_id!r}")

    episode_ids = prior["source_episode_ids"] or []
    if len(episode_ids) != 1:
        raise HTTPException(
            400,
            f"reextract requires a procedure with exactly one source episode "
            f"(found {len(episode_ids)}) -- multi-episode/synthesized procedures "
            f"are not re-extractable this way",
        )

    built = await build_episode_evidence_source(
        pool, str(episode_ids[0]), goal_text=prior["goal"],
    )
    if built is None:
        raise HTTPException(410, f"source episode {episode_ids[0]!r} is gone")
    source, _ep = built

    try:
        result = await extract_procedure(
            pool, source, client=_extraction_client(),
            visibility=prior["visibility"], owner_id=prior["owner_id"],
        )
    except ExtractionTransientFailure as exc:
        raise HTTPException(502, f"extraction failed, nothing changed: {exc}") from exc

    if result.validation_failures:
        return ReextractProcedureResponse(
            prior_row_id=procedure_row_id, procedure_id=prior["procedure_id"],
            extracted_by=result.extracted_by, validation_failures=result.validation_failures,
        )
    if result.abstained:
        return ReextractProcedureResponse(
            prior_row_id=procedure_row_id, procedure_id=prior["procedure_id"],
            extracted_by=result.extracted_by, abstained=True,
        )

    # A real, successful re-extraction: supersede the prior row rather
    # than leaving two live rows -- extract_procedure() already persisted
    # the new candidate as its OWN procedure_id (it has no way to target
    # an existing family), so the real content to carry forward is read
    # back from that fresh row and superseded into place under the
    # ORIGINAL procedure_id/family, then the fresh standalone row is
    # retired (tombstoned, never deleted, this table's own idiom).
    fresh_pool = await home_pool(pool, "procedure", str(result.version_row_id), by_row_id=True)
    fresh = await fresh_pool.fetchrow(
        "SELECT steps, goal, capability_statement, extracted_by, preconditions, "
        "scope, failure_conditions, invariants FROM procedures WHERE id = $1::uuid",
        result.version_row_id,
    )
    superseded = await supersede_procedure(
        pool, prior_row_id=procedure_row_id,
        changed_fields={
            "steps": fresh["steps"], "goal": fresh["goal"],
            "capability_statement": fresh["capability_statement"],
            "extracted_by": fresh["extracted_by"],
            "preconditions": fresh["preconditions"], "scope": fresh["scope"],
            "failure_conditions": fresh["failure_conditions"],
            "invariants": fresh["invariants"],
            "source_episode_ids": episode_ids,
        },
        superseded_by="admin_reextract", reason="re-extraction with the currently-selected extractor",
    )
    await fresh_pool.execute(
        "UPDATE procedures SET t_invalid = now(), verification_state = 'retired' "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        result.version_row_id,
    )
    if superseded is None:
        raise HTTPException(409, "prior row was concurrently superseded/merged; nothing changed")

    return ReextractProcedureResponse(
        prior_row_id=procedure_row_id, procedure_id=superseded["procedure_id"],
        new_version_row_id=str(superseded["id"]), new_version=superseded["version"],
        extracted_by=fresh["extracted_by"],
    )


class IndexLagResponse(BaseModel):
    current_recipe: str
    lag_count: int
    recipe_drift_count: int
    total_stale: int
    sample: list[dict]


@router.get("/index-lag", response_model=IndexLagResponse)
async def index_lag(limit: int = 100, pool=Depends(get_pool)) -> IndexLagResponse:
    """
    Read-only visibility for app.services.index_freshness.get_index_lag()
    -- a real, already-tested function with zero production callers
    before this endpoint (grepped: only referenced in a docstring). Makes
    embedding/retrieval_document staleness observable on demand instead
    of silently accumulating; scripts/run_ingestion.py's `--once` loop
    logs a warning from this same function when `total_stale` crosses a
    threshold. Fixing the drift (scripts/backfill_procedure_embeddings.py)
    stays a deliberate manual/CLI step -- it spends real embedding-API
    calls, so this endpoint reports the problem without silently paying
    to fix it.
    """
    from app.services.index_freshness import get_index_lag

    result = await get_index_lag(pool, limit=limit)
    return IndexLagResponse(**result)


class FailureRouteProcessResponse(BaseModel):
    applied: dict[str, int]


@router.post("/failure-routes/process", response_model=FailureRouteProcessResponse)
async def process_failure_routes(pool=Depends(get_pool)) -> FailureRouteProcessResponse:
    """
    Manual loop trigger, same seam as /v1/admin/scan above: nothing in
    the application ever called
    app.services.procedure_extraction.failure_handlers.run_failure_handlers()
    -- classify_and_route() (app/execution/failures.py, wired into
    procedures.py::record_execution_outcome) appends every failed
    outcome's route durably, but no production caller ever consumed
    fetch_route_queue() to execute the mandated update. This endpoint is
    that consumer: call it from a cron job, a dashboard button, or curl.
    No LLM spend here (no enforce_limits) -- pure DB read/recompute/write.
    Idempotent by construction (each handler's own change_sets ledger
    check): calling this twice in a row performs the same updates only
    once.
    """
    applied = await run_failure_handlers(pool)
    return FailureRouteProcessResponse(applied=applied)
