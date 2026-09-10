"""
DB-free coverage for app/services/claim_evidence.py (task #38).

Two halves:

1.  `record_claim_evidence` -- proves it builds a claim-targeted
    `Evidence` via the real `outcome_to_evidence()` (so its validation
    rules, e.g. invariant #13's success-criteria gate, apply verbatim
    -- no re-implementation here) and issues the real
    `INSERT INTO evidence (...)` with the exact column list/binding
    order `procedures.py::record_execution_outcome`'s real writer uses,
    plus `tenant_id` bound from `TenantScope.commons()` inside a
    `tenant_transaction`. FakeConn/FakeTxnPool mirror
    `test_wave3_tenancy_adoption.py`'s own idiom (same repo convention:
    each offline test file hand-rolls its own fake, not shared).

2.  `get_claim_evidence` -- a hand-rolled FakePool proving the exact
    SQL shape (`target_type = 'claim' AND target_id = $1::uuid AND
    t_invalid IS NULL ORDER BY t_valid ASC`) and that rows come back as
    plain dicts, oldest first.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.execution.evidence import EvidenceViolation
from app.services.claim_evidence import get_claim_evidence, record_claim_evidence

CLAIM_ID = "00000000-0000-4000-8000-0000000000c1"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# record_claim_evidence
# ---------------------------------------------------------------------


class _Row(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class FakeConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def transaction(self):
        return FakeConn._TxnCM(self)

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "SET"

    async def fetchrow(self, sql, *args):
        self.statements.append((_norm(sql), args))
        norm = _norm(sql)
        if "INSERT INTO evidence" in norm:
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


def test_record_claim_evidence_binds_claim_target_with_no_version():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    new_id = _run(record_claim_evidence(
        pool,
        claim_id=CLAIM_ID,
        evidence_type="execution_result",
        outcome_status="success",
        success_criteria={"predicate": "ran without error"},
        created_by="test-writer",
    ))

    assert new_id

    insert_sql, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    # Exact column/binding order procedures.py's real writer uses.
    assert insert_args[2] == "claim"          # target_type
    assert str(insert_args[3]) == CLAIM_ID    # target_id
    assert insert_args[4] is None             # target_version -- claims are unversioned
    assert insert_args[5] == "supports"       # direction defaults from success
    assert insert_args[10] == "success"       # outcome_status
    assert insert_args[13] == "test-writer"   # created_by
    assert insert_args[-1] is not None        # tenant_id bound, not NULL


def test_record_claim_evidence_binds_tenant_id_as_the_set_config_first_statement():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(record_claim_evidence(
        pool,
        claim_id=CLAIM_ID,
        evidence_type="execution_result",
        outcome_status="failure",
        failure_class="environment_changed",
    ))

    set_config_idx = conn.index_of("set_config")
    insert_idx = conn.index_of("INSERT INTO evidence")
    assert set_config_idx < insert_idx, "tenant must be bound before the write, not after"


def test_record_claim_evidence_defaults_writer_stamp_when_created_by_omitted():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(record_claim_evidence(
        pool,
        claim_id=CLAIM_ID,
        evidence_type="reproduction",
        outcome_status="success",
        success_criteria={"metrics": {"passed": True}},
    ))

    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    assert insert_args[13] == "claim_evidence.record_claim_evidence@v1"


def test_record_claim_evidence_forwards_independence_group_to_the_insert():
    """B10 / V4-hardening §14: a caller that knows two rows share one
    underlying source can pass a named group so they stop counting as
    independent. The group binds at arg index 8 -- the same position
    procedures.py's real evidence writer uses."""
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(record_claim_evidence(
        pool,
        claim_id=CLAIM_ID,
        evidence_type="execution_result",
        outcome_status="success",
        success_criteria={"predicate": "the source asserts X"},
        independence_group="skill_md:acme/repo@deadbeef",
    ))

    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    assert insert_args[8] == "skill_md:acme/repo@deadbeef"


def test_record_claim_evidence_independence_group_defaults_to_null_self_grouped():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(record_claim_evidence(
        pool,
        claim_id=CLAIM_ID,
        evidence_type="execution_result",
        outcome_status="success",
        success_criteria={"metrics": {"ok": True}},
    ))

    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    assert insert_args[8] is None


def test_record_claim_evidence_rejects_a_blank_independence_group():
    """The reused outcome_to_evidence() gate refuses a blank group
    (a blank would silently merge every blank-grouped row into one
    non-corroborating bucket). Not re-implemented here -- just proven
    to still apply through this call path."""
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    with pytest.raises(EvidenceViolation):
        _run(record_claim_evidence(
            pool,
            claim_id=CLAIM_ID,
            evidence_type="execution_result",
            outcome_status="success",
            success_criteria={"predicate": "x"},
            independence_group="   ",
        ))
    assert conn.statements == [], "a rejected payload must never reach the INSERT"


def test_record_claim_evidence_rejects_bare_success_with_no_criteria():
    """The real outcome_to_evidence()/validate_evidence() gate applies
    verbatim -- invariant #13 is not re-implemented, just reused."""
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    with pytest.raises(EvidenceViolation):
        _run(record_claim_evidence(
            pool,
            claim_id=CLAIM_ID,
            evidence_type="execution_result",
            outcome_status="success",
        ))
    assert conn.statements == [], "a rejected payload must never reach the INSERT"


# ---------------------------------------------------------------------
# get_claim_evidence
# ---------------------------------------------------------------------


class FakeReadPool:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((_norm(sql), params))
        return self._rows


def test_get_claim_evidence_issues_the_exact_bounded_query():
    pool = FakeReadPool(rows=[])

    result = _run(get_claim_evidence(pool, CLAIM_ID))

    assert result == []
    assert len(pool.fetch_calls) == 1
    sql, params = pool.fetch_calls[0]
    assert "target_type = 'claim'" in sql
    assert "target_id = $1::uuid" in sql
    assert "t_invalid IS NULL" in sql
    assert "ORDER BY t_valid ASC" in sql
    assert params == (CLAIM_ID,)


def test_get_claim_evidence_returns_rows_as_plain_dicts():
    rows = [
        _Row({"id": "e1", "outcome_status": "success"}),
        _Row({"id": "e2", "outcome_status": "failure"}),
    ]
    pool = FakeReadPool(rows=rows)

    result = _run(get_claim_evidence(pool, CLAIM_ID))

    assert result == [
        {"id": "e1", "outcome_status": "success"},
        {"id": "e2", "outcome_status": "failure"},
    ]
    assert all(isinstance(r, dict) and not isinstance(r, _Row) for r in result)
