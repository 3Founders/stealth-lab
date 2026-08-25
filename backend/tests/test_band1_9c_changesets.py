"""Band 1.9c proving tests — universal ChangeSet coverage (Appendix C #7).

Offline (no database): a FakePool captures every statement the boundary
emits, so each wired mutation path proves it records a ChangeSet with the
right operation/table/target — and the whitelists prove non-[V] targets
and unknown operations are refused at the boundary instead of the engine.

House style: sync tests driving coroutines via asyncio.run (no
pytest-asyncio in this repo).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from app.services.changeset_record import (
    ChangeOperation,
    ChangesetRecordError,
    record_change_set,
    status_change,
)


class FakePool:
    """Captures execute/executemany/fetchval calls for assertion."""

    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list]] = []

    async def execute(self, sql: str, *args):
        self.executes.append((sql, args))
        return "OK"

    async def executemany(self, sql: str, args: list):
        self.executemany_calls.append((sql, list(args)))
        return "OK"

    async def fetchval(self, sql: str, *args):
        self.executes.append((sql, args))
        return "11111111-1111-1111-1111-111111111111"


def _cs_inserts(pool: FakePool) -> int:
    return sum(1 for s, _ in pool.executes if "INSERT INTO change_sets" in s)


# ------------------------------------------------- wired mutation paths

def test_approve_procedure_records_status_changeset():
    from app.services.procedures import approve_procedure

    async def _run():
        pool = FakePool()
        await approve_procedure(pool, procedure_row_id="p-1", approved_by="anuj")
        return pool

    pool = asyncio.run(_run())
    assert _cs_inserts(pool) == 1, "approval must record exactly one ChangeSet"
    many_sql, many_args = pool.executemany_calls[0]
    assert "change_set_operations" in many_sql
    op = many_args[0]
    assert op[1] == "status_change" and op[2] == "procedures" and op[3] == "p-1"
    assert json.loads(op[4])["approval_status"] == "approved"


def test_reject_procedure_records_status_changeset():
    from app.services.procedures import reject_procedure

    async def _run():
        pool = FakePool()
        await reject_procedure(pool, procedure_row_id="p-2", approved_by="anuj")
        return pool

    pool = asyncio.run(_run())
    assert _cs_inserts(pool) == 1
    _, many_args = pool.executemany_calls[0]
    assert json.loads(many_args[0][4])["approval_status"] == "rejected"


def test_quarantine_expiry_records_disabled_changeset():
    from app.services import procedures as proc

    old = "2026-07-01T00:00:00+00:00"
    select_row = {"verification_stats": {"quarantine_entered_at": old},
                  "availability": "quarantined"}
    update_row = {"id": "p-3", "availability": "disabled"}

    async def fetchrow(sql, *a):
        return dict(select_row if sql.startswith("SELECT") else update_row)

    async def _run():
        pool = FakePool()
        pool.fetchrow = fetchrow
        out = await proc.check_quarantine_and_disable(pool, "p-3")
        return pool, out

    pool, out = asyncio.run(_run())
    assert out["availability"] == "disabled"
    assert _cs_inserts(pool) == 1
    _, many_args = pool.executemany_calls[0]
    detail = json.loads(many_args[0][4])
    assert detail["availability"] == "disabled"
    assert many_args[0][3] == "p-3"


# ------------------------------------------------- boundary refusals

def test_refuses_non_v_target_table():
    async def _run():
        pool = FakePool()
        await record_change_set(
            pool, author="x", reason="r",
            operations=[ChangeOperation(
                operation="status_change", target_table="agent_traces",
                target_id="t1")],
        )

    with pytest.raises(ValueError, match=r"not a \[V\] object"):
        asyncio.run(_run())


def test_refuses_unknown_operation():
    async def _run():
        pool = FakePool()
        await record_change_set(
            pool, author="x", reason="r",
            operations=[ChangeOperation(
                operation="delete", target_table="procedures",
                target_id="p-1")],
        )

    with pytest.raises(ValueError, match="unknown ChangeSet operation"):
        asyncio.run(_run())


def test_refuses_empty_operations_and_blank_author():
    async def _run_empty():
        pool = FakePool()
        await record_change_set(pool, author="x", reason="r", operations=[])

    async def _run_blank_author():
        pool = FakePool()
        await record_change_set(
            pool, author="   ", reason="r",
            operations=[status_change("p-1", {})])

    with pytest.raises(ChangesetRecordError, match="empty ChangeSet"):
        asyncio.run(_run_empty())
    with pytest.raises(ChangesetRecordError, match="author"):
        asyncio.run(_run_blank_author())


# ------------------------------------------------- db/25 static contracts

def _ddl() -> str:
    p = Path(__file__).resolve().parents[1] / "db" / "25_universal_changesets.sql"
    return p.read_text(encoding="utf-8")


def test_migration_creates_both_tables_append_only():
    ddl = _ddl()
    assert "CREATE TABLE IF NOT EXISTS change_sets" in ddl
    assert "CREATE TABLE IF NOT EXISTS change_set_operations" in ddl
    assert ddl.count("BEFORE UPDATE OR DELETE ON") == 2, \
        "both tables must carry append-only triggers (invariant #19)"
    assert "tg_change_sets_append_only" in ddl and "tg_cso_append_only" in ddl


def test_migration_whitelists_match_boundary():
    from app.services.changeset_record import OPERATIONS, TARGET_TABLES
    ddl = _ddl()
    for t in TARGET_TABLES:
        assert f"'{t}'" in ddl, t
    for op in OPERATIONS:
        assert f"'{op}'" in ddl, op


def test_no_backfills_in_migration():
    ddl = _ddl()
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", ddl, re.IGNORECASE), \
        "fresh-start ruling: no data backfills in migrations"
