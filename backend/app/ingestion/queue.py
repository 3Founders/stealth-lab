"""Durable lease queue on the existing ``ingestion_jobs`` table.

Status mapping (legacy names kept so existing code and tests keep working):

    pending           -> pending
    processing        -> leased/running   (``lease_until`` > now)
    retryable_failed  -> retry after ``run_after`` (backoff)
    done              -> completed
    failed            -> permanent_failed
    cancelled         -> cancelled

Delivery is AT-LEAST-ONCE. Handlers are idempotent (canonical writes use unique
keys + upserts), so duplicate delivery, lease expiry and zombie workers are
safe; the queue additionally FENCES state transitions so a zombie can never
overwrite the state a newer attempt wrote: complete/fail/heartbeat only apply
while ``claimed_by`` == this worker AND ``attempts`` == the attempt this worker
was given.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import asyncpg

SCOPE_TYPES = ("global", "organization", "team", "project", "repository", "branch", "user", "session", "task", "entity")
# Handlers that write PUBLIC/GLOBAL canonical knowledge: a job for them must be
# explicitly global+public, or it is refused (no worker publishes private
# knowledge globally just because it is processing the source).
PUBLIC_ONLY_JOB_TYPES = frozenset({"ingest_skill_package"})


@dataclass
class Job:
    id: int
    job_type: str
    payload: dict
    attempt: int
    max_attempts: int
    worker_id: str
    scope_type: Optional[str] = None
    scope_entity_id: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: Optional[str] = None
    idempotency_key: Optional[str] = None
    source_id: Optional[str] = None
    config_version: Optional[str] = None


class ScopeError(ValueError):
    pass


def validate_scope(job_type: str, scope_type: Optional[str], visibility: Optional[str], owner_id: Optional[str]) -> None:
    if scope_type is not None and scope_type not in SCOPE_TYPES:
        raise ScopeError(f"unknown scope_type {scope_type!r}")
    if visibility is not None and visibility not in ("public", "private", "org"):
        raise ScopeError(f"unknown visibility {visibility!r}")
    if visibility in ("private", "org") and not owner_id:
        raise ScopeError("private/org jobs must name an owner_id")
    if job_type in PUBLIC_ONLY_JOB_TYPES and not (scope_type in (None, "global") and visibility in (None, "public")):
        raise ScopeError(f"{job_type} writes global public knowledge; refusing scope={scope_type!r} visibility={visibility!r}")


async def enqueue(
    pool: asyncpg.Pool, job_type: str, payload: dict, *, idempotency_key: str, source_id: Optional[str] = None,
    scope_type: str, scope_entity_id: Optional[str] = None, owner_id: Optional[str] = None, visibility: str = "public",
    config_version: Optional[str] = None, max_attempts: int = 5, offload: bool = True,
) -> tuple[int, bool]:
    """Insert a job unless (job_type, idempotency_key) already exists. Returns
    (job_id, created). Explicit scope is REQUIRED: a queued job can never be
    ambiguous about who may see what it produces."""
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    validate_scope(job_type, scope_type, visibility, owner_id)
    if offload:      # large raw strings go to object storage; the queue row keeps a locator + sha256
        from app.services.object_storage import offload_payload
        payload = await offload_payload(pool, payload)
    row = await pool.fetchrow(
        "INSERT INTO ingestion_jobs (job_type, payload, idempotency_key, source_id, scope_type, scope_entity_id, "
        "owner_id, visibility, config_version, max_attempts) VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, $8, $9, $10) "
        "ON CONFLICT (job_type, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING id",
        job_type, payload, idempotency_key, source_id, scope_type, scope_entity_id, owner_id, visibility, config_version,
        max_attempts)
    if row:
        return row["id"], True
    existing = await pool.fetchval(
        "SELECT id FROM ingestion_jobs WHERE job_type = $1 AND idempotency_key = $2", job_type, idempotency_key)
    return existing, False


_LEASE_SQL = """
WITH c AS (
    SELECT id FROM ingestion_jobs
    WHERE ($3::text[] IS NULL OR job_type = ANY($3::text[]))
      AND attempts < max_attempts
      AND (
            (status = 'pending' AND (run_after IS NULL OR run_after <= now()))
         OR (status = 'retryable_failed' AND (run_after IS NULL OR run_after <= now()))
         OR (status = 'processing' AND lease_until IS NOT NULL AND lease_until < now())
      )
    ORDER BY id
    LIMIT $2
    FOR UPDATE SKIP LOCKED
)
UPDATE ingestion_jobs j SET
    status = 'processing', claimed_by = $1, claimed_at = now(), started_at = COALESCE(j.started_at, now()),
    lease_until = now() + make_interval(secs => $4), attempts = j.attempts + 1, completed_at = NULL
FROM c WHERE j.id = c.id
RETURNING j.id, j.job_type, j.payload, j.attempts, j.max_attempts, j.scope_type, j.scope_entity_id, j.owner_id,
          j.visibility, j.idempotency_key, j.source_id, j.config_version
