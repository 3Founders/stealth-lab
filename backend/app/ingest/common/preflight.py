"""Checks a run must pass before it writes anything. Each failure stops the run with the reason; none is skippable
from the command line.

1. Migrations: every control-database migration is applied (core `assert_schema_current`).
2. One embedding space: every stored vector was made by the configured embedding model. On 2026-09-29 two
   processes with different settings wrote gemini-embedding-001 and gemini-embedding-2 vectors into one index;
   the only guard was the vector length, and both are 1024-dimensional. A run now refuses unless the index holds
   exactly the configured model (or nothing yet).
3. One run at a time: a session-level advisory lock, held on a dedicated connection for the whole run.
4. A live queue: goal placement and trace normalization are finished by the job worker. A pending job that has
   waited unclaimed for more than QUEUE_STALE_MINUTES means nobody is draining the queue, and everything this run
   enqueues would sit there too (1,263 placement jobs were found unclaimed in production on 2026-09-29).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

RUN_LOCK_KEY = 0x6B656C5F696E6765   # "kel_inge": one ingestion run per database
QUEUE_STALE_MINUTES = 10

_VECTOR_TABLES = (
    ("goals", "embedding_model_id", "embedding"),
    ("procedures", "embedding_model_id", "embedding"),
    ("knowledge_nodes", "embedding_model_id", "embedding"),
    ("goal_search_index", "embedding_model", "embedding"),
    ("procedure_search_index", "embedding_model", "embedding"),
    ("claim_search_index", "embedding_model", "embedding"),
)


class PreflightFailed(RuntimeError):
    pass


@dataclass
class RunLock:
    conn: Any

    async def release(self) -> None:
        try:
            await self.conn.execute("SELECT pg_advisory_unlock($1)", RUN_LOCK_KEY)
        finally:
            await self.conn.close()


async def check_migrations(pool: Any, *, command: str) -> None:
    from app.ingestion.preflight import PendingMigrations, assert_schema_current

    try:
        await assert_schema_current(pool, command=command, env={})     # the env escape hatch is not honoured here
    except PendingMigrations as exc:
        raise PreflightFailed(str(exc)) from exc


async def stored_embedding_models(pool: Any) -> dict[str, dict[str, int]]:
    """{table: {model: rows with a vector}}; a vector with no recorded model is reported as '(unrecorded)'."""
    out: dict[str, dict[str, int]] = {}
    for table, model_col, vec_col in _VECTOR_TABLES:
        exists = await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
        if not exists:
            continue
        cols = {r["column_name"] for r in await pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = $1", table)}
        if model_col not in cols or vec_col not in cols:
            continue
        rows = await pool.fetch(
            f"SELECT coalesce(nullif({model_col}, 'unknown'), '(unrecorded)') AS m, count(*) AS n "
            f"FROM {table} WHERE {vec_col} IS NOT NULL GROUP BY 1")
        if rows:
            out[table] = {r["m"]: int(r["n"]) for r in rows}
    return out


async def check_embedding_space(pool: Any, configured_model: str) -> dict[str, dict[str, int]]:
    stored = await stored_embedding_models(pool)
    foreign = {t: {m: n for m, n in models.items() if m != configured_model} for t, models in stored.items()}
    foreign = {t: m for t, m in foreign.items() if m}
    if foreign:
        raise PreflightFailed(
            f"the index holds vectors not made by the configured embedding model {configured_model}: {foreign}. "
            "Convert them first (python -m scripts.reembed_to_current_model) or configure the model they were "
            "made with; writing now would mix two vector spaces in one index.")
    return stored


async def acquire_run_lock(dsn: str) -> RunLock:
    import asyncpg

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", RUN_LOCK_KEY)
    if not got:
        await conn.close()
        raise PreflightFailed("another ingestion run holds this database's run lock; one run at a time")
    return RunLock(conn)


async def check_queue_alive(pool: Any, *, stale_minutes: int = QUEUE_STALE_MINUTES) -> dict:
    row = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE created_at < now() - make_interval(mins => $1)) AS stale, "
        "       count(*) AS pending, min(created_at) AS oldest "
        "FROM ingestion_jobs WHERE status = 'pending' AND claimed_at IS NULL", stale_minutes)
    if row and int(row["stale"] or 0) > 0:
        # A backlog behind a working worker is not a dead queue (2026-09-30: goal placement takes ~100 s a job, so
        # a busy worker always has jobs older than 10 minutes). Refuse only when nothing has been claimed lately.
        recent = await pool.fetchval(
            "SELECT count(*) FROM ingestion_jobs WHERE claimed_at > now() - make_interval(mins => $1)", stale_minutes)
        if int(recent or 0) > 0:
            return {"pending": int(row["pending"] or 0), "backlog_older_than_min": int(row["stale"]),
                    "claimed_recently": int(recent)}
        by_type = await pool.fetch(
            "SELECT job_type, count(*) AS n FROM ingestion_jobs WHERE status = 'pending' AND claimed_at IS NULL "
            "AND created_at < now() - make_interval(mins => $1) GROUP BY 1 ORDER BY 2 DESC", stale_minutes)
        raise PreflightFailed(
            f"{row['stale']} queued job(s) have waited over {stale_minutes} minutes unclaimed "
            f"({ {r['job_type']: int(r['n']) for r in by_type} }, oldest {row['oldest']}): no job worker is draining "
            "the queue. Start `python -m app.ingestion.worker` first.")
    return {"pending": int(row["pending"] or 0) if row else 0}


async def run_all(pool: Any, *, dsn: str, command: str, configured_model: str) -> tuple[RunLock, dict]:
    """All checks; returns the held run lock (release it when the run ends) and a report."""
    await check_migrations(pool, command=command)
    stored = await check_embedding_space(pool, configured_model)
    queue = await check_queue_alive(pool)
    lock = await acquire_run_lock(dsn)
    return lock, {"migrations": "current", "embedding_model": configured_model, "stored_vectors": stored,
                  "queue": queue, "run_lock": "held"}
