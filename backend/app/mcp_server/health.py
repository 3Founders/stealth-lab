"""Liveness, readiness and graceful shutdown for the hosted MCP server (securityp1.md §7, P1-C).

    GET /healthz   liveness: 200 whenever the event loop answers. No dependency is checked, so a database
                   blip never makes the container restart (Docker HEALTHCHECK points here).
    GET /readyz    readiness: 200 only when this process should get traffic -- the pool exists, the primary
                   database answers `SELECT 1` within READY_DB_TIMEOUT_S, the newest control migration
                   shipped in this image is applied, and shutdown has not begun. 503 otherwise, with a small
                   JSON naming the failed check. The host health check points here, so traffic is withheld
                   while not ready. Model / embedding providers are NOT checked: find_ways degrades to
                   lexical search without them, which is a degraded service, not an unready one.
    GET /          unchanged (static, kept for anything that still probes it).

Shutdown: on SIGTERM the process marks itself draining BEFORE uvicorn's own handler runs, so /readyz
answers 503 at once while uvicorn stops accepting connections and lets in-flight requests finish (bounded by
`--timeout-graceful-shutdown` in the container command). The lifespan then closes the pool and flushes
Sentry / OpenTelemetry.

Never put a connection string, host name, exception text or stack trace in a response: the checks report
fixed codes only.
"""
from __future__ import annotations

import asyncio
import logging
import re
import signal
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

READY_DB_TIMEOUT_S = 2.0
MIGRATION_CACHE_S = 60.0          # the applied set only changes when migrate.py runs (before the server starts)
RATE_PER_S = 10.0                 # /readyz and /healthz are unauthenticated: a small token bucket per process
RATE_BURST = 20.0
DB_DIR = Path(__file__).resolve().parents[2] / "db"

_draining = False
_migration_cache: dict[str, Any] = {"at": 0.0, "ok": None}
_bucket = {"tokens": RATE_BURST, "at": time.monotonic()}


# ---------------------------------------------------------------- shutdown state

def is_draining() -> bool:
    return _draining


def mark_draining(reason: str = "shutdown") -> None:
    global _draining
    if not _draining:
        log.info("readiness: draining (%s); /readyz now answers 503", reason)
    _draining = True


def reset_for_tests() -> None:
    global _draining
    _draining = False
    _migration_cache.update(at=0.0, ok=None)
    _bucket.update(tokens=RATE_BURST, at=time.monotonic())


STOP_SIGNALS = tuple(s for s in (getattr(signal, n, None) for n in ("SIGTERM", "SIGINT", "SIGBREAK")) if s is not None)


def install_drain_on_signals(signals: tuple[int, ...] = STOP_SIGNALS) -> Callable[[], None]:
    """Chain a 'mark draining' step in front of whatever handler is installed (uvicorn's, which stops the
    server). Returns a function that restores the previous handlers. Safe where a signal does not exist or
    cannot be set (Windows, a non-main thread): it is then a no-op for that signal."""
    previous: dict[int, Any] = {}
    for sig in signals:
        try:
            prev = signal.getsignal(sig)

            def handler(signum: int, frame: Any, _prev: Any = prev) -> None:
                mark_draining(f"signal {signum}")
                if callable(_prev):
                    _prev(signum, frame)
                elif _prev == signal.SIG_DFL:
                    raise KeyboardInterrupt if signum == signal.SIGINT else SystemExit(0)

            signal.signal(sig, handler)
            previous[sig] = prev
        except (ValueError, OSError, AttributeError, TypeError):
            continue

    def restore() -> None:
        for sig, prev in previous.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError, TypeError):
                pass
    return restore


# ---------------------------------------------------------------- checks

def expected_migration(db_dir: Path = DB_DIR) -> Optional[str]:
    """The newest CONTROL migration shipped in this image (search-target files run elsewhere)."""
    files = []
    for p in db_dir.glob("*.sql"):
        m = re.match(r"^(\d+)", p.name)
        if not m:
            continue
        try:
            with p.open(encoding="utf-8") as fh:
                first = fh.readline().strip().lower().replace(" ", "")
        except OSError:
            continue
        if first != "--target:search":
            files.append((int(m.group(1)), p.name))
    return max(files)[1] if files else None


async def _db_ok(pool: Any) -> bool:
    async def probe() -> Any:
        return await pool.fetchval("SELECT 1")
    try:
        return await asyncio.wait_for(probe(), timeout=READY_DB_TIMEOUT_S) == 1
    except Exception:  # noqa: BLE001 -- any failure is "not ready"; the reason is logged, never returned
        log.warning("readiness: database check failed", exc_info=True)
        return False


async def _migrations_ok(pool: Any, expected: Optional[str]) -> bool:
    now = time.monotonic()
    if _migration_cache["ok"] is not None and now - _migration_cache["at"] < MIGRATION_CACHE_S:
        return bool(_migration_cache["ok"])
    if expected is None:
        ok = True
    else:
        async def probe() -> Any:
            return await pool.fetchval("SELECT 1 FROM schema_migrations WHERE filename = $1", expected)
        try:
            ok = await asyncio.wait_for(probe(), timeout=READY_DB_TIMEOUT_S) == 1
        except Exception:  # noqa: BLE001
            log.warning("readiness: migration check failed", exc_info=True)
            ok = False
    _migration_cache.update(at=now, ok=ok)
    return ok


def _rate_ok() -> bool:
    now = time.monotonic()
    _bucket["tokens"] = min(RATE_BURST, _bucket["tokens"] + (now - _bucket["at"]) * RATE_PER_S)
    _bucket["at"] = now
    if _bucket["tokens"] < 1.0:
        return False
    _bucket["tokens"] -= 1.0
    return True


async def readiness(pool: Optional[Any], *, expected: Optional[str] = None) -> tuple[int, dict]:
    """(status code, body). Checks short-circuit in order: draining, pool, database, migrations."""
    checks: dict[str, str] = {}
    if _draining:
        return 503, {"status": "draining", "checks": {"shutdown": "in_progress"}}
    if pool is None:
        return 503, {"status": "not_ready", "checks": {"pool": "missing"}}
    checks["pool"] = "ok"
    if not await _db_ok(pool):
        checks["database"] = "unavailable"
        return 503, {"status": "not_ready", "checks": checks}
    checks["database"] = "ok"
    if not await _migrations_ok(pool, expected if expected is not None else expected_migration()):
        checks["migrations"] = "behind"
        return 503, {"status": "not_ready", "checks": checks}
    checks["migrations"] = "ok"
    # model / embedding providers are deliberately not checked here (see the module docstring)
    return 200, {"status": "ready", "checks": checks, "providers": "not_checked"}


# ---------------------------------------------------------------- HTTP handlers

async def healthz_response() -> tuple[int, dict]:
    if not _rate_ok():
        return 429, {"status": "rate_limited"}
    return 200, {"status": "alive"}


async def readyz_response(pool: Optional[Any]) -> tuple[int, dict]:
    if not _rate_ok():
        return 429, {"status": "rate_limited"}
    return await readiness(pool)


# ---------------------------------------------------------------- shutdown flush

def flush_observability(timeout_s: float = 5.0) -> None:
    """Send what Sentry and OpenTelemetry still hold before the process exits. Each is optional."""
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=timeout_s)          # a no-op when Sentry was never initialised
    except Exception:  # noqa: BLE001
        log.debug("sentry flush skipped", exc_info=True)
    try:
        from app import telemetry

        telemetry.shutdown()
    except Exception:  # noqa: BLE001
        log.debug("telemetry shutdown skipped", exc_info=True)
