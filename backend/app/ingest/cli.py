"""Run an ingestion pipeline.

    python -m app.ingest.cli openhands --target local --dsn-env KEL_INGEST_DSN --max-usd 2 --limit 10
    python -m app.ingest.cli skills    --target local --dsn-env KEL_INGEST_DSN --max-usd 2 --limit 10
    python -m app.ingest.cli openhands --target production --approved-by "Anuj" --max-usd 20

Every run: resolves and binds its target (common/target.py), passes the preflight (migrations, one embedding
space, a live job queue, the run lock), proves the extraction models answer, installs a hard model-spend cap
(`--max-usd`, required: there is no default spend), records itself in `ingestion_runs`, then prints one JSON
report built from the ledger. Exit code 0 = the run completed (items may still be rejected or failed; the report
says which and why), 2 = refused before writing anything, 3 = stopped by the spend cap.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.ingest.common.parallel import DEFAULT_CONCURRENCY

PIPELINES = ("openhands", "skills", "verified")


def _parse(argv: Optional[list[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.ingest.cli")
    p.add_argument("pipeline", choices=PIPELINES)
    p.add_argument("--target", required=True, choices=("local", "production"))
    p.add_argument("--dsn-env", help="local target: NAME of the env var holding the local DSN")
    p.add_argument("--approved-by", help="production target: who approved this run")
    p.add_argument("--max-usd", type=float, required=True, help="hard model-spend cap for the rolling 24 hours")
    p.add_argument("--limit", type=int, default=None, help="process at most N new items")
    p.add_argument("--instances", default=None, help="openhands: comma-separated task ids only")
    p.add_argument("--outcomes", default="resolved,failed", help="openhands: resolved,failed")
    p.add_argument("--sources", default=None,
                   help="verified: comma-separated subset of swe-rebench,swe-rebench-v2,swe-bench-extra,swe-gym")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help="items (tasks / repositories) processed at once; runs of one task stay in order")
    return p.parse_args(argv)


async def _spend_since(pool: Any, since: datetime) -> Optional[float]:
    from app.services.shards import search_pool

    sp = await search_pool(pool)
    try:
        value = await sp.fetchval("SELECT coalesce(sum(estimated_cost), 0) FROM llm_spend WHERE occurred_at >= $1",
                                  since)
        return round(float(value or 0), 4)
    except Exception:  # noqa: BLE001 -- the report says unknown rather than zero
        return None


async def _amain(a: argparse.Namespace) -> int:
    # Every blocking model call runs in the default thread pool, which Python sizes to cpu_count + 4 (8 on a
    # 4-core laptop) -- a hidden cap below --concurrency, since each unit can hold several calls at once.
    from concurrent.futures import ThreadPoolExecutor

    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=max(32, 4 * a.concurrency)))
    from app.db.session import create_pool
    from app.ingest.common import preflight
    from app.ingest.common.ledger import Ledger
    from app.ingest.common.llm import ModelUnavailable, extraction_client, probe
    from app.ingest.common.target import TargetRefused, assert_shards_local, bind_local, resolve_target
    from app.services import ingest_budget
    from app.services.embeddings import Embedder
    from app.utils.aio import run_blocking

    try:
        target = resolve_target(a.target, dsn_env=a.dsn_env, approved_by=a.approved_by)
        bind_local(target)
    except TargetRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if a.max_usd <= 0:
        print("REFUSED: --max-usd must be positive", file=sys.stderr)
        return 2

    # Every parallel unit holds a connection at times; a few spare for the ledger, the budget and the preflight.
    pool = await create_pool(target.dsn, min_size=1, max_size=a.concurrency + 4)
    lock = None
    try:
        try:
            if target.name == "local":
                await assert_shards_local(pool)
            lock, checks = await preflight.run_all(pool, dsn=target.dsn, command=f"ingest {a.pipeline}",
                                                   configured_model=Embedder().embedding_model_id())
            from app.config import settings

            from app.ingest.common.llm import ingest_model

            models = [ingest_model()]
            checks["models"] = await run_blocking(probe, extraction_client(models[0]), models)
        except (TargetRefused, preflight.PreflightFailed, ModelUnavailable) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2

        run_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        spec = {"pipeline": a.pipeline, **target.describe(), "limit": a.limit, "max_usd": a.max_usd}
        await pool.execute("INSERT INTO ingestion_runs (run_id, started_at, source_spec, created_by) "
                           "VALUES ($1::uuid, $2, $3::jsonb, $4)", run_id, started, spec, f"ingest:{a.pipeline}")
        ingest_budget.install(pool, cap_usd=a.max_usd)
        ledger = Ledger(pool, pipeline=a.pipeline, run_id=run_id, target=target.name)
        try:
            result = await _resuming(pool, run_id, a, lambda limit: _dispatch(pool, ledger, a, limit))
        finally:
            ingest_budget.uninstall()

        pending = await pool.fetch(
            "SELECT job_type, count(*) AS n FROM ingestion_jobs WHERE status = 'pending' AND created_at >= $1 "
            "GROUP BY 1", started)
        report = {"run_id": run_id, **spec, "preflight": checks, "result": result,
                  "ledger": await ledger.counts(), "model_spend_usd": await _spend_since(pool, started),
                  "jobs_enqueued_pending": {r["job_type"]: int(r["n"]) for r in pending},
                  "wall_s": round(time.monotonic() - t0, 1)}
        await pool.execute("UPDATE ingestion_runs SET finished_at = now(), metrics = $2::jsonb WHERE run_id = $1::uuid",
                           run_id, json.loads(json.dumps(report, default=str)))
        print(json.dumps(report, indent=2, default=str))
        return 3 if result.get("stopped") else 0
    finally:
        if lock is not None:
            await lock.release()
        await pool.close()


def main(argv: Optional[list[str]] = None) -> None:
    raise SystemExit(asyncio.run(_amain(_parse(argv))))


async def _dispatch(pool: Any, ledger: Any, a: Any, limit: Optional[int]) -> dict:
    """Run the chosen pipeline once (the ledger makes a re-run skip every item already decided)."""
    if a.pipeline == "openhands":
        from app.ingest.openhands import pipeline as oh

        return await oh.run(pool, ledger=ledger, limit=limit, concurrency=a.concurrency,
                          instances=set(a.instances.split(",")) if a.instances else None,
                          outcomes=tuple(o.strip() for o in a.outcomes.split(",") if o.strip()))
    elif a.pipeline == "verified":
        from app.ingest.verified import pipeline as vf
        from app.ingest.verified.sources import ORDER

        return await vf.run(pool, ledger=ledger, limit=limit, concurrency=a.concurrency,
                          sources=tuple(a.sources.split(",")) if a.sources else ORDER,
                          instances=set(a.instances.split(",")) if a.instances else None)
    else:
        from app.ingest.skills import pipeline as sk

        return await sk.run(pool, ledger=ledger, limit=limit, concurrency=a.concurrency)


# Transient failures of the connection to the database or the network (2026-09-30: a production run died on
# "WinError 1236: the network connection was aborted by the local system" an hour in). The ledger records every
# decided item and the writes are idempotent (shared task Goal, procedure dedup), so the safe response is to wait,
# reconnect and resume -- never to guess. A run that keeps failing still stops.
_RESUME_ATTEMPTS = 8
_RESUME_WAIT_S = (30, 60, 120, 300, 300, 300, 300, 300)


def _transient(exc: BaseException) -> bool:
    import asyncpg

    return isinstance(exc, (ConnectionError, TimeoutError, asyncpg.InterfaceError,
                            asyncpg.PostgresConnectionError, asyncpg.exceptions.ConnectionDoesNotExistError)) or (
        isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError))


async def _resuming(pool: Any, run_id: str, a: Any, once: Any) -> dict:
    """`once(limit)`, resumed after transient connection failures. With --limit, items this run already decided
    count against it, so a resume never processes more than asked."""
    for attempt in range(_RESUME_ATTEMPTS + 1):
        limit = a.limit
        try:
            if a.limit is not None:
                done = await pool.fetchval("SELECT count(*) FROM ingest_ledger WHERE run_id = $1::uuid", run_id)
                limit = max(0, a.limit - int(done or 0))
            return await once(limit)
        except Exception as exc:  # noqa: BLE001 -- only transient ones are resumed; everything else is re-raised
            if not _transient(exc) or attempt == _RESUME_ATTEMPTS:
                raise
            wait = _RESUME_WAIT_S[min(attempt, len(_RESUME_WAIT_S) - 1)]
            print(f"transient failure ({type(exc).__name__}: {str(exc)[:200]}); resuming in {wait}s "
                  f"(attempt {attempt + 1}/{_RESUME_ATTEMPTS})", file=sys.stderr, flush=True)
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    main()
