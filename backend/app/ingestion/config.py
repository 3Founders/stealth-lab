"""Worker/ingestion configuration (env only; no secrets in the repo) and
production start-up validation."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class WorkerConfig:
    concurrency: int = 4              # INGEST_WORKER_CONCURRENCY  (coroutines per process)
    lease_seconds: int = 300          # INGEST_LEASE_SECONDS       (>= 3x the slowest heartbeat gap)
    job_timeout_seconds: int = 1200   # INGEST_JOB_TIMEOUT_SECONDS
    max_attempts: int = 5             # INGEST_MAX_ATTEMPTS        (default for new jobs)
    retry_base_seconds: float = 30.0  # INGEST_RETRY_BASE_SECONDS
    retry_cap_seconds: float = 1800.0 # INGEST_RETRY_CAP_SECONDS
    poll_seconds: float = 5.0         # INGEST_POLL_SECONDS        (idle sleep in --loop)
    idle_exit_seconds: float = 0.0    # INGEST_IDLE_EXIT_SECONDS   (>0: exit after this long with no work)
    max_jobs: int = 0                 # INGEST_MAX_JOBS            (>0: exit after N jobs; batch runners)
    projection_batch: int = 200       # PROJECTION_BATCH
    drain_projections: bool = True    # INGEST_DRAIN_PROJECTIONS
    reconcile_goals: bool = True      # INGEST_RECONCILE_GOALS   (judge concurrently created paraphrase goals)
    reconcile_claims: bool = True     # INGEST_RECONCILE_CLAIMS  (judge claims created while the judge was down)
    reconcile_window_minutes: float = 30.0  # INGEST_RECONCILE_WINDOW_MINUTES (> the longest job)

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            concurrency=max(1, _int("INGEST_WORKER_CONCURRENCY", 4)),
            lease_seconds=max(10, _int("INGEST_LEASE_SECONDS", 300)),
            job_timeout_seconds=max(10, _int("INGEST_JOB_TIMEOUT_SECONDS", 1200)),
            max_attempts=max(1, _int("INGEST_MAX_ATTEMPTS", 5)),
            retry_base_seconds=_float("INGEST_RETRY_BASE_SECONDS", 30.0),
            retry_cap_seconds=_float("INGEST_RETRY_CAP_SECONDS", 1800.0),
            poll_seconds=_float("INGEST_POLL_SECONDS", 5.0),
            idle_exit_seconds=_float("INGEST_IDLE_EXIT_SECONDS", 0.0),
            max_jobs=_int("INGEST_MAX_JOBS", 0),
            projection_batch=max(1, _int("PROJECTION_BATCH", 200)),
            drain_projections=os.environ.get("INGEST_DRAIN_PROJECTIONS", "1") not in ("0", "false", "False"),
            reconcile_goals=os.environ.get("INGEST_RECONCILE_GOALS", "1") not in ("0", "false", "False"),
            reconcile_claims=os.environ.get("INGEST_RECONCILE_CLAIMS", "1") not in ("0", "false", "False"),
            reconcile_window_minutes=_float("INGEST_RECONCILE_WINDOW_MINUTES", 30.0),
        )


def control_database_url() -> str:
    """CONTROL_DATABASE_URL (preferred) or the legacy DATABASE_URL."""
    from app.config import settings

    return os.environ.get("CONTROL_DATABASE_URL") or settings.require("database_url")


def validate_startup(*, strict: bool | None = None) -> list[str]:
    """Return a list of configuration problems. Production (the default when
    STEALTHLAB_ENV is unset) must not silently fall back to fakes: a semantic
    provider chain and a real embedding provider are REQUIRED. ``strict=None``
    means strict iff running as PRODUCTION."""
    from app.config import settings

    strict = settings.is_production if strict is None else strict
    problems: list[str] = []
    if not (os.environ.get("CONTROL_DATABASE_URL") or settings.database_url):
        problems.append("CONTROL_DATABASE_URL (or DATABASE_URL) is not set")
    if strict:
        from app.services.semantic.providers import build_provider_chain

        if not build_provider_chain(settings):
            problems.append(
                "no semantic provider configured (set JEV_BASE_URL and/or GEMINI_API_KEY and/or LOCAL_MODEL_NAME): "
                "production identity resolution and retrieval must not run without a JEV/NLI judge")
        if not (settings.gemini_api_key or settings.gemini_api_keys or settings.voyage_api_key or settings.use_local_models):
            problems.append("no embedding provider configured (GEMINI_API_KEY / VOYAGE_API_KEY / USE_LOCAL_MODELS)")
    url = os.environ.get("OBJECT_STORAGE_URL", "")
    if strict and url.startswith("memory://"):
        problems.append("OBJECT_STORAGE_URL=memory:// is not allowed in PRODUCTION (in-memory storage loses data)")
    if strict and not url:
        problems.append("OBJECT_STORAGE_URL is not set: large raw payloads would be refused above RAW_PAYLOAD_HARD_MAX_BYTES "
                        "(set s3://bucket/prefix or file:///shared/path)")
    return problems
