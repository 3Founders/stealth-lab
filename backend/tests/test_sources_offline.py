"""
DB-free coverage for app/services/sources.py -- the Source origin
registry (migration 64).

FakeConn / FakeTxnPool mirror tests/test_claim_evidence_offline.py's own
idiom (this repo hand-rolls a fake per file; they are not shared). The
fakes capture emitted SQL so the tests can assert:

  - the exact INSERT column list + ON CONFLICT identity dedup
    (source_type, locator, publisher) and the `(xmax = 0) AS inserted`
    reused flag,
  - a uuid7 id is minted service-side (not gen_random_uuid()),
  - the tenant is bound (set_config) BEFORE the INSERT, never after,
  - V0 scope + provenance rejection short-circuits before any write,
  - the `provenance_source` pg-enum guard (only company_ingested /
    company_debate / prior_library are storable on a Source row),
  - get_source's bounded live-row query shape.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.services.sources import get_source, register_source
from app.services.v0_gate import V0Violation


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


class _Row(dict):
    pass


class FakeConn:
    def __init__(self, *, inserted: bool = True):
        self.statements: list[tuple[str, tuple]] = []
        self._inserted = inserted

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def transaction(self):
        return FakeConn._TxnCM(self)

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "SELECT 1"

    async def fetchrow(self, sql, *args):
        norm = _norm(sql)
        self.statements.append((norm, args))
        if "INSERT INTO sources" in norm:
            return _Row({"id": args[0], "inserted": self._inserted})
        raise AssertionError(f"unexpected fetchrow: {norm[:120]}")

    def index_of(self, needle: str) -> int:
        for i, (s, _) in enumerate(self.statements):
            if needle in s:
                return i
        raise AssertionError(f"no statement matching {needle!r}")


class FakeTxnPool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    class _AcquireCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakeTxnPool._AcquireCM(self._conn)


def test_register_source_emits_insert_with_on_conflict_dedup():
    conn = FakeConn(inserted=True)
    pool = FakeTxnPool(conn)

    result = _run(register_source(
        pool,
        source_type="document",
        locator="file:///skills/x/SKILL.md",
        publisher="skills",
        title="x",
        provenance="prior_library",
        created_by="test-writer",
    ))

    assert result["reused"] is False
    insert_sql, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO sources" in s
    )
    assert "ON CONFLICT (source_type, locator, publisher) DO UPDATE" in insert_sql
    assert "(xmax = 0) AS inserted" in insert_sql
    assert "RETURNING id" in insert_sql
    # id is minted service-side as a uuid7 (version 7), not gen_random_uuid()
    assert UUID(str(insert_args[0])).version == 7
    # column/binding order: source_type, locator, publisher, title
    assert insert_args[1] == "document"
    assert insert_args[2] == "file:///skills/x/SKILL.md"
    assert insert_args[3] == "skills"
    assert insert_args[4] == "x"
    # tenant_id bound, not NULL (Commons default)
    assert insert_args[13] is not None


def test_register_source_reports_reused_when_conflict_updated_an_existing_row():
    conn = FakeConn(inserted=False)   # xmax != 0 -> row already existed
    pool = FakeTxnPool(conn)

    result = _run(register_source(
        pool,
        source_type="document",
        locator="file:///skills/x/SKILL.md",
        publisher="skills",
        provenance="prior_library",
        created_by="test-writer",
    ))
    assert result["reused"] is True


def test_register_source_binds_tenant_before_the_insert():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(register_source(
        pool,
        source_type="document",
        locator="l",
        publisher="p",
        provenance="prior_library",
        created_by="w",
    ))
    set_idx = conn.index_of("set_config")
    insert_idx = conn.index_of("INSERT INTO sources")
    assert set_idx < insert_idx, "tenant must be bound before the write"


def test_register_source_rejects_unknown_scope_type_before_writing():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    with pytest.raises(V0Violation):
        _run(register_source(
            pool,
            source_type="document",
            locator="l",
            publisher="p",
            provenance="prior_library",
            created_by="w",
            scope_type="galaxy",
        ))
    assert conn.statements == [], "a rejected payload must never reach the INSERT"


def test_register_source_rejects_a_non_storable_provenance_value():
    """`public_generated` / `system_pending_review` are valid V0 provenance
    but NOT values of the `provenance_source` pg enum -- they belong on the
    derived rows, not the origin."""
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    with pytest.raises(ValueError):
        _run(register_source(
            pool,
            source_type="document",
            locator="l",
            publisher="p",
            provenance="public_generated",
            created_by="w",
        ))
    assert conn.statements == []


def test_register_source_non_global_scope_requires_an_entity_id():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    with pytest.raises(V0Violation):
        _run(register_source(
            pool,
            source_type="document",
            locator="l",
            publisher="p",
            provenance="prior_library",
            created_by="w",
            scope_type="project",   # no scope_entity_id
        ))
    assert conn.statements == []


class FakeReadPool:
    def __init__(self, row):
        self._row = row
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *params):
        self.calls.append((_norm(sql), params))
        return self._row


def test_get_source_issues_the_bounded_live_row_query():
    pool = FakeReadPool(_Row({"id": "s1", "locator": "l"}))
    result = _run(get_source(pool, "00000000-0000-4000-8000-00000000000a"))
    assert result == {"id": "s1", "locator": "l"}
    sql, params = pool.calls[0]
    assert "FROM sources WHERE id = $1::uuid" in sql
    assert "t_invalid IS NULL" in sql
    assert params == ("00000000-0000-4000-8000-00000000000a",)


def test_get_source_returns_none_when_absent():
    pool = FakeReadPool(None)
    assert _run(get_source(pool, "00000000-0000-4000-8000-00000000000a")) is None
