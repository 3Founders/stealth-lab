"""
DB-free coverage for app/services/procedure_implementations.py -- the
first-class ProcedureImplementation relation (migration 53, spec
v4-hardening Sec B23).

Idiom: each test hand-rolls a FakeConn/FakePool that records emitted SQL
(normalized) + bind args, per this repo's "each offline file owns its
fake" convention (see test_claim_evidence_offline.py). Asserts:
  - role/status vocab is validated BEFORE any SQL runs;
  - bind_implementation's INSERT column list + ON CONFLICT target,
    including the `WHERE t_invalid IS NULL` partial-index predicate;
  - uuid7 id at the write path;
  - tenant bound (SET set_config) as the first statement of every write;
  - list_implementations_for_procedure JOIN + binding status/role filters;
  - the reverse listing (one impl -> many procedures);
  - close_binding sets ONLY t_invalid;
  - add_evidence_ref's jsonb append (`||`) + `@>` dedupe guard.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.services import procedure_implementations as pi

PROC_ID = "00000000-0000-4000-8000-0000000a0001"
IMPL_ID = "00000000-0000-4000-8000-0000000b0001"
BINDING_ID = "00000000-0000-4000-8000-0000000c0001"
EVIDENCE_ID = "00000000-0000-4000-8000-0000000000e1"
INGEST_CTX_ID = "00000000-0000-4000-8000-0000000d0001"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


class _Row(dict):
    pass


class FakeConn:
    def __init__(self, *, insert_returns_row=True, live_row=True, fetch_rows=None):
        self.statements: list[tuple[str, tuple]] = []
        self._insert_returns_row = insert_returns_row
        self._live_row = live_row
        self._fetch_rows = fetch_rows or []

    # -- transaction plumbing (mirrors tenant_transaction's expectations) --
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
        if "INSERT INTO procedure_implementations" in norm:
            return _Row({"id": args[0]}) if self._insert_returns_row else None
        if "SELECT id FROM procedure_implementations" in norm:
            return _Row({"id": BINDING_ID}) if self._live_row else None
        raise AssertionError(f"unexpected fetchrow: {norm[:160]}")

    async def fetch(self, sql, *args):
        norm = _norm(sql)
        self.statements.append((norm, args))
        return list(self._fetch_rows)

    def index_of(self, needle: str) -> int:
        for i, (s, _) in enumerate(self.statements):
            if needle in s:
                return i
        raise AssertionError(f"no statement matching {needle!r}")

    def first_matching(self, needle: str) -> tuple[str, tuple]:
        for s, a in self.statements:
            if needle in s:
                return s, a
        raise AssertionError(f"no statement matching {needle!r}")


class FakePool:
    """acquire() for write paths; fetch() direct for read helpers."""

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
        return FakePool._AcquireCM(self._conn)

    async def fetch(self, sql, *args):
        return await self._conn.fetch(sql, *args)


# ---------------------------------------------------------------------
# validation happens before SQL
# ---------------------------------------------------------------------


def test_bind_implementation_rejects_unknown_role_before_sql():
    conn = FakeConn()
    with pytest.raises(pi.ProcedureImplementationError):
        _run(pi.bind_implementation(
            FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
            role="primaryish", created_by="t",
        ))
    assert conn.statements == [], "a rejected payload must never reach SQL"


def test_bind_implementation_rejects_unknown_status_before_sql():
    conn = FakeConn()
    with pytest.raises(pi.ProcedureImplementationError):
        _run(pi.bind_implementation(
            FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
            status="live", created_by="t",
        ))
    assert conn.statements == []


def test_list_implementations_for_procedure_rejects_bad_role_filter_before_sql():
    conn = FakeConn()
    with pytest.raises(pi.ProcedureImplementationError):
        _run(pi.list_implementations_for_procedure(
            FakePool(conn), PROC_ID, roles=["nope"],
        ))
    assert conn.statements == []


def test_list_implementations_for_procedure_rejects_bad_status_before_sql():
    conn = FakeConn()
    with pytest.raises(pi.ProcedureImplementationError):
        _run(pi.list_implementations_for_procedure(
            FakePool(conn), PROC_ID, status="bogus",
        ))
    assert conn.statements == []


def test_roles_and_statuses_match_migration_52_vocab():
    assert pi.ROLES == ("primary", "supporting", "partial", "verification")
    assert pi.STATUSES == (
        "candidate", "active", "deprecated", "disabled", "quarantined",
    )


# ---------------------------------------------------------------------
# bind_implementation -- INSERT shape, ON CONFLICT, uuid7, tenant bind
# ---------------------------------------------------------------------


def test_bind_implementation_insert_column_list_and_conflict_target():
    conn = FakeConn(insert_returns_row=True)
    out = _run(pi.bind_implementation(
        FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
        role="verification", status="candidate",
        supported_steps=["s1"], supported_capabilities=["c1"],
        applicability={"env": "linux"}, interface_binding={"entry": "x"},
        evidence_refs=["e0"], resource_path="scripts/run.sh",
        implementation_version=3, implementation_version_constraint=">=1,<2",
        ingestion_context_id=INGEST_CTX_ID,
        created_by="writer",
    ))
    assert out["reused"] is False

    sql, args = conn.first_matching("INSERT INTO procedure_implementations")
    # column list, in order
    for col in (
        "id", "procedure_id", "implementation_id", "role",
        "implementation_version", "implementation_version_constraint",
        "supported_steps", "supported_capabilities", "applicability",
        "interface_binding", "evidence_refs", "status", "resource_path",
        "ingestion_context_id", "created_by",
    ):
        assert col in sql
    # ON CONFLICT on the new partial identity index -- the partial
    # predicate MUST be spelled out for asyncpg to match the index.
    assert "ON CONFLICT (procedure_id, implementation_id, role) WHERE t_invalid IS NULL" in sql
    assert "DO NOTHING" in sql
    assert "RETURNING id" in sql
    # jsonb casts on the payload columns
    assert "$7::jsonb" in sql and "$11::jsonb" in sql

    # arg order + values
    assert args[1] == PROC_ID
    assert args[2] == IMPL_ID
    assert args[3] == "verification"
    assert args[4] == 3
    assert args[5] == ">=1,<2"
    assert args[6] == ["s1"]
    assert args[7] == ["c1"]
    assert args[8] == {"env": "linux"}
    assert args[9] == {"entry": "x"}
    assert args[10] == ["e0"]
    assert args[11] == "candidate"
    assert args[12] == "scripts/run.sh"
    assert args[14] == "writer"


def test_bind_implementation_generates_a_uuid7_id():
    conn = FakeConn(insert_returns_row=True)
    out = _run(pi.bind_implementation(
        FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
        created_by="writer",
    ))
    # returned id is the freshly generated one and is a real UUID
    UUID(out["id"])
    _, args = conn.first_matching("INSERT INTO procedure_implementations")
    assert args[0] == out["id"]
    UUID(args[0])


def test_bind_implementation_binds_tenant_before_the_insert():
    conn = FakeConn(insert_returns_row=True)
    _run(pi.bind_implementation(
        FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
        created_by="writer",
    ))
    set_idx = conn.index_of("set_config")
    insert_idx = conn.index_of("INSERT INTO procedure_implementations")
    assert set_idx < insert_idx, "tenant must be bound before the write"


def test_bind_implementation_defaults_are_empty_containers_not_none():
    conn = FakeConn(insert_returns_row=True)
    _run(pi.bind_implementation(
        FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
        created_by="writer",
    ))
    _, args = conn.first_matching("INSERT INTO procedure_implementations")
    assert args[3] == "primary"        # role default
    assert args[6] == [] and args[7] == []      # supported_steps / capabilities
    assert args[8] == {} and args[9] == {}      # applicability / interface_binding
    assert args[10] == []              # evidence_refs
    assert args[11] == "active"        # status default


def test_bind_implementation_conflict_falls_back_to_live_row_and_marks_reused():
    conn = FakeConn(insert_returns_row=False, live_row=True)
    out = _run(pi.bind_implementation(
        FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
        role="supporting", created_by="writer",
    ))
    assert out == {"id": BINDING_ID, "reused": True}
    sql, args = conn.first_matching("SELECT id FROM procedure_implementations")
    assert "t_invalid IS NULL" in sql
    assert args == (PROC_ID, IMPL_ID, "supporting")


def test_bind_implementation_conflict_with_no_live_row_raises_not_fabricates():
    conn = FakeConn(insert_returns_row=False, live_row=False)
    with pytest.raises(pi.ProcedureImplementationError):
        _run(pi.bind_implementation(
            FakePool(conn), procedure_id=PROC_ID, implementation_id=IMPL_ID,
            created_by="writer",
        ))


# ---------------------------------------------------------------------
# list_implementations_for_procedure -- JOIN + filters
# ---------------------------------------------------------------------


def test_list_implementations_for_procedure_joins_and_filters_active_by_default():
    conn = FakeConn(fetch_rows=[
        _Row({"id": UUID(IMPL_ID), "implementation_id": UUID(IMPL_ID),
              "name": "x", "kind": "tool", "role": "primary",
              "binding_status": "active", "binding_id": UUID(BINDING_ID)}),
    ])
    rows = _run(pi.list_implementations_for_procedure(FakePool(conn), PROC_ID))
    assert [r["id"] for r in rows] == [IMPL_ID]
    # UUIDs stringified
    assert rows[0]["binding_id"] == BINDING_ID
    assert isinstance(rows[0]["binding_id"], str)

    sql, args = conn.statements[0]
    assert "FROM procedure_implementations pi" in sql
    assert "JOIN implementations i ON i.id = pi.implementation_id" in sql
    assert "pi.procedure_id = $1::uuid" in sql
    assert "pi.t_invalid IS NULL" in sql
    assert "pi.status = $2" in sql
    assert "ORDER BY pi.t_valid DESC" in sql
    assert args == (PROC_ID, "active")


def test_list_implementations_for_procedure_status_none_drops_the_status_clause():
    conn = FakeConn(fetch_rows=[])
    _run(pi.list_implementations_for_procedure(FakePool(conn), PROC_ID, status=None))
    sql, args = conn.statements[0]
    assert "pi.status = $" not in sql
    assert args == (PROC_ID,)


def test_list_implementations_for_procedure_role_filter_uses_any_array():
    conn = FakeConn(fetch_rows=[])
    _run(pi.list_implementations_for_procedure(
        FakePool(conn), PROC_ID, roles=["primary", "verification"],
    ))
    sql, args = conn.statements[0]
    assert "pi.role = ANY($3::text[])" in sql
    assert args == (PROC_ID, "active", ["primary", "verification"])


# ---------------------------------------------------------------------
# list_procedures_for_implementation -- reverse direction
# ---------------------------------------------------------------------


def test_list_procedures_for_implementation_reverse_listing():
    conn = FakeConn(fetch_rows=[
        _Row({"binding_id": UUID(BINDING_ID), "procedure_id": UUID(PROC_ID),
              "implementation_id": UUID(IMPL_ID), "role": "supporting",
              "procedure_row_id": UUID(PROC_ID), "procedure_name": "p"}),
    ])
    rows = _run(pi.list_procedures_for_implementation(FakePool(conn), IMPL_ID))
    assert rows[0]["procedure_id"] == PROC_ID

    sql, args = conn.statements[0]
    assert "WHERE pi.implementation_id = $1::uuid" in sql
    assert "pi.t_invalid IS NULL" in sql
    assert "LEFT JOIN LATERAL" in sql
    assert "FROM procedures" in sql
    assert "ORDER BY pi.t_valid DESC" in sql
    assert args == (IMPL_ID,)


def test_list_procedures_for_implementation_role_filter():
    conn = FakeConn(fetch_rows=[])
    _run(pi.list_procedures_for_implementation(
        FakePool(conn), IMPL_ID, roles=["primary"],
    ))
    sql, args = conn.statements[0]
    assert "pi.role = ANY($2::text[])" in sql
    assert args == (IMPL_ID, ["primary"])


# ---------------------------------------------------------------------
# close_binding / add_evidence_ref
# ---------------------------------------------------------------------


def test_close_binding_sets_only_t_invalid_and_binds_tenant_first():
    conn = FakeConn()
    _run(pi.close_binding(FakePool(conn), binding_id=BINDING_ID, closed_by="w"))
    set_idx = conn.index_of("set_config")
    upd_sql, upd_args = conn.first_matching("UPDATE procedure_implementations")
    upd_idx = conn.index_of("UPDATE procedure_implementations")
    assert set_idx < upd_idx
    assert "SET t_invalid = now()" in upd_sql
    # only t_invalid in the SET list -- nothing else assigned
    set_fragment = upd_sql.split("SET", 1)[1].split("WHERE", 1)[0]
    assert set_fragment.count("=") == 1
    assert "t_invalid IS NULL" in upd_sql
    assert upd_args == (BINDING_ID,)


def test_add_evidence_ref_appends_jsonb_with_dedupe_guard():
    conn = FakeConn()
    _run(pi.add_evidence_ref(
        FakePool(conn), binding_id=BINDING_ID, evidence_id=EVIDENCE_ID,
    ))
    set_idx = conn.index_of("set_config")
    upd_sql, upd_args = conn.first_matching("UPDATE procedure_implementations")
    upd_idx = conn.index_of("UPDATE procedure_implementations")
    assert set_idx < upd_idx
    assert "evidence_refs = evidence_refs || to_jsonb($2::text)" in upd_sql
    assert "NOT (evidence_refs @> to_jsonb($2::text))" in upd_sql
    assert "t_invalid IS NULL" in upd_sql
    assert upd_args == (BINDING_ID, EVIDENCE_ID)
