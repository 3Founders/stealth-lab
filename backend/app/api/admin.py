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

from app.api.deps import enforce_limits, make_cost_recorder
from app.debate.panel import default_judge, default_layer2_agent, default_panel
from app.services.loop import LoopOrchestrator
from app.services.procedure_extraction.failure_handlers import run_failure_handlers
from app.services.triggers import ThresholdRule, TriggerDetector

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin"])

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
