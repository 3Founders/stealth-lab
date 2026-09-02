"""
Automatic background driver for the learning pipeline: a single
`asyncio.create_task` loop, started in `main.py`'s lifespan and cancelled
on shutdown. No new dependency -- a `while True: sleep; sweep` loop is the
same weight class as the in-process worker `ingestion_jobs.py` already
runs.

TWO MODES (settings.ingestion_auto_mode):

  "local"  -- P0-1, the V1 DEFAULT. Each tick runs
              `app.local_agent.local_learning_sweep.run_local_learning_sweep()`:
              read the local trace collector output
              (.claude/traces/<session>.jsonl) and write PRIVATE candidates
              into the workspace `LocalProcedureStore`. DB-FREE. A raw
              local trace is never uploaded to the global server just
              because auto-learning is enabled -- crossing to the shared
              corpus stays the explicit `publish.py` path.

  "global" -- the shared-substrate path. Each tick calls
              `app.api.admin.process_ingestion()` (the exact function
              `POST /v1/admin/ingestion/process` calls), driving
              trace_events -> observations -> claims -> shared procedures.
              For a deliberate shared/company deployment only; needs a DB.

REUSES, DOES NOT DUPLICATE: neither mode reimplements extraction. "local"
calls the same trace reader + `LocalProcedureStore` writer the one-shot
bootstrap uses; "global" calls the same admin function the REST endpoint
does.

COST CONTROL: unlike the manual endpoint's promote_limit=0/extract_limit=0
safe-by-default, this loop's whole purpose is to actually do bounded work
automatically, so its defaults (settings.ingestion_auto_promote_limit /
ingestion_auto_extract_limit) are small positive numbers, not zero -- but
still bounded and operator-configurable, never unbounded. A bare app start
with an empty trace directory costs nothing (process_ingestion's own DB-only
no-op path); real spend only happens once real collector data exists to
promote/extract from, and even then the loop's caps are the ceiling.

FAILURE SAFETY: one iteration's exception is caught, logged, and recorded in
`last_error` -- never re-raised into the task, which would silently kill the
loop for the rest of the process lifetime (and asyncio does not surface an
unhandled task exception anywhere a human would see it by default). The
user's own agent work happens over separate request paths (ingest.py, MCP
tools) entirely independent of this task, so a stuck or slow ingestion
sweep never blocks it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestionSchedulerState:
    """Read-only-from-the-outside diagnostics for the loop below. One
    instance lives at app.state.ingestion_scheduler for the life of the
    process -- an operator (or a test) reads it to see the loop is alive
    without grepping logs."""

    def __init__(
        self,
        *,
        enabled: bool,
        mode: str,
        interval_seconds: int,
        promote_limit: int,
        extract_limit: int,
        job_limit: int,
        workspace: Optional[str] = None,
        trace_dir: Optional[str] = None,
        max_sessions: int = 5,
    ) -> None:
        self.enabled = enabled
        self.mode = mode
        self.interval_seconds = interval_seconds
        self.promote_limit = promote_limit
        self.extract_limit = extract_limit
        self.job_limit = job_limit
        self.workspace = workspace
        self.trace_dir = trace_dir
        self.max_sessions = max_sessions

        self.run_count = 0
        self.last_run_started_at: Optional[str] = None
        self.last_run_completed_at: Optional[str] = None
        self.last_result: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "interval_seconds": self.interval_seconds,
            "workspace": self.workspace,
            "trace_dir": self.trace_dir,
            "max_sessions": self.max_sessions,
            "promote_limit": self.promote_limit,
            "extract_limit": self.extract_limit,
            "job_limit": self.job_limit,
            "run_count": self.run_count,
            "last_run_started_at": self.last_run_started_at,
            "last_run_completed_at": self.last_run_completed_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
        }


async def _run_local_tick(state: IngestionSchedulerState) -> dict:
    """One local-mode sweep: local traces -> PRIVATE LocalProcedureStore
    candidates. DB-free. Imported here, not at module top, so a bare
    `import app.services.ingestion_scheduler` never pulls the local_agent
    tree in."""
    from app.local_agent.local_learning_sweep import run_local_learning_sweep
    from app.local_agent.local_store import LocalProcedureStore

    workspace = state.workspace or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    trace_dir = state.trace_dir or os.path.join(workspace, ".claude", "traces")
    store = LocalProcedureStore(workspace)
    return run_local_learning_sweep(
        store, trace_dir,
        max_sessions=state.max_sessions,
        workspace_entity_id=os.path.abspath(workspace),
    )


async def _run_global_tick(app: FastAPI, state: IngestionSchedulerState) -> dict:
    """One global-mode tick: the exact function POST
    /v1/admin/ingestion/process calls. Needs app.state.pool."""
    from app.api.admin import process_ingestion

    result = await process_ingestion(
        promote_limit=state.promote_limit,
        extract_limit=state.extract_limit,
        job_limit=state.job_limit,
        pool=app.state.pool,
    )
    return result.model_dump()


async def _loop(app: FastAPI, state: IngestionSchedulerState) -> None:
    while True:
        try:
            await asyncio.sleep(state.interval_seconds)
        except asyncio.CancelledError:
            # Normal shutdown path (app.state.ingestion_scheduler_task
            # cancelled from the lifespan's finally block) -- propagate so
            # the task actually ends rather than looping forever uncancellable.
            raise

        state.run_count += 1
        state.last_run_started_at = _now_iso()
        try:
            if state.mode == "global":
                state.last_result = await _run_global_tick(app, state)
            else:
                state.last_result = await _run_local_tick(state)
            state.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one bad
            # iteration (a transient DB hiccup, a malformed collector file,
            # an extraction call failing) must never end the loop or take
            # down the app; the user's own agent work runs over entirely
            # separate request paths and must never notice this.
            log.exception("automatic ingestion loop iteration failed")
            state.last_error = repr(exc)
            state.last_error_at = _now_iso()
        finally:
            state.last_run_completed_at = _now_iso()


def start(app: FastAPI) -> Optional["asyncio.Task[None]"]:
    """Called once from main.py's lifespan, after app.state.pool exists.
    Reads settings directly (not injected) since this is a process-lifetime
    singleton, same posture as create_pool()/close_pool() in db/session.py.
    Returns the task (also stashed on app.state) so the lifespan can cancel
    it; returns None (and still sets app.state.ingestion_scheduler for the
    status endpoint to read) when INGESTION_AUTO_ENABLED=false, e.g. for
    tests/dev that don't want a bare app start doing any automatic work."""
    from app.config import settings

    state = IngestionSchedulerState(
        enabled=settings.ingestion_auto_enabled,
        mode=settings.ingestion_auto_mode,
        interval_seconds=settings.ingestion_auto_interval_seconds,
        promote_limit=settings.ingestion_auto_promote_limit,
        extract_limit=settings.ingestion_auto_extract_limit,
        job_limit=settings.ingestion_auto_job_limit,
        workspace=settings.ingestion_auto_workspace,
        trace_dir=settings.ingestion_auto_trace_dir,
        max_sessions=settings.ingestion_auto_max_sessions,
    )
    app.state.ingestion_scheduler = state
    app.state.ingestion_scheduler_task = None

    if not state.enabled:
        log.info("automatic learning loop disabled (INGESTION_AUTO_ENABLED=false)")
        return None

    task = asyncio.create_task(_loop(app, state))
    app.state.ingestion_scheduler_task = task
    log.info(
        "automatic learning loop started: mode=%s interval=%ss max_sessions=%s",
        state.mode, state.interval_seconds, state.max_sessions,
    )
    return task


async def stop(app: FastAPI) -> None:
    """Called from main.py's lifespan finally block, before the pool
    closes -- cancelling first means the loop can never fire a query
    against a pool that's mid-close."""
    task = getattr(app.state, "ingestion_scheduler_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