"""


async def lease(
    pool: asyncpg.Pool, worker_id: str, *, limit: int = 1, lease_seconds: int = 300, job_types: Optional[Sequence[str]] = None,
) -> list[Job]:
    """Atomically take up to ``limit`` runnable jobs (pending, due retries, and
    jobs whose lease EXPIRED because their worker died). The attempt counter
    increments at lease time so a crash still counts against ``max_attempts``."""
    rows = await pool.fetch(_LEASE_SQL, worker_id, limit, list(job_types) if job_types else None, float(lease_seconds))
    jobs = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        jobs.append(Job(r["id"], r["job_type"], payload, r["attempts"], r["max_attempts"], worker_id, r["scope_type"],
                        r["scope_entity_id"], r["owner_id"], r["visibility"], r["idempotency_key"], r["source_id"],
                        r["config_version"]))
    return jobs


_OWNED = "id = $1 AND status = 'processing' AND claimed_by = $2 AND attempts = $3"


async def heartbeat(pool: asyncpg.Pool, job: Job, lease_seconds: int) -> bool:
    """Extend the lease. False => this worker no longer owns the job."""
    r = await pool.execute(
        f"UPDATE ingestion_jobs SET lease_until = now() + make_interval(secs => $4) WHERE {_OWNED}",
        job.id, job.worker_id, job.attempt, float(lease_seconds))
    return r.endswith(" 1")


async def complete(pool: asyncpg.Pool, job: Job, usage: Optional[dict] = None) -> bool:
    r = await pool.execute(
        f"UPDATE ingestion_jobs SET status = 'done', completed_at = now(), lease_until = NULL, last_error = NULL, "
        f"usage = COALESCE($4::jsonb, usage) WHERE {_OWNED}", job.id, job.worker_id, job.attempt, usage)
    return r.endswith(" 1")


def backoff_seconds(attempt: int, *, base: float, cap: float, rng=random.random) -> float:
    """Exponential backoff with jitter (50-100 % of the nominal delay)."""
    return min(cap, base * (2 ** max(0, attempt - 1))) * (0.5 + 0.5 * rng())


async def fail(
    pool: asyncpg.Pool, job: Job, error: str, *, retryable: bool, base: float = 30.0, cap: float = 1800.0,
) -> str:
    """Record a failure. Returns the new status, or 'lost' if fenced out.
    Retryable + attempts remaining -> retryable_failed (run_after = backoff);
    otherwise permanent (status 'failed')."""
    if retryable and job.attempt < job.max_attempts:
        delay = backoff_seconds(job.attempt, base=base, cap=cap)
        r = await pool.execute(
            f"UPDATE ingestion_jobs SET status = 'retryable_failed', last_error = $4, lease_until = NULL, "
            f"run_after = now() + make_interval(secs => $5) WHERE {_OWNED}",
            job.id, job.worker_id, job.attempt, error[:2000], float(delay))
        return "retryable_failed" if r.endswith(" 1") else "lost"
    r = await pool.execute(
        f"UPDATE ingestion_jobs SET status = 'failed', last_error = $4, lease_until = NULL, completed_at = now() WHERE {_OWNED}",
        job.id, job.worker_id, job.attempt, error[:2000])
    return "failed" if r.endswith(" 1") else "lost"


async def release(pool: asyncpg.Pool, job: Job) -> bool:
    """Graceful shutdown: hand the job back without spending the attempt."""
    r = await pool.execute(
        f"UPDATE ingestion_jobs SET status = 'pending', lease_until = NULL, claimed_by = NULL, attempts = attempts - 1 "
        f"WHERE {_OWNED}", job.id, job.worker_id, job.attempt)
    return r.endswith(" 1")


async def reap_exhausted(pool: asyncpg.Pool) -> int:
    """Jobs whose lease expired with no attempts left can never be leased again
    (crash-loop protection): mark them permanently failed with a clear reason."""
    r = await pool.execute(
        "UPDATE ingestion_jobs SET status = 'failed', completed_at = now(), lease_until = NULL, "
        "last_error = COALESCE(last_error, 'lease expired after final attempt (worker crashed or timed out)') "
        "WHERE status = 'processing' AND lease_until < now() AND attempts >= max_attempts")
    return int(r.split()[-1])


async def stats(pool: asyncpg.Pool, job_types: Optional[Sequence[str]] = None) -> dict[str, Any]:
    rows = await pool.fetch(
        "SELECT status, count(*) AS n FROM ingestion_jobs "
        "WHERE ($1::text[] IS NULL OR job_type = ANY($1::text[])) GROUP BY status", list(job_types) if job_types else None)
    out: dict[str, Any] = {r["status"]: r["n"] for r in rows}
    out["expired_leases"] = await pool.fetchval(
        "SELECT count(*) FROM ingestion_jobs WHERE status = 'processing' AND lease_until < now()")
    return out


async def retry_failed(pool: asyncpg.Pool, *, job_types: Optional[Sequence[str]] = None, ids: Optional[Sequence[int]] = None,
                       reset_attempts: bool = True) -> int:
    """Operator retry of permanently failed jobs."""
    r = await pool.execute(
        "UPDATE ingestion_jobs SET status = 'pending', run_after = NULL, completed_at = NULL, lease_until = NULL, "
        "attempts = CASE WHEN $3 THEN 0 ELSE attempts END WHERE status IN ('failed', 'retryable_failed') "
        "AND ($1::text[] IS NULL OR job_type = ANY($1::text[])) AND ($2::bigint[] IS NULL OR id = ANY($2::bigint[]))",
        list(job_types) if job_types else None, list(ids) if ids else None, reset_attempts)
    return int(r.split()[-1])
