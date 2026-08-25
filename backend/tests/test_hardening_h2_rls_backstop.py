"""HARDENING H2 proving tests — RLS backstop on the [H] truth tables.

All offline, per house style: sync tests driving async boundaries via
asyncio.run, fakes recording SQL for content assertions.

Sections:
  Helper    — tenant_transaction() binds app.tenant_id as the FIRST
              statement INSIDE the transaction boundary, via set_config
              with is_local=TRUE spelled into the SQL text (asyncpg
              cannot parameterize SET LOCAL; this is its exact twin).
              FakePool capture proves ordering, content, locality flag,
              the unrestricted hatch, and rollback behavior.
  Leak      — a miniature model of pooled-connection semantics proves
              the binding asyncpg caveat: two sequential borrows of the
              SAME physical connection cannot see each other's tenant;
              distinct simultaneous connections stay independent; and a
              deliberately session-scoped misuse IS detected by the same
              probe (the apparatus has teeth, not vacuous greens).
  Migration — static assertions on db/29_rls_backstop.sql: additive
              column birth, ENABLE+FORCE per truth table, idempotent
              policy guards, ONE shared expression behind every USING/
              WITH CHECK (no drift possible), permissive-when-unset
              fallback, destructive-op ban, and the docs-comment naming
              every table that stays RLS-free and why.
  Cross-pin — Python's TENANT_SETTING is literally the name the policy
              SQL reads; access.py hosts what db/29 depends on.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.config import settings
from app.services.access import (
    TENANT_SETTING,
    TenantScope,
    tenant_setting_statement,
    tenant_transaction,
)

BACKEND = Path(__file__).resolve().parents[1]

ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"


# --------------------------------------------------------------- helper


class RecordingConn:
    def __init__(self, pool: "RecordingPool"):
        self._pool = pool

    @asynccontextmanager
    async def transaction(self):
        self._pool.log.append(("BEGIN", "", ()))
        try:
            yield self
        except BaseException:
            self._pool.log.append(("ROLLBACK", "", ()))
            raise
        else:
            self._pool.log.append(("COMMIT", "", ()))

    async def execute(self, sql, *args):
        entry = ("EXECUTE", " ".join(sql.split()), tuple(args))
        self._pool.log.append(entry)
        return "OK"


class RecordingPool:
    """Records the full statement stream including txn boundaries."""

    def __init__(self):
        self.log: list[tuple[str, str, tuple]] = []
        self.acquires = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquires += 1
        yield RecordingConn(self)


def kinds(pool):
    return [entry[0] for entry in pool.log]


def executed(pool):
    return [(sql, args) for op, sql, args in pool.log if op == "EXECUTE"]


def test_helper_binds_setting_as_first_statement_inside_the_boundary():
    pool = RecordingPool()

    async def body():
        async with tenant_transaction(pool, TenantScope.for_tenant(ORG_A)) as conn:
            await conn.execute("INSERT INTO evidence DEFAULT VALUES")

    asyncio.run(body())
    assert kinds(pool) == ["BEGIN", "EXECUTE", "EXECUTE", "COMMIT"]
    bind_sql, bind_args = executed(pool)[0]
    assert bind_sql == "SELECT set_config($1, $2, TRUE)"
    # both arguments bound like any other statement — nothing interpolated
    assert bind_args == (TENANT_SETTING, ORG_A)
    # the caller's own statement runs strictly after the binding
    assert executed(pool)[1] == ("INSERT INTO evidence DEFAULT VALUES", ())


def test_helper_emits_set_local_not_a_session_set():
    """The whole point: is_local=TRUE is IN the emitted SQL text."""
    pool = RecordingPool()

    async def body():
        async with tenant_transaction(pool, TenantScope.for_tenant(ORG_B)):
            pass

    asyncio.run(body())
    sql, _ = executed(pool)[0]
    assert "set_config($1, $2, TRUE)" in sql
    assert "FALSE" not in sql


def test_tenant_setting_statement_is_the_single_emission_point():
    sql, args = tenant_setting_statement(TenantScope.for_tenant(ORG_A))
    assert sql == "SELECT set_config($1, $2, TRUE)"
    assert args == (TENANT_SETTING, ORG_A)


def test_unrestricted_hatch_opens_a_plain_transaction():
    pool = RecordingPool()

    async def body():
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            await conn.execute("SELECT 1")

    asyncio.run(body())
    assert kinds(pool) == ["BEGIN", "EXECUTE", "COMMIT"]
    assert all(TENANT_SETTING not in sql for sql, _ in executed(pool))


def test_tenant_setting_statement_refuses_unrestricted_scope():
    with pytest.raises(ValueError):
        tenant_setting_statement(TenantScope.unrestricted())


def test_exception_rolls_back_and_propagates_no_commit():
    class Boom(Exception):
        pass

    pool = RecordingPool()

    async def body():
        async with tenant_transaction(pool, TenantScope.for_tenant(ORG_A)):
            raise Boom()

    with pytest.raises(Boom):
        asyncio.run(body())
    assert kinds(pool) == ["BEGIN", "EXECUTE", "ROLLBACK"]


# ----------------------------------------------------------------- leak


_SET_CONFIG_RE = re.compile(r"set_config\(\$1,\s*\$2,\s*(TRUE|FALSE)\)")


class PooledConnSim:
    """
    A physical connection, modeled at miniature scale: SESSION state
    survives across borrows (that is the leak surface); transaction-
    local writes live in an overlay that dies at the boundary — commit
    OR rollback, exactly Postgres's SET LOCAL semantics.
    """

    def __init__(self):
        self.session: dict[str, str] = {}
        self._overlay: dict[str, str] | None = None

    @asynccontextmanager
    async def transaction(self):
        assert self._overlay is None, "no nested transactions in this model"
        self._overlay = {}
        try:
            yield self
        finally:
            self._overlay = None  # commit or rollback: locals evaporate

    async def execute(self, sql, *args):
        match = _SET_CONFIG_RE.search(" ".join(sql.split()))
        if match:
            name, value = args[0], args[1]
            if match.group(1) == "TRUE":
                assert self._overlay is not None
                self._overlay[name] = value
            else:
                self.session[name] = value  # session scope: THE leak
        return "OK"

    def current_setting(self, name: str) -> str | None:
        if self._overlay is not None and name in self._overlay:
            return self._overlay[name]
        return self.session.get(name)


class OneConnPool:
    """Hands out the SAME physical connection on every acquire."""

    def __init__(self, conn: PooledConnSim):
        self._conn = conn
        self.borrows = 0

    @asynccontextmanager
    async def acquire(self):
        self.borrows += 1
        yield self._conn


def test_sequential_borrows_of_one_connection_cannot_see_each_others_tenant():
    conn = PooledConnSim()
    pool = OneConnPool(conn)

    async def borrow(tenant_id: str) -> str | None:
        seen_inside = None

        async with tenant_transaction(pool, TenantScope.for_tenant(tenant_id)) as c:
            seen_inside = c.current_setting(TENANT_SETTING)
        return seen_inside

    assert asyncio.run(borrow(ORG_A)) == ORG_A
    # back in the pool: the SAME physical connection carries NO residue
    assert conn.current_setting(TENANT_SETTING) is None

    assert asyncio.run(borrow(ORG_B)) == ORG_B
    assert conn.current_setting(TENANT_SETTING) is None
    assert pool.borrows == 2


def test_simultaneous_distinct_connections_stay_independent():
    conn_a, conn_b = PooledConnSim(), PooledConnSim()

    async def both():
        async with tenant_transaction(OneConnPool(conn_a), TenantScope.for_tenant(ORG_A)):
            async with tenant_transaction(
                OneConnPool(conn_b), TenantScope.for_tenant(ORG_B)
            ) as c_b:
                assert c_b.current_setting(TENANT_SETTING) == ORG_B
        return (
            conn_a.current_setting(TENANT_SETTING),
            conn_b.current_setting(TENANT_SETTING),
        )

    assert asyncio.run(both()) == (None, None)


def test_apparatus_detects_a_session_scoped_leak_negative_control():
    """The probe must be able to FAIL: a session-scope binding (the exact
    misuse the caveat warns about) leaves residue this same detector sees."""
    conn = PooledConnSim()

    async def misuse():
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, FALSE)", TENANT_SETTING, ORG_A)

    asyncio.run(misuse())
    assert conn.current_setting(TENANT_SETTING) == ORG_A  # leaked, as suspected
    conn.session.clear()  # teardown for later tests sharing nothing anyway


def test_rollback_discards_the_setting_too():
    class Boom(Exception):
        pass

    conn = PooledConnSim()

    async def doomed():
        async with tenant_transaction(OneConnPool(conn), TenantScope.for_tenant(ORG_A)):
            raise Boom()

    with pytest.raises(Boom):
        asyncio.run(doomed())
    assert conn.current_setting(TENANT_SETTING) is None


# ------------------------------------------------------------ migration


DB29 = (BACKEND / "db" / "29_rls_backstop.sql").read_text(encoding="utf-8")

TRUTH_TABLES = (
    "evidence",
    "executions",
    "change_sets",
    "change_set_operations",
    "failure_routes",
)


def _section(table: str) -> str:
    matches = [
        m.start() for m in re.finditer(rf"^ALTER TABLE {table}$", DB29, re.M)
    ]
    assert len(matches) == 1, f"expected exactly one section start for {table}"
    start = matches[0]
    next_starts = [
        m.start()
        for t in TRUTH_TABLES
        for m in re.finditer(rf"^ALTER TABLE {t}$", DB29, re.M)
        if m.start() > start
    ]
    end = min(next_starts) if next_starts else len(DB29)
    return DB29[start:end]


def test_migration_covers_exactly_the_truth_tables():
    altered = re.findall(r"^ALTER TABLE (\w+)$", DB29, re.M)
    assert sorted(altered) == sorted(TRUTH_TABLES)
    assert DB29.count("CREATE POLICY tenant_isolation ON") == len(TRUTH_TABLES)


def test_each_table_births_tenant_id_additively_with_commons_default():
    commons = f"'{settings.default_tenant_id}'"
    for table in TRUTH_TABLES:
        block = _section(table)
        assert "ADD COLUMN IF NOT EXISTS tenant_id UUID" in block, table
        assert f"NOT NULL DEFAULT {commons}" in block, table


def test_each_table_gets_an_index_and_enable_plus_force_rls():
    expected_idx = {
        "evidence": "idx_evidence_tenant",
        "executions": "idx_executions_tenant",
        "change_sets": "idx_change_sets_tenant",
        "change_set_operations": "idx_cso_tenant",
        "failure_routes": "idx_failure_routes_tenant",
    }
    for table in TRUTH_TABLES:
        block = _section(table)
        assert f"CREATE INDEX IF NOT EXISTS {expected_idx[table]}" in block, table
        assert "ENABLE ROW LEVEL SECURITY" in block, table
        assert "FORCE ROW LEVEL SECURITY" in block, table


def test_every_policy_is_created_under_an_idempotency_guard():
    for table in TRUTH_TABLES:
        block = _section(table)
        assert (
            "SELECT 1 FROM pg_policies" in block
            and f"tablename = '{table}'" in block
            and "policyname = 'tenant_isolation'" in block
        ), table


def test_every_using_and_with_check_delegates_to_the_one_expression():
    """Single policy source, engine edition: no policy spells its own
    comparison, so USING/WITH CHECK drift is structurally impossible."""
    clause = r"(?:USING|WITH CHECK)\s*\(sl_tenant_scope_allows\(tenant_id\)\)"
    assert len(re.findall(clause, DB29)) == 2 * len(TRUTH_TABLES)
    # ...and nothing else ever appears as a policy clause body
    assert len(re.findall(r"(?:USING|WITH CHECK)\s*\(", DB29)) == 2 * len(
        TRUTH_TABLES
    )
    assert DB29.count("CREATE OR REPLACE FUNCTION sl_tenant_scope_allows") == 1


def test_policy_expression_is_permissive_when_unset_strict_when_bound():
    decl = DB29[DB29.index("CREATE OR REPLACE FUNCTION sl_tenant_scope_allows"):]
    body = decl.split("AS $$")[1].split("$$;")[0]
    assert "current_setting('app.tenant_id', true)" in body  # missing_ok: never errors
    assert "NULLIF(" in body and ", '')" in body  # empty string counts as unset
    assert "COALESCE(" in body
    assert "p_row_tenant::text" in body  # fallback = the row's own tenant


def test_expression_function_is_stable_sql_and_replaceable():
    decl = DB29[DB29.index("CREATE OR REPLACE FUNCTION sl_tenant_scope_allows"):]
    header = decl.split("AS $$")[0]
    assert "RETURNS BOOLEAN" in header
    assert "LANGUAGE sql" in header
    assert "STABLE" in header


def test_migration_is_idempotent_and_never_destructive():
    assert DB29.count("IF NOT EXISTS") >= len(TRUTH_TABLES) * 2  # columns + indexes
    assert "CREATE OR REPLACE FUNCTION" in DB29
    for banned in (
        "DROP TABLE",
        "DROP COLUMN",
        "DROP POLICY",
        "DELETE FROM",
        "TRUNCATE",
        "UPDATE ",
    ):
        assert banned not in DB29, banned


def test_migration_commons_default_resolves_to_the_seeded_organization():
    assert DB29.count(f"DEFAULT '{settings.default_tenant_id}'") == len(TRUTH_TABLES)


def test_docs_comment_names_what_stays_rls_free_and_why():
    """The posture note IS part of the deliverable: someone flipping
    multi-tenancy on later must find the reasoning where the policies are."""
    assert "BACKSTOP" in DB29 and "SINGLE policy source" in DB29
    assert "LEFT RLS-FREE" in DB29 or "RLS-FREE" in DB29
    assert "public-commons posture" in DB29
    for substrate in ("knowledge_nodes", "task_nodes", "episodes", "traces"):
        assert substrate in DB29, substrate
    assert "execution_plans" in DB29 and "[D→frozen]" in DB29
    assert "organizations" in DB29 and "circular" in DB29  # identity defines tenants
    assert "CUTOVER" in DB29  # substrate RLS needs the adoption sweep first
    assert "ASYNCPG CAVEAT IS BINDING" in DB29  # the pooling rule, in writing


# ------------------------------------------------------------ cross-pin


def test_python_setting_name_is_literally_what_the_policy_reads():
    assert TENANT_SETTING == "app.tenant_id"
    assert f"'{TENANT_SETTING}'" in DB29


def test_access_py_hosts_the_helpers_the_migration_depends_on():
    src = (BACKEND / "app" / "services" / "access.py").read_text(encoding="utf-8")
    assert "TENANT_SETTING = \"app.tenant_id\"" in src
    assert "def tenant_setting_statement(" in src
    assert "async def tenant_transaction(" in src
    assert "@asynccontextmanager" in src
