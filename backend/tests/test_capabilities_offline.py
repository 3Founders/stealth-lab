"""DB-free coverage for app/services/capabilities.py.

Mirrors test_claim_evidence_offline.py's own FakeConn/FakeTxnPool /
FakeReadPool idiom exactly (this repo's convention: each offline test file
hand-rolls its own fake, not shared).
"""
from __future__ import annotations

import asyncio

import pytest

from app.execution.evidence import EvidenceViolation
from app.services.capabilities import (
    compare_implementations,
    get_implementation_capability,
    record_implementation_outcome,
)

IMPL_ID = "00000000-0000-4000-8000-0000000000bb"
IMPL_ID_2 = "00000000-0000-4000-8000-0000000000cc"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


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


# ---------------------------------------------------------------------
# record_implementation_outcome
# ---------------------------------------------------------------------


def test_record_implementation_outcome_binds_implementation_target_with_no_version():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    new_id = _run(record_implementation_outcome(
        pool,
        implementation_id=IMPL_ID,
        outcome_status="success",
        success_criteria={"predicate": "ran without error"},
        created_by="test-writer",
    ))

    assert new_id
    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    assert insert_args[2] == "implementation"   # target_type
    assert str(insert_args[3]) == IMPL_ID       # target_id
    assert insert_args[4] is None               # target_version -- unversioned
    assert insert_args[5] == "supports"         # direction defaults from success
    assert insert_args[10] == "success"         # outcome_status
    assert insert_args[13] == "test-writer"     # created_by
    assert insert_args[-1] is not None          # tenant_id bound, not NULL


def test_record_implementation_outcome_defaults_writer_stamp_when_omitted():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(record_implementation_outcome(
        pool, implementation_id=IMPL_ID, outcome_status="failure",
        failure_class="environment_changed",
    ))

    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    assert insert_args[13] == "capabilities.record_implementation_outcome@v1"


def test_record_implementation_outcome_derives_context_key_from_task_node_id():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    _run(record_implementation_outcome(
        pool, implementation_id=IMPL_ID, task_node_id="task-123",
        outcome_status="success", success_criteria={"predicate": "ok"},
    ))

    _, insert_args = next(
        (s, p) for s, p in conn.statements if "INSERT INTO evidence" in s
    )
    assert insert_args[9] == "task_node:task-123"  # context_key


def test_record_implementation_outcome_rejects_bare_success_with_no_criteria():
    conn = FakeConn()
    pool = FakeTxnPool(conn)

    with pytest.raises(EvidenceViolation):
        _run(record_implementation_outcome(
            pool, implementation_id=IMPL_ID, outcome_status="success",
        ))
    assert conn.statements == [], "a rejected payload must never reach the INSERT"


# ---------------------------------------------------------------------
# get_implementation_capability / compare_implementations
# ---------------------------------------------------------------------


class FakeReadPool:
    def __init__(self, rows_by_id: dict):
        self._rows_by_id = rows_by_id
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((_norm(sql), params))
        target_id = params[0]
        return self._rows_by_id.get(target_id, [])


def _evidence_row(outcome_status: str, direction: str = None, context_key=None):
    return _Row({
        "evidence_type": "execution_result",
        "direction": direction or ("supports" if outcome_status == "success" else "contradicts"),
        "outcome_status": outcome_status,
        "independence_group": None,
        "context_key": context_key,
    })


def test_get_implementation_capability_issues_the_exact_bounded_query():
    pool = FakeReadPool(rows_by_id={})

    result = _run(get_implementation_capability(pool, IMPL_ID))

    assert result["evidence_count"] == 0
    assert result["p_estimate"] == 0.0
    sql, params = pool.fetch_calls[0]
    assert "target_type = 'implementation'" in sql
    assert "target_id = $1::uuid" in sql
    assert "t_invalid IS NULL" in sql
    assert params == (IMPL_ID,)


def test_get_implementation_capability_computes_real_p_estimate_from_recorded_outcomes():
    # Mirrors procedure_graph_api.py's own _capability_estimate exactly:
    # a live outcome-bearing row with a terminal outcome_status is an
    # attempt, in EITHER direction. A default-built failure row carries
    # direction='contradicts' (outcome_to_evidence's honest default) and
    # STILL counts -- it is exactly what lowers the Wilson lower bound.
    rows = [_evidence_row("success"), _evidence_row("success"), _evidence_row("failure")]
    pool = FakeReadPool(rows_by_id={IMPL_ID: rows})

    result = _run(get_implementation_capability(pool, IMPL_ID))

    assert result["evidence_count"] == 3       # 2 successes + 1 recorded failure
    assert result["success_count"] == 2
    assert result["p_estimate"] > 0.0          # 2/3, Wilson lower bound still positive
    assert result["level_gated"] is None


def test_get_implementation_capability_counts_default_direction_failures():
    """A failure recorded the way record_implementation_outcome actually
    builds one -- direction='contradicts' by outcome_to_evidence's own
    default -- DOES enter the P estimate. The stream gate is
    evidence_type + terminal outcome_status, never `direction` (that
    filter used to silently drop every real failure)."""
    rows = [
        _evidence_row("success"),                 # default direction='supports'
        _evidence_row("failure"),                 # default direction='contradicts'
    ]
    pool = FakeReadPool(rows_by_id={IMPL_ID: rows})

    result = _run(get_implementation_capability(pool, IMPL_ID))

    assert result["evidence_count"] == 2
    assert result["success_count"] == 1


def test_get_implementation_capability_filters_by_task_node_id_when_given():
    rows = [
        _evidence_row("success", context_key="task_node:t1"),
        _evidence_row("failure", context_key="task_node:t2"),
    ]
    pool = FakeReadPool(rows_by_id={IMPL_ID: rows})

    result = _run(get_implementation_capability(pool, IMPL_ID, task_node_id="t1"))

    assert result["evidence_count"] == 1
    assert result["success_count"] == 1


def test_compare_implementations_orders_by_p_estimate_descending():
    good_rows = [_evidence_row("success") for _ in range(5)]
    bad_rows = [_evidence_row("failure") for _ in range(5)]
    pool = FakeReadPool(rows_by_id={IMPL_ID: good_rows, IMPL_ID_2: bad_rows})

    results = _run(compare_implementations(pool, [IMPL_ID_2, IMPL_ID]))

    assert [r["implementation_id"] for r in results] == [IMPL_ID, IMPL_ID_2]
    assert results[0]["p_estimate"] > results[1]["p_estimate"]
