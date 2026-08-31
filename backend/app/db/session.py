"""
Connection pool setup.

The JSONB codec registration below is not optional. Without it asyncpg
returns JSONB columns as raw `str`, so every `properties` / `io_schema` /
`change_set` field silently arrives as a string that looks fine until
something tries to subscript it. Registering the codec at pool init is
the only place this needs handling.
"""
from __future__ import annotations

import json
from typing import Optional

import asyncpg

from app.config import settings

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def create_pool(dsn: Optional[str] = None, **kwargs) -> asyncpg.Pool:
    """
    HOSTED-POSTGRES NOTE (Phase 1 portability audit,
    .scratch/postgres_portability.md): a pooled connection string from a
    provider like Supabase or Neon fronts the real database with
    PgBouncer in TRANSACTION mode -- asyncpg's default server-side
    prepared-statement caching is a real, documented incompatibility
    with that (statements silently fail as "prepared statement ... does
    not exist" once a session-bound backend connection changes
    mid-pool-lifetime). `**kwargs` already forwards to
    `asyncpg.create_pool`/`asyncpg.connect`, so no code change is needed
    here -- a caller deploying against a POOLED hosted connection string
    should pass `statement_cache_size=0` explicitly:

        create_pool(pooled_dsn, statement_cache_size=0)

    A DIRECT (non-pooled/session-mode) connection string needs no such
    flag. `scripts/migrate.py` in particular should always target the
    direct connection string, never the pooled one -- DDL under a
    transaction-mode pooler is a second, independent hazard beyond
    prepared statements.
    """
    return await asyncpg.create_pool(
        dsn or settings.require("database_url"),
        init=_init_connection,
        min_size=kwargs.pop("min_size", 1),
        max_size=kwargs.pop("max_size", 10),
        **kwargs,
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await create_pool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
