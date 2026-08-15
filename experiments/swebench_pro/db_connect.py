"""
Cold-start-tolerant Postgres connection, shared by every script here that
opens its own pool.

Originally lived only in run_graph_experiment.py: a whole sweep died at
startup on

    asyncio.exceptions.CancelledError  (in _create_ssl_connection)
    -> TimeoutError                    (asyncpg connect timeout)

i.e. the TLS handshake did not complete inside asyncpg's DEFAULT 60s connect
timeout. That is the normal shape of a serverless Postgres (Neon and
friends) resuming a suspended compute: the first connection after an idle
period has to wake the instance, and cold starts routinely exceed 60s. 60s
is a bad bound for that, and zero retries meant a cold start killed the
entire run before a single instance began.

The fix (longer per-attempt bound, plus a bounded retry that only retries
what a retry can fix) was built once for run_graph_experiment.py and left
un-propagated to every OTHER script that also calls create_pool() directly
(run_symbolic_instance.py, run_graph_instance.py, compare_embeddings.py,
graph_ingest.py) -- each of those is exactly as exposed to the same cold
start today as run_graph_experiment.py was before this module existed.
Import connect_pool from here instead of calling create_pool directly.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.db.session import create_pool

DB_CONNECT_TIMEOUT = 150.0        # seconds for ONE attempt (asyncpg default 60)
DB_CONNECT_MAX_RETRIES = 4
DB_CONNECT_BACKOFF = (5.0, 15.0, 30.0)  # before attempts 2, 3, 4


def _db_connect_is_transient(exc: BaseException) -> bool:
    """
    True for connect failures a retry can plausibly fix: timeouts (cold
    start), socket/DNS errors, and the Postgres-side "not ready / too busy"
    codes. False for authentication, unknown-database and configuration
    errors, which are exactly as broken on attempt 4 as on attempt 1.
    """
    import asyncpg

    # asyncpg surfaces a blown connect timeout as TimeoutError (it catches the
    # inner CancelledError itself), and TimeoutError is an OSError subclass on
    # 3.11+, so socket/DNS/timeout all land in this one branch.
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    return isinstance(exc, (
        asyncpg.exceptions.CannotConnectNowError,       # server starting up
        asyncpg.exceptions.TooManyConnectionsError,
        asyncpg.exceptions.ConnectionDoesNotExistError,
        asyncpg.exceptions.ConnectionFailureError,
    ))


async def connect_pool(dsn: Optional[str], **kwargs):
    """create_pool() with a cold-start-sized timeout and bounded retry."""
    last: Optional[BaseException] = None
    for attempt in range(DB_CONNECT_MAX_RETRIES):
        try:
            return await create_pool(dsn=dsn, timeout=DB_CONNECT_TIMEOUT, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if (not _db_connect_is_transient(exc)
                    or attempt == DB_CONNECT_MAX_RETRIES - 1):
                break
            wait = DB_CONNECT_BACKOFF[attempt]
            print(f"    db connect failed ({type(exc).__name__}: {exc}); "
                  f"retry {attempt + 2}/{DB_CONNECT_MAX_RETRIES} in {wait:.0f}s",
                  flush=True)
            await asyncio.sleep(wait)
    raise RuntimeError(
        f"could not connect to the database after {DB_CONNECT_MAX_RETRIES} "
        f"attempts: {type(last).__name__}: {last}")
