"""
Knowledge-shard registry, routing and batched hydration (docs/sharding.md).

Physical sharding is plain storage sharding, NOT semantic: a Goal gets exactly
one immutable ``home_shard_id`` chosen by weighted rendezvous hashing over the
*active* shards; Goal-local Procedures/Claims prefer their Goal's shard;
hierarchy (``goal_relations``) never influences placement. Provider
connection strings are deployment config: the registry stores only the NAME of
an env var (``dsn_env``); ``NULL`` means "the control database itself"
(built-in shard ``K000``), so a single-database deployment needs no config.

Retrieval never fans out: candidates arrive from the global projections with
their ``home_shard_id``; ``hydrate_rows`` groups ids by shard and issues ONE
batched query per involved shard, concurrently. A shard that cannot be reached
is reported in ``HydrationResult.unavailable_shards`` -- missing candidates are
never silently treated as nonexistent.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

import asyncpg

log = logging.getLogger(__name__)

HOME_SHARD = "K000"
WRITABLE_STATUSES = frozenset({"active"})
READABLE_STATUSES = frozenset({"active", "full", "readonly"})
OBJECT_TYPES = ("goal", "claim", "procedure")


# Canonical writes to remote shards ARE supported (migration 96 replaced the cross-object
# foreign keys with route-aware validation triggers and added the global goal_names index).
# Placement rules (docs/sharding.md):
#   * PUBLIC canonical knowledge is spread over every active shard (rendezvous hashing, goal-local).
#   * PRIVATE / ORG rows (which include everything a user pushes from their local .stealth cache
#     through selective sync) always stay on the home shard K000: private data never fans out
#     to shard databases that were provisioned for the public corpus.
# Operators can stop remote placement without a deploy: STEALTH_REMOTE_SHARD_WRITES=0.
def remote_writes_enabled() -> bool:
    return os.environ.get("STEALTH_REMOTE_SHARD_WRITES", "1") not in ("0", "false", "False")


def writable_shards(shards: Sequence["ShardInfo"], *, visibility: str = "public") -> list["ShardInfo"]:
    if visibility != "public" or not remote_writes_enabled():
        # the home shard is ALWAYS a valid home for private data, whatever weight/status public placement gives it
        return [ShardInfo(HOME_SHARD, "active", 100)]
    return list(shards)


async def multi_shard(pool: Any) -> bool:
    """True iff a shard other than the home shard is registered (cheap: cached registry)."""
    if not isinstance(pool, asyncpg.Pool):   # test doubles / non-database stand-ins are single-shard by definition
        return False
    try:
        return any(s.shard_id != HOME_SHARD for s in await cached_shards(pool))
    except Exception:  # noqa: BLE001 -- a pool without the registry (offline fakes, pre-95 DB) is single-shard
        return False


# ------------------------------------------------------------ per-request telemetry
# One recorder per request (a ContextVar, so concurrent requests never mix): how
# many independent databases a request touched, how (targeted hydration vs
# broadcast), how long each took, route lookups, timeouts and cold connects.
# Recording is a no-op when no recorder is active.


@dataclass
class ShardRequestStats:
    shards: set = field(default_factory=set)
    hydrations: int = 0
    broadcasts: int = 0
    broadcast_width_max: int = 0
    route_lookups: int = 0
    route_lookup_ms: float = 0.0
    shard_latency_ms: dict = field(default_factory=dict)   # shard_id -> max latency seen
    timeouts: dict = field(default_factory=dict)           # shard_id -> count
    unavailable: dict = field(default_factory=dict)        # shard_id -> reason
    cold_connects: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "distinct_shards": len(self.shards), "shards": sorted(self.shards),
            "hydrations": self.hydrations, "broadcasts": self.broadcasts,
            "broadcast_width_max": self.broadcast_width_max, "route_lookups": self.route_lookups,
            "route_lookup_ms": round(self.route_lookup_ms, 2),
            "shard_latency_ms": {k: round(v, 2) for k, v in sorted(self.shard_latency_ms.items())},
            "timeouts": dict(self.timeouts), "unavailable": dict(self.unavailable),
            "cold_connects": self.cold_connects,
        }


_REQUEST_STATS: contextvars.ContextVar[Optional[ShardRequestStats]] = contextvars.ContextVar(
    "shard_request_stats", default=None)


@contextlib.contextmanager
def track_shard_requests():
    """Record every shard access made inside this block (and tasks it spawns)."""
    stats = ShardRequestStats()
    token = _REQUEST_STATS.set(stats)
    try:
        yield stats
    finally:
        _REQUEST_STATS.reset(token)


def current_shard_stats() -> Optional[ShardRequestStats]:
    return _REQUEST_STATS.get()


def _note_shard(shard_id: str, latency_ms: Optional[float] = None) -> None:
    stats = _REQUEST_STATS.get()
    if stats is None:
        return
    stats.shards.add(shard_id)
    if latency_ms is not None:
        stats.shard_latency_ms[shard_id] = max(stats.shard_latency_ms.get(shard_id, 0.0), latency_ms)


def _note_failure(shard_id: str, reason: str) -> None:
    stats = _REQUEST_STATS.get()
    if stats is None:
        return
    stats.unavailable[shard_id] = reason
    if reason == "timeout":
        stats.timeouts[shard_id] = stats.timeouts.get(shard_id, 0) + 1


def shard_read_timeout_s() -> float:
    """Deadline for ONE read on ONE shard database. A slow shard is reported as
    unavailable instead of stalling the whole request (parallel reads wait for the
    slowest). Operators tune it with STEALTH_SHARD_READ_TIMEOUT_S."""
    try:
        return float(os.environ.get("STEALTH_SHARD_READ_TIMEOUT_S", "10"))
    except ValueError:
        return 10.0


_POOLS: dict[int, "ShardPools"] = {}


def pools_for(pool: Any) -> "ShardPools":
    """Process-wide ShardPools bound to a control pool (created once per control pool)."""
    key = id(pool)
    sp = _POOLS.get(key)
    if sp is None:
        sp = _POOLS[key] = ShardPools(pool)
    return sp


async def home_pool(pool: Any, object_type: str, object_id: str, *, by_row_id: bool = False) -> Any:
    """The pool that owns ``object_id``: the control pool for K000 (the overwhelmingly common,
    zero-extra-query case when no remote shard is registered), else the shard's pool."""
    if not await multi_shard(pool):
        return pool
    t0 = time.monotonic()
    if object_type == "procedure" and by_row_id:
        shard = await pool.fetchval("SELECT home_shard_id FROM procedure_row_routes WHERE row_id = $1::uuid", object_id)
    else:
        shard = await pool.fetchval(
            "SELECT home_shard_id FROM object_routes WHERE object_type = $1 AND object_id = $2::uuid", object_type, object_id)
    stats = _REQUEST_STATS.get()
    if stats is not None:
        stats.route_lookups += 1
        stats.route_lookup_ms += (time.monotonic() - t0) * 1000
    if shard in (None, HOME_SHARD):
        _note_shard(HOME_SHARD)
        return pool
    _note_shard(shard)
    return await pools_for(pool).get(shard)


