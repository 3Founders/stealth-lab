"""The search/log database B as a GROUP of projects (storage layout v2, docs/storage_layout_v2.md; migration 132).

B holds tables nothing joins to and no canonical write shares a transaction with (docs/sharding.md): the search
projections (goal_search_docs, procedure_search_index, claim_search_index) and the logs (identity_decisions,
llm_spend, retrieval_decisions, routing_observations, routing_decisions). With members registered
(knowledge_shards.role = 'search'), B is spread over them:

  * a search row goes to ONE member, chosen by weighted rendezvous hashing over writable members and recorded in
    search_routes on the control database, so its later update or delete reaches the same member;
  * a log row goes to one member chosen by hashing its key (logs are never updated in place by a different writer);
  * reads ask EVERY readable member concurrently and merge. Text-search ranks (ts_rank_cd uses no corpus statistics)
    and vector distances are comparable across members, so a merged top-k is the true top-k; lookups by key are
    indexed on each member. Because reads always fan out, a member can be added at any time, nothing rebalances.

With no member registered this is exactly the previous behaviour: every call uses `shards.search_pool(pool)` (the
single project B from SEARCH_DATABASE_URL, or the control database itself).
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any, Awaitable, Callable, Optional, Sequence

log = logging.getLogger(__name__)

LEGACY = "B"      # the single search pool (SEARCH_DATABASE_URL or the control database) when no member exists


async def _members(pool: Any, *, writable: bool) -> list:
    from app.services.shards import READABLE_STATUSES, ROLE_SEARCH, cached_shards

    try:
        shards = await cached_shards(pool)
    except Exception:  # noqa: BLE001 -- a pool without the registry (offline fakes): no members
        return []
    out = [s for s in shards if s.role == ROLE_SEARCH]
    return [s for s in out if (s.writable if writable else s.status in READABLE_STATUSES)]


async def grouped(pool: Any) -> bool:
    """True when search members are registered (B is a group)."""
    return bool(await _members(pool, writable=False))


_MEMBER_LOOPS: dict[tuple[str, int], Any] = {}


def _drop_dead_loop_pools(dsn: str) -> None:
    """Terminate this member's pools that belong to an event loop that no longer runs (a process that opens many
    loops -- tests, scripts -- would otherwise keep one pool set per loop open until the database refuses)."""
    from app.services import shards

    for key in [k for k in list(_MEMBER_LOOPS) if k[0] == dsn]:
        loop = _MEMBER_LOOPS[key]()
        if loop is None or loop.is_closed():
            stale = shards._SEARCH_POOLS.pop(key, None)
            _MEMBER_LOOPS.pop(key, None)
            if stale is not None and hasattr(stale, "terminate"):
                try:
                    stale.terminate()
                except Exception:  # noqa: BLE001 -- best effort on a dead loop's pool
                    pass


async def _member(pool: Any, member_id: str) -> Any:
    """A search member's pool: ONE process-wide pool per member connection string and event loop (the same caching
    as `shards.search_pool`), whatever control pool or transaction connection the caller holds -- caching per
    control pool leaked a pool set per connection (found in testing, 2026-09-30)."""
    import os

    from app.services import shards

    info = {s.shard_id: s for s in await shards.cached_shards(pool)}.get(member_id)
    if info is None:
        raise shards.ShardUnavailable(member_id, "not registered")
    dsn = shards.shard_dsn(info.dsn_env)
    if not dsn:
        raise shards.ShardUnavailable(member_id, f"env var {info.dsn_env!r} is not set")
    loop = asyncio.get_running_loop()
    key = (dsn, id(loop))
    owner = _MEMBER_LOOPS.get(key)
    if owner is None or owner() is not loop:
        # first use in this loop, or a dead loop's id reused: forget whatever was cached under the key
        _drop_dead_loop_pools(dsn)
        shards._SEARCH_POOLS.pop(key, None)
        _MEMBER_LOOPS[key] = weakref.ref(loop)
        shards._SEARCH_POOL_LOCKS[key] = asyncio.Lock()
    existing = shards._SEARCH_POOLS.get(key)
    if existing is not None:
        return existing
    async with shards._SEARCH_POOL_LOCKS[key]:
        existing = shards._SEARCH_POOLS.get(key)
        if existing is None:
            from app.db.session import create_pool

            try:
                existing = shards._SEARCH_POOLS[key] = await create_pool(
                    dsn, min_size=0, max_size=int(os.environ.get("SEARCH_MEMBER_POOL_MAX", "8")),
                    timeout=float(os.environ.get("STEALTH_SHARD_CONNECT_TIMEOUT", "30")))
            except Exception as exc:  # noqa: BLE001 -- surfaced like any unavailable shard
                raise shards.ShardUnavailable(member_id, f"connect failed: {type(exc).__name__}") from exc
        return existing


async def member_pools(pool: Any, *, strict: bool = True, pools: Any = None) -> list[tuple[str, Any]]:
    """[(member_id, pool)] of every readable member; [(LEGACY, search_pool)] when there is no group.

    `strict` (the default) raises when a member cannot be reached -- what one search database does when it is down,
    so identity, dedup and has_procedures never decide on a partial view. Only read-only user paths that prefer a
    partial answer (retrieval legs, product search) pass strict=False."""
    from app.services.shards import ShardUnavailable, _note_failure, search_pool

    members = await _members(pool, writable=False)
    if not members:
        return [(LEGACY, await search_pool(pool))]
    out: list[tuple[str, Any]] = []
    for m in members:
        try:
            out.append((m.shard_id, await _member(pool, m.shard_id)))
        except ShardUnavailable as exc:
            _note_failure(m.shard_id, exc.reason)
            if strict:
                raise
            log.warning("search member %s unavailable: skipped", m.shard_id)
    return out


async def _choose(pool: Any, key: str) -> Optional[str]:
    from app.services.shards import choose_shard

    members = await _members(pool, writable=True)
    return choose_shard(key, members) if members else None


async def pool_for_object(pool: Any, object_type: str, object_id: str, *, place: bool = True,
                          placement_key: Optional[str] = None, pools: Any = None) -> Optional[Any]:
    """The pool holding (or, with `place`, that will hold) an object's search row. Without a group: the single
    search pool. With one: the recorded member, else a newly chosen one (recorded). None when `place` is False and
    the object was never placed on a member. `placement_key` (e.g. the owning Goal's id) keeps related rows on one
    member; reads never depend on it (they ask every member)."""
    from app.services.shards import search_pool

    if not await grouped(pool):
        return await search_pool(pool)
    sid = await pool.fetchval(
        "SELECT search_shard_id FROM search_routes WHERE object_type = $1 AND object_id = $2::uuid",
        object_type, str(object_id))
    if sid is None:
        if not place:
            return None
        sid = await _choose(pool, placement_key or f"{object_type}:{object_id}")
        if sid is None:
            raise RuntimeError("no writable search member is registered")
        sid = await pool.fetchval(
            "INSERT INTO search_routes (object_type, object_id, search_shard_id) VALUES ($1, $2::uuid, $3) "
            "ON CONFLICT (object_type, object_id) DO UPDATE SET search_shard_id = search_routes.search_shard_id "
            "RETURNING search_shard_id", object_type, str(object_id), sid)
    return await _member(pool, sid)


async def pools_for_objects(pool: Any, object_type: str, ids: Sequence[str]) -> dict[str, list[str]]:
    """{member_id: [ids]} for objects already placed; ids never placed are omitted. Without a group:
    {LEGACY: ids}."""
    ids = [str(i) for i in ids]
    if not await grouped(pool):
        return {LEGACY: ids} if ids else {}
    rows = await pool.fetch(
        "SELECT object_id::text AS id, search_shard_id FROM search_routes "
        "WHERE object_type = $1 AND object_id = ANY($2::uuid[])", object_type, ids)
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["search_shard_id"], []).append(r["id"])
    return out


async def member_pool(pool: Any, member_id: str, *, pools: Any = None) -> Any:
    from app.services.shards import search_pool

    if member_id == LEGACY:
        return await search_pool(pool)
    return await _member(pool, member_id)


async def pool_for_log(pool: Any, key: str, *, pools: Any = None) -> Any:
    """Where a new log row goes: a member chosen by hashing `key` (the single search pool without a group)."""
    from app.services.shards import search_pool

    sid = await _choose(pool, f"log:{key}")
    if sid is None:
        return await search_pool(pool)
    return await _member(pool, sid)


async def _each(pool: Any, call: Callable[[Any], Awaitable[Any]], *, strict: bool, pools: Any = None) -> list[Any]:
    from app.services.shards import _bounded

    members = await member_pools(pool, strict=strict, pools=pools)
    if len(members) == 1:
        return [await call(members[0][1])]
    return list(await asyncio.gather(*[_bounded(mid, call(p), strict=strict) for mid, p in members]))


async def fetch_all(pool: Any, sql: str, *args: Any, strict: bool = True, pools: Any = None) -> list:
    """Every member's rows (unordered concatenation)."""
    parts = await _each(pool, lambda p: p.fetch(sql, *args), strict=strict, pools=pools)
    return [r for part in parts if part for r in part]


async def fetchrow_any(pool: Any, sql: str, *args: Any, strict: bool = True, pools: Any = None) -> Any:
    """The first non-null row from any member (for lookups by a unique key)."""
    for part in await _each(pool, lambda p: p.fetchrow(sql, *args), strict=strict, pools=pools):
        if part is not None:
            return part
    return None


async def fetchval_sum(pool: Any, sql: str, *args: Any, strict: bool = True, pools: Any = None) -> Any:
    """Sum of a numeric scalar over members (counts, spend totals)."""
    total = 0
    for part in await _each(pool, lambda p: p.fetchval(sql, *args), strict=strict, pools=pools):
        if part is not None:
            total += part
    return total


async def execute_all(pool: Any, sql: str, *args: Any, pools: Any = None) -> int:
    """Run a statement on every member (deletes by key, maintenance); returns rows affected."""
    n = 0
    for part in await _each(pool, lambda p: p.execute(sql, *args), strict=True, pools=pools):
        try:
            n += int(str(part).rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            pass
    return n


async def fetch_merged(pool: Any, sql: str, *args: Any, key: Callable[[Any], Any], limit: int,
                       strict: bool = True, pools: Any = None) -> list:
    """Top `limit` rows of a ranked query over every member, merged by `key` (the query's own ORDER BY). Each
    member runs the same query with its own LIMIT; the merge of per-member top-k lists is the global top-k."""
    parts = await _each(pool, lambda p: p.fetch(sql, *args), strict=strict, pools=pools)
    if len(parts) == 1:
        return list(parts[0] or [])
    return sorted((r for part in parts if part for r in part), key=key)[:limit]


async def goal_embedding_model(pool: Any) -> Optional[str]:
    """The embedding model most stored Goal vectors use (goal_search_docs on every member of a search group,
    goal_search_index on the control database otherwise)."""
    if not await grouped(pool):
        return await pool.fetchval(
            "SELECT embedding_model FROM goal_search_index WHERE embedding IS NOT NULL GROUP BY 1 "
            "ORDER BY count(*) DESC LIMIT 1")
    counts: dict[str, int] = {}
    for r in await fetch_all(pool, "SELECT embedding_model, count(*) AS n FROM goal_search_docs "
                                   "WHERE embedding IS NOT NULL GROUP BY 1"):
        counts[r["embedding_model"]] = counts.get(r["embedding_model"], 0) + int(r["n"])
    return max(counts, key=lambda m: (counts[m], m)) if counts else None
