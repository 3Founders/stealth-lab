"""The ONE ingestion worker. Runs identically on Cloud Run, GitHub Actions, an
Oracle VM or a laptop (docs/distributed_ingestion.md):

    python -m app.ingestion.worker --once            # drain what is runnable, then exit
    python -m app.ingestion.worker --loop            # long-running service
    python -m app.ingestion.worker --once --max-jobs 500 --concurrency 8 --job-types ingest_skill_package

A worker: leases a job -> heartbeats while running -> runs the registered
handler (app.services.ingestion_jobs.JOB_HANDLERS) -> completes or fails it
(fenced) -> drains the search-projection outbox. Crash at any point is safe:
the lease expires and another worker retries; handlers and canonical writes are
idempotent (unique keys, upserts, durable identity decisions).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
import time
import uuid
from typing import Any, Optional

import asyncpg

from app.ingestion import queue as q
from app.ingestion.config import WorkerConfig, control_database_url, validate_startup

log = logging.getLogger("ingestion.worker")
EXIT_BUDGET = 3   # stopped by the daily model budget (work remains queued); distinct from 1 = job failed, 2 = config


def default_worker_id() -> str:
    return os.environ.get("INGEST_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def is_retryable(exc: BaseException) -> bool:
    """Transient infrastructure/provider failures are retried with backoff;
    contract/data errors are permanent (retrying cannot help). Unknown errors
    are retryable until ``max_attempts`` is exhausted."""
    from app.services.embeddings import EmbeddingError
    from app.services.goal_abstraction import GoalRelationDependencyError
    from app.services.semantic.errors import SemanticJudgmentUnavailable
    from app.services.object_storage import ObjectStoreCorrupt, ObjectStoreUnavailable, PayloadTooLarge
    from app.services.shards import NoWritableShard, ShardUnavailable

    if isinstance(exc, ObjectStoreUnavailable):
        return True
    if isinstance(exc, (ObjectStoreCorrupt, PayloadTooLarge, q.ScopeError, KeyError, NoWritableShard)):
        return False  # bad payload / refused scope / nowhere to write: retrying cannot help
    try:
        from app.services.goals import GoalQualityRejected
        from app.services.v0_gate import V0Violation

        if isinstance(exc, (GoalQualityRejected, V0Violation)):
            return False
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, (SemanticJudgmentUnavailable, GoalRelationDependencyError, ShardUnavailable, EmbeddingError,
                        asyncio.TimeoutError, TimeoutError, ConnectionError, OSError, asyncpg.PostgresConnectionError,
                        asyncpg.TooManyConnectionsError, asyncpg.CannotConnectNowError, asyncpg.DeadlockDetectedError,
                        asyncpg.SerializationError)):
        return True
    if isinstance(exc, (ValueError, TypeError, AssertionError, NotImplementedError)):
        return False
    return True


class Worker:
    def __init__(self, pool: asyncpg.Pool, cfg: WorkerConfig, *, worker_id: Optional[str] = None,
                 job_types: Optional[list[str]] = None, handlers: Optional[dict[str, Any]] = None, pools: Any = None,
                 service: Any = None):
        self.pool, self.cfg = pool, cfg
        self.service = service   # verified ServiceAuthContext (None only in TEST)
        self.worker_id = worker_id or default_worker_id()
        self.job_types = job_types
        if handlers is None:
            import app.ingestion.handlers  # noqa: F401 -- registers ingest_candidate_bundle
            from app.services import benchmark_transfer
            from app.services.ingestion_jobs import JOB_HANDLERS

            handlers = JOB_HANDLERS
            if handlers.get(benchmark_transfer.JOB_TYPE) is not benchmark_transfer.handle_benchmark_transfer:
                handlers[benchmark_transfer.JOB_TYPE] = benchmark_transfer.handle_benchmark_transfer
        self.handlers = handlers
        self.pools = pools
        self.stop = asyncio.Event()
        self.budget = None            # ingest_budget.IngestBudget once run() installs it
        self.budget_stopped = False   # True once the daily model budget stopped this worker leasing
        self.counts = {"done": 0, "retryable_failed": 0, "failed": 0, "lost": 0, "leased": 0}

    # -------------------------------------------------------------- one job

    async def run_job(self, job: q.Job) -> str:
        """Run one leased job to a terminal/queued state. Returns the status."""
        self.counts["leased"] += 1
        handler = self.handlers.get(job.job_type)
        if handler is None:
            return await self._fail(job, f"no handler registered for job_type={job.job_type!r}", retryable=False)
        try:
            q.validate_scope(job.job_type, job.scope_type, job.visibility, job.owner_id)
        except q.ScopeError as exc:
            return await self._fail(job, f"scope refused: {exc}", retryable=False)

        payload = dict(job.payload)
        try:
            payload["_job"] = q.trusted_job_metadata(job)
        except ValueError as exc:
            return await self._fail(job, str(exc), retryable=False)

        lease_lost = asyncio.Event()
        hb = asyncio.create_task(self._heartbeat(job, lease_lost))

        async def _run():
            from app.services.object_storage import authorized_hydrate, hydrate_payload
            if self.service is None:
                return await handler(self.pool, await hydrate_payload(payload))
            from app.ingestion.queue import job_authority_from_row
            from app.services.authorization import ObjectRef

            ctx = self.service.bind_job(job_authority_from_row({
                "owner_id": job.owner_id, "visibility": job.visibility,
                "auth_tenant_id": job.scope_entity_id if job.visibility == "org" else None}))
            obj = ObjectRef(job.visibility, owner_id=job.owner_id,
                            tenant_id=job.scope_entity_id if job.visibility == "org" else None)
            return await handler(self.pool, await authorized_hydrate(payload, ctx, obj))   # blob refs -> content (sha256 verified)

        run = asyncio.create_task(_run())
        lost = asyncio.create_task(lease_lost.wait())
        try:
            done, _ = await asyncio.wait({run, lost}, timeout=self.cfg.job_timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
            if run in done:
                exc = run.exception()
                if exc is None:
                    ok = await q.complete(self.pool, job)
                    self.counts["done" if ok else "lost"] += 1
                    if not ok:
                        log.warning("job %s finished but its lease was lost; result is idempotent, discarding state write", job.id)
                    return "done" if ok else "lost"
                from app.services.governance import BudgetExceeded

                if isinstance(exc, BudgetExceeded):   # cost stop, not a job failure: hand it back, keep the attempt
                    log.warning("job %s handed back: %s", job.id, exc)
                    await q.release(self.pool, job)
                    self.counts["budget_released"] = self.counts.get("budget_released", 0) + 1
                    self.budget_stopped = True
                    return "released"
                log.warning("job %s (%s) attempt %d failed: %r", job.id, job.job_type, job.attempt, exc)
                return await self._fail(job, repr(exc), retryable=is_retryable(exc))
            run.cancel()
            reason = "lease lost to another worker" if lost in done else f"timed out after {self.cfg.job_timeout_seconds}s"
            if lost in done:
                self.counts["lost"] += 1
                return "lost"
            return await self._fail(job, reason, retryable=True)
        except asyncio.CancelledError:  # graceful shutdown: hand the job back
            run.cancel()
            await q.release(self.pool, job)
            raise
        finally:
            for t in (hb, lost):
                t.cancel()
            await asyncio.gather(hb, lost, run, return_exceptions=True)

    async def _fail(self, job: q.Job, error: str, *, retryable: bool) -> str:
        status = await q.fail(self.pool, job, error, retryable=retryable, base=self.cfg.retry_base_seconds,
                              cap=self.cfg.retry_cap_seconds)
        self.counts[status] = self.counts.get(status, 0) + 1
        return status

    async def _heartbeat(self, job: q.Job, lost: asyncio.Event) -> None:
        interval = max(1.0, self.cfg.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                if not await q.heartbeat(self.pool, job, self.cfg.lease_seconds):
                    lost.set()
                    return
            except Exception:  # noqa: BLE001 -- a transient DB blip must not kill the job; the lease will tell
                log.warning("heartbeat for job %s failed", job.id, exc_info=True)

    # --------------------------------------------------------------- loops

    async def _lane(self, lane: int, budget: dict) -> None:
        idle_since: Optional[float] = None
        while not self.stop.is_set():
            if self.cfg.max_jobs and budget["taken"] >= self.cfg.max_jobs:
                return
            if self.budget is not None:
                st = await self.budget.status()
                if st.state in ("exceeded", "unavailable"):   # never lease expensive work we cannot pay for
                    if not self.budget_stopped:
                        log.error("model budget %s ($%.2f of $%.2f): not leasing new jobs", st.state, st.spent_usd, st.cap_usd)
                    self.budget_stopped = True
                    if not budget["loop"]:
                        return
                    try:
                        await asyncio.wait_for(self.stop.wait(), timeout=max(self.cfg.poll_seconds, 30.0))
                    except asyncio.TimeoutError:
                        pass
                    continue
                self.budget_stopped = False
            jobs = await q.lease(self.pool, self.worker_id, limit=1, lease_seconds=self.cfg.lease_seconds, job_types=self.job_types)
            if not jobs:
                if not budget["loop"]:
                    return
                idle_since = idle_since or time.monotonic()
                if self.cfg.idle_exit_seconds and time.monotonic() - idle_since >= self.cfg.idle_exit_seconds:
                    return
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.cfg.poll_seconds)
                except asyncio.TimeoutError:
                    pass
                continue
            idle_since = None
            budget["taken"] += 1
            await self.run_job(jobs[0])

    async def run(self, *, loop: bool) -> dict:
        budget = {"taken": 0, "loop": loop}
        if self.budget is None and os.environ.get("INGEST_BUDGET_ENFORCE", "1") not in ("0", "false", "False"):
            from app.services import ingest_budget

            self.budget = ingest_budget.install(self.pool)   # same ledger + cap as the API's CostGovernor
        await q.reap_exhausted(self.pool)
        await asyncio.gather(*[self._lane(i, budget) for i in range(self.cfg.concurrency)])
        await q.reap_exhausted(self.pool)
        if self.cfg.reconcile_goals:
            from app.ingestion.handlers import Dependencies
            from app.services.identity_resolution import reconcile_goals

            try:
                self.counts['reconcile'] = await reconcile_goals(
                    self.pool, judge=Dependencies.get_judge(),
                    window_minutes=self.cfg.reconcile_window_minutes)
            except Exception:  # noqa: BLE001 -- retried next run; never fails the batch
                log.warning('goal reconciliation failed; will retry next run', exc_info=True)
            from app.services.identity_resolution import enqueue_missing_goal_placements

            try:
                self.counts['placement_repair'] = await enqueue_missing_goal_placements(self.pool)
            except Exception:  # noqa: BLE001 -- retried next run; never fails the batch
                log.warning('goal placement repair failed; will retry next run', exc_info=True)
        from app.services.shard_capacity import enforce_shard_capacity

        try:   # storage guard: full shards stop taking NEW objects before the provider refuses writes
            capacity = await enforce_shard_capacity(self.pool, apply=True)
            self.counts['shards_marked_full'] = sum(1 for row in capacity if row.get("action") == "marked_full")
        except Exception:  # noqa: BLE001 -- retried next run; never fails the batch
            log.warning('shard capacity check failed; will retry next run', exc_info=True)
        if self.cfg.reconcile_claims:
            from app.ingestion.handlers import Dependencies
            from app.services.claim_identity import reconcile_claims

            try:
                self.counts['claim_reconcile'] = await reconcile_claims(
                    self.pool, embedder=Dependencies.get_embedder(self.pool), judge=Dependencies.get_judge())
            except Exception:  # noqa: BLE001 -- retried next run; never fails the batch
                log.warning('claim reconciliation failed; will retry next run', exc_info=True)
        if self.cfg.drain_projections:
            from app.services.search_projection import drain_outbox

            self.counts["projection"] = await drain_outbox(self.pool, batch=self.cfg.projection_batch, pools=self.pools)
        return dict(self.counts, worker_id=self.worker_id, budget_stopped=self.budget_stopped)


def _parse(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.ingestion.worker", description=__doc__.split("\n\n")[0])
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--once", action="store_true", help="process runnable jobs, then exit (0 = ok, 1 = a job failed permanently, 3 = daily model budget reached)")
    m.add_argument("--loop", action="store_true", help="poll forever (SIGTERM/SIGINT stop gracefully)")
    m.add_argument("--validate-config", action="store_true", help="check environment and exit")
    p.add_argument("--concurrency", type=int)
    p.add_argument("--max-jobs", type=int)
    p.add_argument("--job-types", help="comma-separated job_type filter")
    p.add_argument("--worker-id")
    return p.parse_args(argv)


async def authenticate_worker(pool: asyncpg.Pool) -> Optional[Any]:
    """Verify this worker's service credential (INGEST_SERVICE_TOKEN) and return
    its ServiceAuthContext, or None when authentication is not required (TEST
    with no service-token config). Outside TEST the worker REFUSES to start
    without a valid credential holding ingestion:process: a worker is a
    registered, revocable, least-privilege service, not "whoever holds the
    database URL". Credentials expire (see SERVICE_TOKEN_MAX_TTL_SECONDS), so
    long-running workers re-verify on a timer (Worker.reauth)."""
    from app.config import settings
    from app.services import auth_context as ac
    from app.services.service_identity import PgServiceRegistry, ServiceTokenConfig, ServiceTokenRejected, verify_service_token

    cfg = ServiceTokenConfig.from_settings(settings)
    token = os.environ.get("INGEST_SERVICE_TOKEN")
    if cfg is None or not token:
        if settings.is_test:
            return None
        raise SystemExit("CONFIG ERROR: INGEST_SERVICE_TOKEN and SERVICE_TOKEN_* are required outside TEST")
    try:
        ctx = await verify_service_token(token, config=cfg, registry=PgServiceRegistry(pool, ttl=0.0))
    except ServiceTokenRejected as exc:
        raise SystemExit(f"CONFIG ERROR: worker credential rejected ({exc.reason})") from None
    if not ctx.has_scope(ac.INGESTION_PROCESS):
        raise SystemExit("CONFIG ERROR: worker credential lacks scope ingestion:process")
    return ctx


async def _amain(args: argparse.Namespace) -> int:
    cfg = WorkerConfig.from_env()
    over = {k: v for k, v in (("concurrency", args.concurrency), ("max_jobs", args.max_jobs)) if v}
    if over:
        from dataclasses import replace
        cfg = replace(cfg, **over)
    requested_job_types = {t.strip() for t in args.job_types.split(",")} if args.job_types else set()
    # The production Cloud Run job intentionally has no job-type filter and
    # can lease skill packages. Refuse startup before leasing anything when
    # the extraction client is absent; otherwise jobs are marked done while
    # artifacts are silently recorded without procedures.
    requires_skill_extraction = not requested_job_types or "ingest_skill_package" in requested_job_types
    problems = validate_startup(require_skill_extraction=requires_skill_extraction)
    if problems:
        for pr in problems:
            print(f"CONFIG ERROR: {pr}", file=sys.stderr)
        return 2
    if args.validate_config:
        print("config ok")
        return 0
    from app import observability

    observability.init("worker")   # OTel + Sentry when configured; never raises, never blocks ingestion
    from app.db.session import create_pool

    # each in-flight bundle holds the advisory-lock connection AND needs a second one for its writes
    pool = await create_pool(control_database_url(), max_size=2 * cfg.concurrency + 4)
    from app.services.shards import ShardPools

    service = await authenticate_worker(pool)
    worker_id = args.worker_id
    if service is not None:
        worker_id = f"{service.service_id}:{args.worker_id or default_worker_id()}"
        logging.getLogger().info("worker authenticated as service %s (credential %s)", service.service_id, service.credential_id)
    worker = Worker(pool, cfg, worker_id=worker_id, pools=ShardPools(pool), service=service,
                    job_types=[t.strip() for t in args.job_types.split(",")] if args.job_types else None)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: worker.stop.set())
    result = await worker.run(loop=args.loop)
    logging.getLogger().info("worker finished: %s", result)
    print(result)
    await pool.close()
    try:
        from app import telemetry

        telemetry.shutdown()   # flush spans before a short-lived (Cloud Run / Actions) process exits
    except Exception:  # noqa: BLE001
        pass
    if result.get("failed"):
        return 1
    return EXIT_BUDGET if result.get("budget_stopped") else 0


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.exit(asyncio.run(_amain(_parse(argv))))


if __name__ == "__main__":
    main()