REQUIRED_SHARD_TABLES = ("goals", "procedures", "knowledge_nodes", "evidence", "edges", "execution_plans")


async def check_shard_schema(dsn: str) -> list[str]:
    """Problems that make a database unusable as a knowledge shard (empty list = ok). A shard is
    provisioned by running the normal migrations against it (`scripts/migrate.py --dsn <shard>`)."""
    conn = await asyncpg.connect(dsn)
    try:
        problems = [f"missing table {t}" for t in REQUIRED_SHARD_TABLES
                    if await conn.fetchval("SELECT to_regclass($1)", f"public.{t}") is None]
        if not problems and await conn.fetchval("SELECT to_regclass('public.procedures')") is not None:
            cols = {r[0] for r in await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'procedures'")}
            for c in ("achieves_goal_id", "home_shard_id", "source_key"):
                if c not in cols:
                    problems.append(f"procedures.{c} missing (run migrations through 96)")
        return problems
    finally:
        await conn.close()


class ShardUnavailable(Exception):
    def __init__(self, shard_id: str, reason: str):
        super().__init__(f"shard {shard_id} unavailable: {reason}")
        self.shard_id = shard_id
        self.reason = reason


class NoWritableShard(Exception):
    """No active shard with weight > 0: refuse to place data (fail closed)."""


@dataclass(frozen=True)
class ShardInfo:
    shard_id: str
    status: str
    weight: int
    dsn_env: Optional[str] = None
    capacity_rows: Optional[int] = None
    capacity_bytes: Optional[int] = None   # storage limit (migration 116), enforced by shard_capacity

    @property
    def writable(self) -> bool:
        return self.status in WRITABLE_STATUSES and self.weight > 0


# ------------------------------------------------------------------ routing


def _unit_hash(key: str, shard_id: str) -> float:
    """Deterministic value in the open interval (0, 1)."""
    digest = hashlib.sha256(f"{shard_id}\x00{key}".encode()).digest()
    n = int.from_bytes(digest[:8], "big")
    return (n + 0.5) / 2.0**64


def choose_shard(key: str, shards: Iterable[ShardInfo]) -> str:
    """Weighted rendezvous (HRW) hashing over writable shards.

    Score = -ln(u) / weight, lowest wins => P(shard) proportional to weight,
    and adding/removing a shard only moves the keys that belong to it
    (existing placements are never recomputed anyway: home_shard_id is stored).
    Pure: same inputs, same answer, on every worker/provider.
    """
    candidates = [s for s in shards if s.writable]
    if not candidates:
        raise NoWritableShard("no active shard with weight > 0 is registered")
    return min(candidates, key=lambda s: (-math.log(_unit_hash(key, s.shard_id)) / s.weight, s.shard_id)).shard_id


def choose_child_shard(preferred_shard: Optional[str], key: str, shards: Sequence[ShardInfo]) -> str:
    """Goal-local objects (Procedure, Claim): colocate with the Goal's home
    shard when it can still take writes; otherwise roll over (rendezvous). The
    Goal keeps its identity and shard; only the NEW object lands elsewhere."""
    if preferred_shard:
        for s in shards:
            if s.shard_id == preferred_shard and s.writable:
                return preferred_shard
    return choose_shard(key, shards)


async def list_shards(pool: asyncpg.Pool | asyncpg.Connection) -> list[ShardInfo]:
    rows = await pool.fetch("SELECT * FROM knowledge_shards ORDER BY shard_id")
    return [ShardInfo(r["shard_id"], r["status"], r["weight"], r["dsn_env"], r["capacity_rows"],
                      dict(r).get("capacity_bytes")) for r in rows]


_SHARD_CACHE: dict[int, tuple[float, list[ShardInfo]]] = {}
SHARD_CACHE_TTL_S = 15.0


async def cached_shards(pool: asyncpg.Pool, *, ttl_s: float = SHARD_CACHE_TTL_S) -> list[ShardInfo]:
    """Registry read with a short TTL so per-goal placement is not a query
    per row. Keyed by pool identity (tests use many pools)."""
    key = id(pool)
    hit = _SHARD_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl_s:
        return hit[1]
    shards = await list_shards(pool) or [ShardInfo(HOME_SHARD, "active", 100)]  # empty registry == home shard only
    _SHARD_CACHE[key] = (now, shards)
    return shards


def invalidate_shard_cache() -> None:
    _SHARD_CACHE.clear()


async def register_shard(
    pool: asyncpg.Pool, shard_id: str, *, dsn_env: Optional[str], weight: int = 100,
    status: str = "active", capacity_rows: Optional[int] = None, notes: Optional[str] = None,
    capacity_bytes: Optional[int] = None,
) -> ShardInfo:
    """Idempotent upsert of a shard record (never stores a DSN)."""
    if dsn_env is not None and "://" in dsn_env:
        raise ValueError("dsn_env must be the NAME of an environment variable, never a connection string")
    await pool.execute(
        """
        INSERT INTO knowledge_shards (shard_id, status, weight, dsn_env, capacity_rows, notes)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (shard_id) DO UPDATE SET
            status = EXCLUDED.status, weight = EXCLUDED.weight, dsn_env = EXCLUDED.dsn_env,
            capacity_rows = EXCLUDED.capacity_rows,
            notes = COALESCE(EXCLUDED.notes, knowledge_shards.notes), updated_at = now()
        """,
        shard_id, status, weight, dsn_env, capacity_rows, notes,
    )
    if capacity_bytes is not None:
        await pool.execute("UPDATE knowledge_shards SET capacity_bytes = $2 WHERE shard_id = $1", shard_id, capacity_bytes)
    invalidate_shard_cache()
    return ShardInfo(shard_id, status, weight, dsn_env, capacity_rows, capacity_bytes)


async def set_shard_status(pool: asyncpg.Pool, shard_id: str, status: str) -> None:
    """Rollover lever: marking a shard full/readonly/unhealthy stops NEW
    placements only. Existing objects keep their shard; no identity changes."""
    n = await pool.execute(
        "UPDATE knowledge_shards SET status = $2, updated_at = now() WHERE shard_id = $1", shard_id, status
    )
    if n.endswith(" 0"):
        raise KeyError(f"unknown shard {shard_id!r}")
    invalidate_shard_cache()


async def record_route(
    conn: asyncpg.Pool | asyncpg.Connection, object_type: str, object_id: str, shard_id: str,
) -> None:
    """Upsert the global object -> shard mapping (idempotent)."""
    await conn.execute(
        """
        INSERT INTO object_routes (object_type, object_id, home_shard_id)
        VALUES ($1, $2::uuid, $3)
        ON CONFLICT (object_type, object_id) DO UPDATE
            SET home_shard_id = EXCLUDED.home_shard_id, updated_at = now()
        """,
        object_type, object_id, shard_id,
    )


async def lookup_routes(
    pool: asyncpg.Pool, object_type: str, ids: Sequence[str]
) -> dict[str, str]:
    """One query for many ids: {object_id: home_shard_id}."""
    if not ids:
        return {}
    rows = await pool.fetch(
        "SELECT object_id::text AS id, home_shard_id FROM object_routes "
        "WHERE object_type = $1 AND object_id = ANY($2::uuid[])",
        object_type, list(ids),
    )
    return {r["id"]: r["home_shard_id"] for r in rows}


# ------------------------------------------------------------ pool manager


class ShardPools:
    """shard_id -> asyncpg pool. ``K000`` is the control pool itself. Other
    shards connect lazily from the env var named in ``knowledge_shards.dsn_env``;
    a connect failure is remembered for ``backoff_s`` so a dead shard costs one
    fast failure per interval, not a connection storm per request."""

    def __init__(
        self, control_pool: asyncpg.Pool, *, pool_factory: Optional[Callable[[str], Awaitable[Any]]] = None,
        backoff_s: float = 10.0, max_size: int = 4,
    ):
        self._control = control_pool
        self._pools: dict[str, Any] = {HOME_SHARD: control_pool}
        self._failed_until: dict[str, float] = {}
        if pool_factory is None:
            from app.db.session import _init_connection

            def pool_factory(dsn: str):  # noqa: E306 -- same JSONB codec as the control pool
                return asyncpg.create_pool(dsn, min_size=0, max_size=max_size, init=_init_connection)
        self._factory = pool_factory
        self._backoff_s = backoff_s
        self._locks: dict[str, asyncio.Lock] = {}
        self.connects = 0  # observable: connection storms show up here

    def inject(self, shard_id: str, pool: Any) -> None:
        """Test/deployment hook: bind an already-open pool to a shard id."""
        self._pools[shard_id] = pool

    async def get(self, shard_id: str) -> Any:
        pool = self._pools.get(shard_id)
        if pool is not None:
            return pool
        if time.monotonic() < self._failed_until.get(shard_id, 0.0):
            raise ShardUnavailable(shard_id, "recent connect failure (backing off)")
        lock = self._locks.setdefault(shard_id, asyncio.Lock())
        async with lock:
            pool = self._pools.get(shard_id)
            if pool is not None:
                return pool
            shards = {s.shard_id: s for s in await cached_shards(self._control)}
            info = shards.get(shard_id)
            if info is None:
                raise ShardUnavailable(shard_id, "not registered")
            if info.status not in READABLE_STATUSES:
                raise ShardUnavailable(shard_id, f"status={info.status}")
            dsn = os.environ.get(info.dsn_env or "")
            if not dsn:
                raise ShardUnavailable(shard_id, f"env var {info.dsn_env!r} is not set")
            try:
                self.connects += 1
                stats = _REQUEST_STATS.get()
                if stats is not None:
                    stats.cold_connects += 1
                pool = await self._factory(dsn)
            except Exception as exc:  # noqa: BLE001 -- recorded, surfaced as ShardUnavailable
                self._failed_until[shard_id] = time.monotonic() + self._backoff_s
                raise ShardUnavailable(shard_id, f"connect failed: {type(exc).__name__}") from exc
            self._pools[shard_id] = pool
            return pool

    async def close(self) -> None:
        for sid, p in list(self._pools.items()):
            if sid != HOME_SHARD and hasattr(p, "close"):
                await p.close()


@dataclass
class HydrationResult:
    rows: dict[str, dict] = field(default_factory=dict)
    unavailable_shards: dict[str, str] = field(default_factory=dict)  # shard_id -> reason
    missing_ids: list[str] = field(default_factory=list)  # routable but absent on a reachable shard
    shard_batches: dict[str, int] = field(default_factory=dict)  # shard_id -> ids requested
    shard_latency_ms: dict[str, float] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return bool(self.unavailable_shards)


async def hydrate_rows(
    pools: ShardPools,
    id_to_shard: dict[str, str],
    fetch: Callable[[Any, list[str]], Awaitable[Sequence[Any]]],
    *, id_key: str = "id", timeout_s: Optional[float] = None,
) -> HydrationResult:
    """Fetch canonical rows for ``id_to_shard`` with ONE call to ``fetch(pool,
    ids)`` per involved shard (never per id, never for uninvolved shards).

    ``fetch`` returns records/dicts carrying ``id_key``. Reachable shards that
    do not return an id yield it in ``missing_ids`` (a real absence);
    unreachable shards yield ``unavailable_shards`` (an outage, NOT absence).
    Every shard read is bounded by ``timeout_s`` (default
    ``shard_read_timeout_s()``); a timed-out shard is reported, not waited on.
    """
    result = HydrationResult()
    by_shard: dict[str, list[str]] = {}
    for oid, sid in id_to_shard.items():
        by_shard.setdefault(sid, []).append(oid)
    deadline = shard_read_timeout_s() if timeout_s is None else timeout_s
    stats = _REQUEST_STATS.get()
    if stats is not None and by_shard:
        stats.hydrations += 1

    async def one(sid: str, ids: list[str]) -> None:
        t0 = time.monotonic()
        result.shard_batches[sid] = len(ids)
        try:
            pool = await pools.get(sid)
            rows = await asyncio.wait_for(fetch(pool, ids), deadline)
        except ShardUnavailable as exc:
            result.unavailable_shards[sid] = exc.reason
            _note_failure(sid, exc.reason)
            return
        except asyncio.TimeoutError:
            result.unavailable_shards[sid] = "timeout"
            _note_failure(sid, "timeout")
            log.warning("shard %s hydrate timed out after %.1fs", sid, deadline)
            return
        except (asyncpg.PostgresConnectionError, OSError) as exc:
            result.unavailable_shards[sid] = f"query failed: {type(exc).__name__}"
            _note_failure(sid, result.unavailable_shards[sid])
            log.warning("shard %s hydrate failed: %s", sid, exc)
            return
        finally:
            result.shard_latency_ms[sid] = (time.monotonic() - t0) * 1000
            _note_shard(sid, result.shard_latency_ms[sid])
        found = set()
        for r in rows:
            d = dict(r)
            key = str(d[id_key])
            result.rows[key] = d
            found.add(key)
        result.missing_ids.extend(i for i in ids if i not in found)

    await asyncio.gather(*(one(sid, ids) for sid, ids in by_shard.items()))
    return result


async def verify_routes(pool: Any, *, limit_per_shard: int = 100000) -> dict[str, Any]:
    """Application-level referential validation for remote shards (there is no cross-database FK):

      * every routed goal/procedure/claim exists on its home shard (missing = dangling route)
      * every live procedure's ``achieves_goal_id`` resolves to a live goal on the goal's home shard
    Unreachable shards are reported, never counted as clean."""
    report: dict[str, Any] = {"ok": True, "shards": {}, "unreachable": []}
    sp = pools_for(pool)
    tables = {"goal": ("goals", "id"), "procedure": ("procedures", "procedure_id"), "claim": ("knowledge_nodes", "id")}
    for shard in await list_shards(pool):
        if shard.shard_id == HOME_SHARD:
            continue
        try:
            spool = await sp.get(shard.shard_id)
        except ShardUnavailable as exc:
            report["unreachable"].append({"shard": shard.shard_id, "reason": exc.reason})
            report["ok"] = False
            continue
        entry: dict[str, Any] = {}
        for otype, (table, col) in tables.items():
            ids = [r[0] for r in await pool.fetch(
                "SELECT object_id::text FROM object_routes WHERE object_type = $1 AND home_shard_id = $2 LIMIT $3",
                otype, shard.shard_id, limit_per_shard)]
            if not ids:
                entry[otype] = {"routed": 0, "dangling": 0}
                continue
            present = {r[0] for r in await spool.fetch(f"SELECT DISTINCT {col}::text FROM {table} WHERE {col} = ANY($1::uuid[])", ids)}
            dangling = [i for i in ids if i not in present]
            entry[otype] = {"routed": len(ids), "dangling": len(dangling), "sample": dangling[:5]}
            if dangling:
                report["ok"] = False
        # remote procedures -> their goals
        gids = [r[0] for r in await spool.fetch(
            "SELECT DISTINCT achieves_goal_id::text FROM procedures WHERE t_invalid IS NULL AND achieves_goal_id IS NOT NULL")]
        if gids:
            known = {r[0] for r in await pool.fetch("SELECT object_id::text FROM object_routes WHERE object_type = 'goal' AND object_id = ANY($1::uuid[])", gids)}
            local = {r[0] for r in await pool.fetch("SELECT id::text FROM goals WHERE id = ANY($1::uuid[])", gids)}
            missing = [g for g in gids if g not in known and g not in local]
            entry["procedure_goal_links"] = {"checked": len(gids), "unresolved": len(missing), "sample": missing[:5]}
            if missing:
                report["ok"] = False
        report["shards"][shard.shard_id] = entry
    return report


# ------------------------------------------------------------------ fan-out helpers for readers/writers that scan
# Use ``home_pool`` for anything addressed by id. Use these ONLY for queries that are not addressed by an id (scans,
# counts, "which procedures use X"): they run the same SQL on the control database and on every readable remote shard.
# With no remote shard registered (or a non-database test double) they are a plain call on ``pool`` -- zero overhead.

async def all_pools(pool: Any, *, strict: bool = False) -> list[tuple[str, Any]]:
    """[(shard_id, pool)] for the control database and every readable remote shard. An unreachable shard is skipped
    (logged) unless ``strict``, in which case ShardUnavailable propagates -- writers and verifiers that must not
    silently miss data pass strict=True."""
    out: list[tuple[str, Any]] = [(HOME_SHARD, pool)]
    if not await multi_shard(pool):
        _note_shard(HOME_SHARD)
        return out
    sp = pools_for(pool)
    for info in await cached_shards(pool):
        if info.shard_id == HOME_SHARD or info.status not in READABLE_STATUSES:
            continue
        try:
            out.append((info.shard_id, await sp.get(info.shard_id)))
        except ShardUnavailable as exc:
            _note_failure(info.shard_id, exc.reason)
            if strict:
                raise
            log.warning("shard %s unavailable: skipped in fan-out read", info.shard_id)
    stats = _REQUEST_STATS.get()
    if stats is not None:
        stats.broadcasts += 1
        stats.broadcast_width_max = max(stats.broadcast_width_max, len(out))
        stats.shards.update(sid for sid, _ in out)
    return out


async def _bounded(shard_id: str, awaitable: Awaitable[Any], *, strict: bool) -> Any:
    """One broadcast leg under the per-shard deadline: a timed-out shard is skipped
    (lenient, logged) or raised as ShardUnavailable (strict) -- never waited on forever."""
    try:
        return await asyncio.wait_for(awaitable, shard_read_timeout_s())
    except asyncio.TimeoutError:
        _note_failure(shard_id, "timeout")
        if strict:
            raise ShardUnavailable(shard_id, "timeout") from None
        log.warning("shard %s timed out: skipped in fan-out read", shard_id)
        return None


async def fanout_fetch(pool: Any, sql: str, *args: Any, strict: bool = False) -> list:
    pools = await all_pools(pool, strict=strict)
    if len(pools) == 1:
        return list(await pools[0][1].fetch(sql, *args))
    parts = await asyncio.gather(*[_bounded(sid, p.fetch(sql, *args), strict=strict) for sid, p in pools])
    return [r for part in parts if part for r in part]


async def fanout_fetchrow(pool: Any, sql: str, *args: Any) -> Any:
    """First non-null row over the shards (for an id whose route is unknown)."""
    for sid, p in await all_pools(pool):
        row = await _bounded(sid, p.fetchrow(sql, *args), strict=False)
        if row is not None:
            return row
    return None


async def fanout_fetchval_sum(pool: Any, sql: str, *args: Any, strict: bool = False) -> int:
    pools = await all_pools(pool, strict=strict)
    vals = await asyncio.gather(*[_bounded(sid, p.fetchval(sql, *args), strict=strict) for sid, p in pools])
    return int(sum(v or 0 for v in vals))


async def fanout_execute(pool: Any, sql: str, *args: Any, strict: bool = True) -> int:
    """Run a write on every shard; returns the total affected-row count. Strict by default: a write that could not
    reach a shard must be seen, not skipped."""
    pools = await all_pools(pool, strict=strict)
    tags = await asyncio.gather(*[p.execute(sql, *args) for _, p in pools])
    total = 0
    for t in tags:
        try:
            total += int(str(t).split()[-1])
        except (ValueError, IndexError):
            pass
    return total


async def fanout_sum_row(pool: Any, sql: str, *args: Any, strict: bool = False) -> dict:
    """For a single-row aggregate query (``SELECT count(*) AS a, sum(..) AS b``): run it on every shard via ``fetchrow``
    and add the columns up. Non-numeric/NULL values count as 0."""
    pools = await all_pools(pool, strict=strict)
    rows = await asyncio.gather(*[_bounded(sid, p.fetchrow(sql, *args), strict=strict) for sid, p in pools])
    out: dict[str, Any] = {}
    for row in rows:
        if row is None:
            continue
        for k, v in dict(row).items():
            out[k] = out.get(k, 0) + (v if isinstance(v, (int, float)) else 0)
    return out


async def fanout_best_row(pool: Any, sql: str, *args: Any, key: Callable[[Any], Any]) -> Any:
    """``fetchrow`` on every shard (the SQL already has ORDER BY .. LIMIT 1), then keep the row with the greatest ``key``."""
    rows = [r for r in await asyncio.gather(*[_bounded(sid, p.fetchrow(sql, *args), strict=False)
                                              for sid, p in await all_pools(pool)]) if r is not None]
    return max(rows, key=key) if rows else None
