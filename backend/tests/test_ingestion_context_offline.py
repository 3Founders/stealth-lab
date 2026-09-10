"""
DB-free coverage for app/services/ingestion_context.py -- the
IngestionContext provenance-unit lifecycle (migration 51).

FakeConn / FakeTxnPool follow tests/test_claim_evidence_offline.py's
idiom (hand-rolled per file). Assertions:

  - open_ingestion_context emits the INSERT with the migration-50 column
    list, mints a uuid7 id service-side, returns that id, and binds the
    tenant (set_config) BEFORE the INSERT,
  - V0 scope validation short-circuits before any write (unknown
    scope_type; a non-global scope with no entity id),
  - complete_ingestion_context emits the terminal UPDATE and refuses a
    non-terminal / unknown status,
  - get_ingestion_context's query shape.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.services.ingestion_context import (
    complete_ingestion_context,
    get_ingestion_context,
    open_ingestion_context,
)
from app.services.v0_gate import V0Violation


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


class _Row(dict):
    pass


class FakeConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

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
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        norm = _norm(sql)
        self.statements.append((norm, args))
        if "INSERT INTO ingestion_contexts" in norm:
            return _Row({"id": args[0]})
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


_OPEN_KWARGS = dict(
    source_type="skill_md",
    extractor_id="skill_md_ingestion",
    extractor_version="skill_md_v5",
    actor_id="skill_md_ingestion",
)


def test_open_ingestion_context_emits_insert_and_returns_uuid7_id():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    ctx_id = _run(open_ingestion_context(
        pool, scope_type="global", source_uri="file:///x/SKILL.md",
        source_hash="deadbeef", source_ref="00000000-0000-4000-8000-00000000abcd",
        classification="PUBLIC_SOURCE", **_OPEN_KWARGS,
    ))

    insert_sql, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO ingestion_contexts" in s
    )
    assert "extractor_id, extractor_version" in insert_sql
    assert "RETURNING id" in insert_sql
    # service-minted uuid7, echoed back as the return value
    assert UUID(str(insert_args[0])).version == 7
    assert ctx_id == str(insert_args[0])
    # column/binding order
    assert insert_args[1] == "00000000-0000-4000-8000-00000000abcd"  # source_ref
    assert insert_args[2] == "skill_md"                              # source_type
    assert insert_args[3] == "file:///x/SKILL.md"                    # source_uri
    assert insert_args[4] == "deadbeef"                              # source_hash
    assert insert_args[8] == "global"                                # scope_type
    assert insert_args[13] == "PUBLIC_SOURCE"                        # classification
    assert insert_args[14] == "skill_md_ingestion"                   # extractor_id
    assert insert_args[15] == "skill_md_v5"                          # extractor_version
    # tenant bound, not NULL
    assert insert_args[12] is not None


def test_open_ingestion_context_binds_tenant_before_insert():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    _run(open_ingestion_context(pool, scope_type="global", **_OPEN_KWARGS))
    assert conn.index_of("set_config") < conn.index_of("INSERT INTO ingestion_contexts")


def test_open_ingestion_context_rejects_unknown_scope_type_before_write():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    with pytest.raises(V0Violation):
        _run(open_ingestion_context(pool, scope_type="galaxy", **_OPEN_KWARGS))
    assert conn.statements == []


def test_open_ingestion_context_non_global_scope_requires_entity_id():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    with pytest.raises(V0Violation):
        _run(open_ingestion_context(pool, scope_type="project", **_OPEN_KWARGS))
    assert conn.statements == []


def test_open_ingestion_context_accepts_non_global_scope_with_entity_id():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    _run(open_ingestion_context(
        pool, scope_type="project", scope_entity_id="proj-1", **_OPEN_KWARGS,
    ))
    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO ingestion_contexts" in s
    )
    assert insert_args[8] == "project"
    assert insert_args[9] == "proj-1"


def test_complete_ingestion_context_emits_terminal_update():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    _run(complete_ingestion_context(pool, "ctx-1", status="completed"))
    update_sql, update_args = next(
        (s, p) for s, p in conn.statements if "UPDATE ingestion_contexts SET status" in s
    )
    assert "completed_at = now()" in update_sql
    assert update_args == ("ctx-1", "completed")


def test_complete_ingestion_context_allows_failed_and_rejected():
    for status in ("failed", "rejected"):
        conn = FakeConn()
        _run(complete_ingestion_context(FakeTxnPool(conn), "ctx-1", status=status))
        _, update_args = next(
            (s, p) for s, p in conn.statements if "UPDATE ingestion_contexts SET status" in s
        )
        assert update_args == ("ctx-1", status)


def test_complete_ingestion_context_refuses_non_terminal_or_unknown_status():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    for bad in ("open", "done", "closed", ""):
        with pytest.raises(ValueError):
            _run(complete_ingestion_context(pool, "ctx-1", status=bad))
    assert conn.statements == []


class FakeReadPool:
    def __init__(self, row):
        self._row = row
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *params):
        self.calls.append((_norm(sql), params))
        return self._row


def test_get_ingestion_context_query_shape_and_none():
    pool = FakeReadPool(_Row({"id": "c1", "status": "open"}))
    assert _run(get_ingestion_context(pool, "c1")) == {"id": "c1", "status": "open"}
    sql, params = pool.calls[0]
    assert "FROM ingestion_contexts WHERE id = $1::uuid" in sql
    assert params == ("c1",)

    empty = FakeReadPool(None)
    assert _run(get_ingestion_context(empty, "c1")) is None
