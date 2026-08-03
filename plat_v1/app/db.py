"""
Connection pool setup.

The JSONB codec registration is not optional and is the reason this file
exists at all. Without it asyncpg hands back JSONB columns as raw `str`, so
`input_schema`, `spec`, `plan`, and every other jsonb field arrives as a
string that looks fine right up until something subscripts it.

The corollary, and the mistake backend_v2 made once already: once a codec is
registered, pass Python dicts to asyncpg directly. Do NOT json.dumps() in
Python and cast in SQL -- the encoder then serialises an already-serialised
string, and reads come back double-encoded. Every write path in this app
passes dicts.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

# \Z not $: `$` also matches before a trailing newline, so "plat_v1\n" would
# validate. Lowercase only, because an unquoted identifier is case-folded by
# Postgres and `Plat_V1` would then never equal what we compare against.
_IDENTIFIER = re.compile(r"\A[a-z_][a-z0-9_]*\Z")

# Schemas plat_v1 must never claim as its own. `public` is the dangerous one:
# it is where backend_v2 keeps task_nodes and traces, and every isolation
# check in this module would report green while writing to them.
_RESERVED_SCHEMAS = frozenset({"public", "pg_catalog", "information_schema", "pg_toast"})


def qualified_schema() -> str:
    """
    The schema name, validated as a bare identifier.

    It arrives from the environment and is interpolated into `search_path` and
    `CREATE SCHEMA`, neither of which can be parameterised. Rejecting anything
    that isn't a plain lowercase identifier is cheaper than quoting rules and
    leaves no room for argument.
    """
    name = settings.db_schema
    if not _IDENTIFIER.match(name):
        raise RuntimeError(
            f"DB_SCHEMA must be a plain lowercase identifier (a-z, 0-9, underscore, "
            f"not starting with a digit); got {name!r}"
        )
    if len(name.encode()) > 63:
        raise RuntimeError(
            f"DB_SCHEMA is longer than Postgres' 63-byte identifier limit and would "
            f"be silently truncated; got {name!r}"
        )
    if name in _RESERVED_SCHEMAS or name.startswith("pg_"):
        raise RuntimeError(
            f"DB_SCHEMA must not be {name!r}. plat_v1 needs a schema of its own: "
            f"it defines task_nodes and traces, and backend_v2 defines tables of the "
            f"same names with different columns in public."
        )
    return name


_extension_schema: dict[str, str] = {}


async def discover_extension_schema(dsn: str) -> str:
    """
    Find the schema that actually hosts pgvector, and cache it.

    Guessing does not work. Supabase documents extensions living in
    `extensions`, and on the project this was built against `pgcrypto` is
    indeed there -- but `vector` is in `public`. A hardcoded guess produces
    `type "vector" does not exist` at CREATE TABLE, which reads like a
    missing extension rather than a search_path that doesn't reach it.
    """
    # Keyed by DSN: the answer is a property of the database, and a process
    # that opens pools against two of them must not reuse the first's.
    cached = _extension_schema.get(dsn)
    if cached is not None:
        return cached

    conn = await asyncpg.connect(dsn)
    try:
        found = await conn.fetchval(
            """
            SELECT n.nspname FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
            """
        )
    finally:
        await conn.close()

    # Not installed yet: 01_schema.sql's CREATE EXTENSION will put it in the
    # first schema of the search_path, which is ours.
    resolved = found or qualified_schema()
    _extension_schema[dsn] = resolved
    return resolved


def search_path(extension_schema: str) -> str:
    """
    Our schema first, then wherever pgvector lives.

    Ours is first so that once our tables exist they always shadow a
    same-named table elsewhere -- and `CREATE TABLE IF NOT EXISTS` resolves
    against the *target* schema, not visibility, so seeding into our schema
    works even while public holds a table of the same name. Both verified
    against the live database rather than assumed.

    The extension schema is appended only because the `vector` type has to
    resolve. If that turns out to be `public`, `verify_isolation` is what
    stops a missing table of ours from silently falling through to another
    application's.
    """
    ours = qualified_schema()
    if extension_schema == ours:
        return ours
    # Validated, not trusted. It is interpolated into a startup parameter, and
    # a name needing quotes would produce a malformed search_path -- which the
    # setup check would then compare against its own malformed copy and report
    # as fine. A false negative in the guard is worse than a hard failure here.
    if not _IDENTIFIER.match(extension_schema):
        raise RuntimeError(
            f"pgvector lives in schema {extension_schema!r}, which is not a plain "
            f"lowercase identifier. plat_v1 cannot safely put it on the search_path."
        )
    return f"{ours},{extension_schema}"


# Every table plat_v1 owns. `task_nodes` and `traces` are the ones whose
# names backend_v2 also uses today, but checking only those would mean the
# guard silently stops covering a name the moment either app adds a table --
# and the cost of checking all nine is one query.
OWNED_TABLES = (
    "task_nodes",
    "task_edges",
    "implementations",
    "evals",
    "eval_results",
    "runs",
    "proposals",
    "traces",
    "cache_entries",
)


async def verify_isolation(conn: asyncpg.Connection) -> None:
    """
    Assert that every table we own resolves to our schema.

    Called after seeding and at app startup, never at connection init: before
    the schema is seeded these names legitimately resolve elsewhere or not at
    all, and failing then would make the database impossible to set up.

    This is the check that has to hold, because the search_path is not
    structurally safe. pgvector's schema must be on it for the `vector` type
    to resolve, and on some installs -- including the one this was built
    against -- that schema is `public`, which is exactly where backend_v2
    keeps its own task_nodes and traces.
    """
    ours = qualified_schema()
    for name in OWNED_TABLES:
        # relkind restricted to ordinary and partitioned tables: to_regclass
        # also matches views, so without this a `plat_v1.traces` view over
        # `public.traces` would satisfy the guard while every write still
        # landed on the other application's rows.
        where = await conn.fetchval(
            """
            SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.oid = to_regclass($1) AND c.relkind IN ('r', 'p')
            """,
            name,
        )
        if where is None:
            raise RuntimeError(
                f"table {name!r} does not exist in schema {ours!r}. "
                f"Run `python scripts/seed.py`."
            )
        if where != ours:
            raise RuntimeError(
                f"unqualified {name!r} resolves to schema {where!r}, not {ours!r}. "
                f"That is another application's table with different columns. "
                f"Refusing to continue."
            )


def _normalise_path(value: str) -> list[str]:
    return [part.strip().strip('"') for part in (value or "").split(",") if part.strip()]


async def _init_connection(conn: asyncpg.Connection) -> None:
    """
    Once per physical connection: type codecs.

    The JSONB codec is not optional. Without it asyncpg returns JSONB columns
    as raw `str`, so every `input_schema` / `spec` / `plan` field arrives as a
    string that looks fine until something subscripts it.
    """
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    # Guarded: a database without pgvector loaded should still serve every
    # non-vector endpoint rather than failing pool construction outright.
    try:
        await conn.execute(f"SET hnsw.ef_search = {int(settings.hnsw_ef_search)}")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not set hnsw.ef_search (vector search may be untuned): %s", exc)


def _make_setup(expected_path: str):
    """
    Build the pool's per-*acquire* callback.

    `setup`, not `init`, and the difference is the whole point. `init` runs
    once when the pool creates a physical connection; `setup` runs on every
    `acquire()`. The failure this guards against is drift *between acquires* --
    measured directly against Supabase's pooler with the earlier
    `SET search_path` approach: acquire 0 saw `plat_v1, extensions`, acquires
    1-3 saw `"$user", public, extensions`, and an unqualified `traces` then
    resolved to *backend_v2's* table. A check on `init` structurally cannot
    see that, because a released and re-acquired connection never re-runs it.

    It also makes the verify-here/write-there pattern sound: `verify_isolation`
    runs on one connection and the writes happen on others, which is only safe
    if every acquire provably carries the same search_path.

    Checked with `SHOW search_path`, which reports the setting, and NOT with
    `current_schema()`, which reports the first schema that actually exists.
    That distinction is a bootstrap deadlock: before our schema is created
    `current_schema()` is `public`, so checking it would fail every connection
    -- including the one seed.py needs to run CREATE SCHEMA, making the
    database impossible to set up. This verifies the transport;
    `verify_isolation` verifies the state.
    """

    async def _setup_connection(conn: asyncpg.Connection) -> None:
        actual = await conn.fetchval("SHOW search_path")
        if _normalise_path(actual) != _normalise_path(expected_path):
            raise RuntimeError(
                f"connection acquired with search_path={actual!r}, expected "
                f"{expected_path!r}. The startup parameter is not being applied. "
                f"Behind a connection pooler use the session pooler (port 5432), not "
                f"the transaction pooler (6543) -- transaction-mode pooling preserves "
                f"neither this nor asyncpg's prepared statements. Refusing to "
                f"continue: unqualified names would resolve somewhere other than "
                f"{qualified_schema()!r}."
            )

    return _setup_connection


async def create_pool(dsn: Optional[str] = None, **kwargs) -> asyncpg.Pool:
    resolved = dsn or settings.require("database_url")

    # search_path goes in the startup packet, not a `SET` statement. A pooler
    # replays startup parameters onto each backend it assigns; a `SET` applies
    # only to the backend that happened to receive it. See
    # _verify_search_path for what that difference cost.
    #
    # Caller settings are spread FIRST so ours wins. The other order let a
    # caller pass server_settings={"search_path": ...} and quietly opt out of
    # the isolation this module exists to provide.
    expected_path = search_path(await discover_extension_schema(resolved))
    server_settings = {**kwargs.pop("server_settings", {}), "search_path": expected_path}
    return await asyncpg.create_pool(
        resolved,
        init=_init_connection,
        setup=_make_setup(expected_path),
        server_settings=server_settings,
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
