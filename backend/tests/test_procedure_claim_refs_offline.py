"""
DB-free coverage for app/services/procedure_claim_refs.py (migration 52,
the typed Procedure<->Claim relation).

Hand-rolled FakeConn / FakePool per this repo's convention (each offline
file rolls its own; the fakes are not shared). They capture emitted SQL
and answer the specific queries this module issues -- they do not
evaluate Postgres semantics.

Proven here:
  * role validation rejects an unknown role BEFORE any SQL is emitted
  * add_procedure_claim_ref issues INSERT ... ON CONFLICT DO NOTHING
    RETURNING id, and falls back to a SELECT of the live row on conflict
  * STRONG_ROLES is the exact precondition/applicability/assumption tuple
  * list_procedures_for_claim / list_claim_refs_for_procedure thread an
    optional role filter into the SQL as `= ANY($n::text[])`
  * backfill_refs_from_preconditions reads preconditions, emits one add
    per distinct claim_id, skips ones already present, returns real counts
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import procedure_claim_refs as pcr
from app.services.procedure_claim_refs import (
    STRONG_ROLES,
    add_procedure_claim_ref,
    backfill_refs_from_preconditions,
    list_claim_refs_for_procedure,
    list_procedures_for_claim,
)

PROC_ID = "00000000-0000-4000-8000-0000000000a1"
CLAIM_ID = "00000000-0000-4000-8000-0000000000c1"
CLAIM_ID_2 = "00000000-0000-4000-8000-0000000000c2"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, owner: "FakePool"):
        self._owner = owner

    class _Txn:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    def transaction(self):
        return FakeConn._Txn()

    async def execute(self, sql, *args):
        self._owner.statements.append((_norm(sql), args))
        return "UPDATE 0"

    async def fetchval(self, sql, *args):
        return await self._owner.fetchval(sql, *args)

    async def fetch(self, sql, *args):
        return await self._owner.fetch(sql, *args)


class FakePool:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []
        # (proc_id, proc_version, claim_id, role) already-present set for
        # the backfill "skip existing" probe.
        self.existing_refs: set[tuple] = set()
        self.procedures_rows: list[dict] = []
        self.claim_ref_rows: list[dict] = []
        self.insert_calls: list[tuple[str, tuple]] = []
        self.conflict_next = False

    class _Acquire:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakePool._Acquire(FakeConn(self))

    async def fetchval(self, sql, *args):
        norm = _norm(sql)
        self.statements.append((norm, args))
        if "INSERT INTO procedure_claim_refs" in norm:
            self.insert_calls.append((norm, args))
            assert "ON CONFLICT (procedure_id, procedure_version, claim_id, role) DO NOTHING" in norm
            assert "RETURNING id" in norm
            if self.conflict_next:
                return None  # simulate ON CONFLICT DO NOTHING -> no row
            return args[0]  # the uuid7 id bound as $1
        if norm.startswith("SELECT id FROM procedure_claim_refs"):
            # conflict fallback SELECT of the live row
            return "live-row-id"
        if norm.startswith("SELECT 1 FROM procedure_claim_refs"):
            proc_id, proc_version, claim_id = args
            key = (str(proc_id), proc_version, str(claim_id), "PRECONDITION")
            return 1 if key in self.existing_refs else None
        raise AssertionError(f"unexpected fetchval: {norm}")

    async def fetch(self, sql, *args):
        norm = _norm(sql)
        self.statements.append((norm, args))
        if "FROM procedures" in norm and "preconditions FROM procedures" in norm:
            return self.procedures_rows
        if norm.startswith("SELECT id, procedure_id, procedure_version, claim_id"):
            return self.claim_ref_rows
        if "FROM procedure_claim_refs r JOIN procedures p" in norm:
            return self.claim_ref_rows
        raise AssertionError(f"unexpected fetch: {norm}")


# ---------------------------------------------------------------------
# role validation
# ---------------------------------------------------------------------


def test_add_ref_rejects_an_unknown_role_before_any_sql():
    pool = FakePool()
    with pytest.raises(ValueError):
        _run(add_procedure_claim_ref(
            pool, procedure_id=PROC_ID, procedure_version=1,
            claim_id=CLAIM_ID, role="GUIDANCE", created_by="t",
        ))
    assert pool.statements == [], "a bad role must never reach the database"


def test_add_ref_rejects_an_unknown_ref_origin_before_any_sql():
    pool = FakePool()
    with pytest.raises(ValueError):
        _run(add_procedure_claim_ref(
            pool, procedure_id=PROC_ID, procedure_version=1,
            claim_id=CLAIM_ID, role="PRECONDITION", ref_origin="guessed",
            created_by="t",
        ))
    assert pool.statements == []


def test_strong_roles_constant_is_exactly_the_three_forcing_roles():
    assert STRONG_ROLES == ("PRECONDITION", "APPLICABILITY", "ASSUMPTION")
    # and every strong role is a member of the full vocabulary
    assert set(STRONG_ROLES).issubset(set(pcr.ROLES))
    assert set(pcr.EXPLANATORY_ROLES) == set(pcr.ROLES) - set(STRONG_ROLES)


# ---------------------------------------------------------------------
# add_procedure_claim_ref
# ---------------------------------------------------------------------


def test_add_ref_issues_insert_on_conflict_returning_and_returns_new_id():
    pool = FakePool()
    new_id = _run(add_procedure_claim_ref(
        pool, procedure_id=PROC_ID, procedure_version=2,
        claim_id=CLAIM_ID, role="APPLICABILITY", created_by="extractor@v3",
        extractor_version="v3",
    ))
    assert new_id
    insert_norm, insert_args = pool.insert_calls[0]
    assert "INSERT INTO procedure_claim_refs" in insert_norm
    assert "ON CONFLICT (procedure_id, procedure_version, claim_id, role) DO NOTHING" in insert_norm
    assert "RETURNING id" in insert_norm
    # id bound first, role carried through, step_refs defaulted to []
    assert str(insert_args[0]) == new_id
    assert insert_args[5] == "APPLICABILITY"
    assert insert_args[6] == []
    # tenant bound before the insert (house write-path idiom)
    assert any("set_config" in s for s, _ in pool.statements)


def test_add_ref_falls_back_to_select_on_conflict():
    pool = FakePool()
    pool.conflict_next = True
    got = _run(add_procedure_claim_ref(
        pool, procedure_id=PROC_ID, procedure_version=1,
        claim_id=CLAIM_ID, role="PRECONDITION", created_by="t",
    ))
    assert got == "live-row-id"
    assert any(s.startswith("SELECT id FROM procedure_claim_refs") for s, _ in pool.statements)


# ---------------------------------------------------------------------
# role-filtered reads
# ---------------------------------------------------------------------


def test_list_procedures_for_claim_threads_the_role_filter_into_sql():
    pool = FakePool()
    _run(list_procedures_for_claim(pool, CLAIM_ID, roles=list(STRONG_ROLES)))
    norm, args = pool.statements[-1]
    assert "FROM procedure_claim_refs r JOIN procedures p" in norm
    assert "r.claim_id = $1::uuid AND r.t_invalid IS NULL" in norm
    assert "r.role = ANY($2::text[])" in norm
    assert args[1] == list(STRONG_ROLES)


def test_list_procedures_for_claim_omits_the_role_clause_when_unfiltered():
    pool = FakePool()
    _run(list_procedures_for_claim(pool, CLAIM_ID))
    norm, args = pool.statements[-1]
    assert "r.role = ANY(" not in norm
    assert args == (CLAIM_ID,)


def test_list_claim_refs_for_procedure_threads_the_role_filter():
    pool = FakePool()
    _run(list_claim_refs_for_procedure(pool, PROC_ID, 3, roles=["RATIONALE"]))
    norm, args = pool.statements[-1]
    assert "WHERE procedure_id = $1::uuid AND procedure_version = $2" in norm
    assert "role = ANY($3::text[])" in norm
    assert args == (PROC_ID, 3, ["RATIONALE"])


# ---------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------


def test_backfill_emits_one_add_per_distinct_claim_id_and_counts_them():
    pool = FakePool()
    pool.procedures_rows = [
        {
            "id": "row-1", "procedure_id": PROC_ID, "version": 1,
            "preconditions": [
                {"subject": "repo", "predicate": "has", "object": "x", "claim_id": CLAIM_ID},
                {"subject": "repo", "predicate": "has", "object": "y", "claim_id": CLAIM_ID_2},
                {"subject": "repo", "predicate": "has", "object": "z"},          # no claim_id
                {"subject": "repo", "predicate": "dup", "object": "x", "claim_id": CLAIM_ID},  # dup
            ],
        },
    ]
    result = _run(backfill_refs_from_preconditions(pool))
    assert result == {
        "procedures_scanned": 1,
        "refs_created": 2,
        "already_present": 0,
    }
    # exactly two INSERTs, both role=PRECONDITION, ref_origin=backfilled
    assert len(pool.insert_calls) == 2
    for _, args in pool.insert_calls:
        assert args[5] == "PRECONDITION"
        assert args[7] == "backfilled"


def test_backfill_skips_a_precondition_ref_that_already_exists():
    pool = FakePool()
    pool.existing_refs = {(PROC_ID, 1, CLAIM_ID, "PRECONDITION")}
    pool.procedures_rows = [
        {
            "id": "row-1", "procedure_id": PROC_ID, "version": 1,
            "preconditions": [
                {"claim_id": CLAIM_ID},
                {"claim_id": CLAIM_ID_2},
            ],
        },
    ]
    result = _run(backfill_refs_from_preconditions(pool))
    assert result["procedures_scanned"] == 1
    assert result["refs_created"] == 1        # only CLAIM_ID_2
    assert result["already_present"] == 1     # CLAIM_ID skipped
    assert len(pool.insert_calls) == 1
    assert str(pool.insert_calls[0][1][3]) == CLAIM_ID_2


def test_backfill_is_a_noop_for_a_procedure_with_no_claim_bearing_preconditions():
    pool = FakePool()
    pool.procedures_rows = [
        {"id": "row-1", "procedure_id": PROC_ID, "version": 1,
         "preconditions": [{"subject": "s", "predicate": "p", "object": "o"}]},
        {"id": "row-2", "procedure_id": PROC_ID, "version": 2, "preconditions": []},
    ]
    result = _run(backfill_refs_from_preconditions(pool))
    assert result == {
        "procedures_scanned": 2,
        "refs_created": 0,
        "already_present": 0,
    }
    assert pool.insert_calls == []
