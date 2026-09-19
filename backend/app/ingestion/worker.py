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


def default_worker_id() -> str:
    return os.environ.get("INGEST_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def is_retryable(exc: BaseException) -> bool:
    """Transient infrastructure/provider failures are retried with backoff;
    contract/data errors are permanent (retrying cannot help). Unknown errors
    are retryable until ``max_attempts`` is exhausted."""
    from app.services.embeddings import EmbeddingError
    from app.services.semantic.errors import SemanticJudgmentUnavailable
    from app.services.shards import NoWritableShard, ShardUnavailable

    if isinstance(exc, (q.ScopeError, KeyError, NoWritableShard)):
        return False  # bad payload / refused scope / nowhere to write: retrying cannot help
    try:
        from app.services.goals import GoalQualityRejected
        from app.services.v0_gate import V0Violation

        if isinstance(exc, (GoalQualityRejected, V0Violation)):
            return False
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, (SemanticJudgmentUnavailable, ShardUnavailable, EmbeddingError, asyncio.TimeoutError, TimeoutError,
                        ConnectionError, OSError, asyncpg.PostgresConnectionError, asyncpg.TooManyConnectionsError,
                        asyncpg.CannotConnectNowError, asyncpg.DeadlockDetectedError, asyncpg.SerializationError)):
        return True
    if isinstance(exc, (ValueError, TypeError, AssertionError, NotImplementedError)):
        return False
    return True


class Worker:
    def __init__(self, pool: asyncpg.Pool, cfg: WorkerConfig, *, worker_id: Optional[str] = None,
                 job_types: Optional[list[str]] = None, handlers: Optional[dict[str, Any]] = None, pools: Any = None):
        self.pool, self.cfg = pool, cfg
        self.worker_id = worker_id or default_worker_id()
        self.job_types = job_types
        if handlers is None:
            import app.ingestion.handlers  # noqa: F401 -- registers ingest_candidate_bundle
            from app.services.ingestion_jobs import JOB_HANDLERS

            handlers = JOB_HANDLERS
        self.handlers = handlers
        self.pools = pools
        self.stop = asyncio.Event()
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
        # explicit scope travels with the work; handlers that understand it use it
        payload.setdefault("_job", {"id": job.id, "attempt": job.attempt, "idempotency_key": job.idempotency_key,
                                    "scope_type": job.scope_type, "scope_entity_id": job.scope_entity_id,
                                    "owner_id": job.owner_id, "visibility": job.visibility})

        lease_lost = asyncio.Event()
        hb = asyncio.create_task(self._heartbeat(job, lease_lost))
        run = asyncio.create_task(handler(self.pool, payload))
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
        await q.reap_exhausted(self.pool)
        await asyncio.gather(*[self._lane(i, budget) for i in range(self.cfg.concurrency)])
        await q.reap_exhausted(self.pool)
        if self.cfg.drain_projections:
            from app.services.search_projection import drain_outbox

            self.counts["projection"] = await drain_outbox(self.pool, batch=self.cfg.projection_batch, pools=self.pools)
        return dict(self.counts, worker_id=self.worker_id)


def _parse(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.ingestion.worker", description=__doc__.split("\n\n")[0])
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--once", action="store_true", help="process runnable jobs, then exit (0 = ok, 1 = a job failed permanently)")
    m.add_argument("--loop", action="store_true", help="poll forever (SIGTERM/SIGINT stop gracefully)")
    m.add_argument("--validate-config", action="store_true", help="check environment and exit")
    p.add_argument("--concurrency", type=int)
    p.add_argument("--max-jobs", type=int)
    p.add_argument("--job-types", help="comma-separated job_type filter")
    p.add_argument("--worker-id")
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    cfg = WorkerConfig.from_env()
    over = {k: v for k, v in (("concurrency", args.concurrency), ("max_jobs", args.max_jobs)) if v}
    if over:
        from dataclasses import replace
        cfg = replace(cfg, **over)
    problems = validate_startup()
    if problems:
        for pr in problems:
            print(f"CONFIG ERROR: {pr}", file=sys.stderr)
        return 2
    if args.validate_config:
        print("config ok")
        return 0
    from app.db.session import create_pool

    pool = await create_pool(control_database_url(), max_size=cfg.concurrency + 3)
    from app.services.shards import ShardPools

    worker = Worker(pool, cfg, worker_id=args.worker_id, pools=ShardPools(pool),
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
    return 1 if result.get("failed") else 0


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.exit(asyncio.run(_amain(_parse(argv))))


if __name__ == "__main__":
    main()
