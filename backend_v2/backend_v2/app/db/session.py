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
import logging
from typing import Optional

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    # HNSW search breadth. pgvector's default of 40 favours latency over
    # recall, and recall is the axis that matters here -- a missed node is
    # a citation the answer cannot make.
    #
    # This is NOT sufficient on its own behind a connection pooler.
    # Measured against Supabase's session pooler: the first acquire saw
    # 100 and the next two saw 40, because Supavisor multiplexes client
    # connections onto a rotating set of server backends and the GUC only
    # applies to the backend that happened to receive the SET. The
    # database-level default in db/07_vector_search_default.sql is what
    # actually holds; this stays for direct-to-Postgres deployments.
    #
    # Guarded because a database without the extension loaded should still
    # serve every non-vector endpoint rather than failing pool construction.
    try:
        await conn.execute(f"SET hnsw.ef_search = {int(settings.hnsw_ef_search)}")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not set hnsw.ef_search (vector search may be untuned): %s", exc)


async def create_pool(dsn: Optional[str] = None, **kwargs) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn or settings.require("database_url"),
        init=_init_connection,
        min_size=kwargs.pop("min_size", settings.db_pool_min_size),
        max_size=kwargs.pop("max_size", settings.db_pool_max_size),
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
