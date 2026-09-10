"""
DB-free coverage for app/services/claim_belief.py (B8 belief engine + B9
claim_status convergence).

Three parts:

1. `compute_belief` -- PURE. Independence de-dup, the executed >>
   documentary >> weak class ordering, the documentary-only ceiling, the
   zero-evidence floor, the disputed band on balanced contradiction, and
   the hard rule that NO `confidence` key is ever consulted.

2. `status_from_belief` -- PURE. The belief-dict -> claim_status vocab
   mapping, including the open-conflict override.

3. `recompute_claim_belief` -- a hand-rolled FakePool/FakeConn (this repo
   rolls its own per file) proving it reads evidence, writes
   belief_score/belief_method/claim_status through a tenant-bound
   transaction, and records a ChangeSet citing the evidence ids; and that
   a second call with unchanged evidence emits the same UPDATE.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.claim_belief import (
    BELIEF_METHOD,
    compute_belief,
    recompute_claim_belief,
    status_from_belief,
)

CLAIM_ID = "00000000-0000-4000-8000-0000000000b8"


def _run(coro):
    return asyncio.run(coro)


def _ev(ev_id, evidence_type, direction, *, strength_score=1.0, independence_group=None, **extra):
    row = {
        "id": ev_id,
        "evidence_type": evidence_type,
        "direction": direction,
        "strength_score": strength_score,
        "independence_group": independence_group,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------
# compute_belief -- pure
# ---------------------------------------------------------------------


def test_zero_evidence_returns_low_floor_with_method_set():
    b = compute_belief([])
    assert b["score"] == pytest.approx(0.1)
    assert b["method"] == BELIEF_METHOD
    assert b["supporting_independent"] == 0
    assert b["contradicting_independent"] == 0
    assert b["strongest_supporting_type"] is None
    assert b["cited_evidence_ids"] == []


def test_one_independent_execution_result_support_is_moderate():
    b = compute_belief([_ev("e1", "execution_result", "supports")])
    assert 0.45 <= b["score"] <= 0.7
    assert b["supporting_independent"] == 1
    assert b["contradicting_independent"] == 0
    assert b["strongest_supporting_type"] == "execution_result"
    assert b["cited_evidence_ids"] == ["e1"]


def test_three_independent_supports_score_higher_than_one():
    one = compute_belief([_ev("e1", "execution_result", "supports")])
    three = compute_belief([
        _ev("e1", "execution_result", "supports"),
        _ev("e2", "execution_result", "supports"),
        _ev("e3", "execution_result", "supports"),
    ])
    assert three["score"] > one["score"]
    assert three["supporting_independent"] == 3


def test_shared_independence_group_counts_as_one():
    five_grouped = compute_belief([
        _ev(f"e{i}", "execution_result", "supports", independence_group="src:doc-42")
        for i in range(5)
    ])
    single = compute_belief([_ev("solo", "execution_result", "supports")])
    assert five_grouped["supporting_independent"] == 1
    assert five_grouped["score"] == pytest.approx(single["score"])
    # all five ids are still CITED even though they collapse to one group
    assert len(five_grouped["cited_evidence_ids"]) == 5


def test_document_only_evidence_is_capped_no_matter_how_many():
    b = compute_belief([
        _ev(f"d{i}", "document", "supports") for i in range(10)
    ])
    assert b["score"] <= 0.5
    assert b["supporting_independent"] == 10
    assert b["strongest_supporting_type"] == "document"


def test_one_strong_support_beats_ten_documents():
    strong = compute_belief([_ev("x", "execution_result", "supports")])
    docs = compute_belief([_ev(f"d{i}", "document", "supports") for i in range(10)])
    assert strong["score"] > docs["score"]


def test_support_plus_independent_contradiction_lands_in_disputed_band():
    b = compute_belief([
        _ev("s1", "execution_result", "supports"),
        _ev("c1", "execution_result", "contradicts"),
    ])
    assert b["supporting_independent"] == 1
    assert b["contradicting_independent"] == 1
    assert 0.1 <= b["score"] <= 0.45
    assert status_from_belief(b, has_open_conflict=False) == "disputed"


def test_confidence_key_is_never_consulted():
    plain = compute_belief([_ev("e1", "execution_result", "supports")])
    bogus = compute_belief([
        _ev("e1", "execution_result", "supports", confidence=0.99, model_confidence=0.99)
    ])
    assert bogus["score"] == plain["score"]


def test_contradiction_only_scores_below_no_support_ceiling():
    b = compute_belief([_ev("c1", "execution_result", "contradicts")])
    assert b["score"] <= 0.3
    assert b["supporting_independent"] == 0
    assert b["contradicting_independent"] == 1
    assert b["strongest_supporting_type"] is None


# ---------------------------------------------------------------------
# status_from_belief -- pure
# ---------------------------------------------------------------------


def test_status_open_conflict_overrides_everything():
    strong = {"score": 0.9, "supporting_independent": 5, "contradicting_independent": 0}
    assert status_from_belief(strong, has_open_conflict=True) == "disputed"


def test_status_zero_evidence_is_candidate():
    b = compute_belief([])
    assert status_from_belief(b, has_open_conflict=False) == "candidate"


def test_status_strong_independent_support_is_supported():
    b = compute_belief([
        _ev("e1", "execution_result", "supports"),
        _ev("e2", "execution_result", "supports"),
        _ev("e3", "execution_result", "supports"),
    ])
    assert status_from_belief(b, has_open_conflict=False) == "supported"


def test_status_single_moderate_support_is_candidate():
    b = compute_belief([_ev("e1", "execution_result", "supports")])
    assert status_from_belief(b, has_open_conflict=False) == "candidate"


def test_status_document_only_support_is_uncertain_or_candidate():
    b = compute_belief([_ev("d1", "document", "supports")])
    assert status_from_belief(b, has_open_conflict=False) in ("uncertain", "candidate")


def test_status_contradiction_only_is_disputed():
    b = compute_belief([_ev("c1", "execution_result", "contradicts")])
    assert status_from_belief(b, has_open_conflict=False) == "disputed"


# ---------------------------------------------------------------------
# recompute_claim_belief -- FakePool
# ---------------------------------------------------------------------


def _norm(sql: str) -> str:
    return " ".join(sql.split())


class FakePool:
    """Pool + connection in one object (this repo's per-file fake idiom).

    Serves get_claim_evidence (fetch), has_open_conflict_trigger
    (fetchval), the tenant_transaction UPDATE (acquire -> execute), and
    record_change_set (fetchval + executemany).
    """

    def __init__(self, evidence_rows):
        self._evidence_rows = evidence_rows
        self.statements: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list]] = []

    # -- pool-level reads --
    async def fetch(self, sql, *args):
        self.statements.append((_norm(sql), args))
        if "FROM evidence" in _norm(sql) and "target_type = 'claim'" in _norm(sql):
            return list(self._evidence_rows)
        raise AssertionError(f"unexpected fetch: {_norm(sql)[:120]}")

    async def fetchval(self, sql, *args):
        self.statements.append((_norm(sql), args))
        n = _norm(sql)
        if "FROM knowledge_nodes k WHERE k.id = $1::uuid" in n:
            return False  # has_open_conflict_trigger
        if "INSERT INTO change_sets" in n:
            return "11111111-1111-1111-1111-111111111111"
        raise AssertionError(f"unexpected fetchval: {n[:120]}")

    async def executemany(self, sql, args):
        self.executemany_calls.append((_norm(sql), list(args)))
        return "OK"

    # -- tenant_transaction: acquire -> conn (self) --
    def acquire(self):
        pool = self

        class _CM:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _CM()

    def transaction(self):
        class _CM:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _CM()

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "OK"

    def index_of(self, needle: str) -> int:
        for i, (s, _) in enumerate(self.statements):
            if needle in s:
                return i
        raise AssertionError(f"no statement matching {needle!r}")


def test_recompute_reads_evidence_writes_belief_and_records_changeset():
    rows = [
        {"id": "ev-1", "evidence_type": "execution_result", "direction": "supports",
         "strength_score": 1.0, "independence_group": None},
        {"id": "ev-2", "evidence_type": "execution_result", "direction": "supports",
         "strength_score": 1.0, "independence_group": None},
    ]
    pool = FakePool(rows)

    belief = _run(recompute_claim_belief(pool, CLAIM_ID, changeset_reason="claim evidence recorded"))

    # read happened
    pool.index_of("FROM evidence")
    # tenant bound before the write
    set_cfg = pool.index_of("set_config")
    upd = pool.index_of("UPDATE knowledge_nodes SET belief_score")
    assert set_cfg < upd, "tenant must be bound before the belief UPDATE"

    # the UPDATE carries the computed values
    upd_sql, upd_args = pool.statements[upd]
    assert "belief_method = $3" in upd_sql and "claim_status = $4" in upd_sql
    assert str(upd_args[0]) == CLAIM_ID
    assert upd_args[1] == belief["score"]
    assert upd_args[2] == BELIEF_METHOD
    assert upd_args[3] == "supported"  # 2 independent strong supports

    # a ChangeSet was recorded, citing the evidence ids
    assert any("INSERT INTO change_sets" in s for s, _ in pool.statements)
    _, many_args = pool.executemany_calls[0]
    op = many_args[0]
    assert op[1] == "status_change" and op[2] == "knowledge_nodes" and op[3] == CLAIM_ID
    detail = json.loads(op[4])
    assert detail["cited_evidence_ids"] == ["ev-1", "ev-2"]
    assert detail["belief_score"] == belief["score"]
    assert detail["claim_status"] == "supported"


def test_recompute_is_idempotent_second_call_emits_same_update():
    rows = [
        {"id": "ev-1", "evidence_type": "execution_result", "direction": "supports",
         "strength_score": 1.0, "independence_group": None},
    ]

    pool1 = FakePool(rows)
    _run(recompute_claim_belief(pool1, CLAIM_ID))
    upd1 = pool1.statements[pool1.index_of("UPDATE knowledge_nodes SET belief_score")]

    pool2 = FakePool(rows)
    _run(recompute_claim_belief(pool2, CLAIM_ID))
    upd2 = pool2.statements[pool2.index_of("UPDATE knowledge_nodes SET belief_score")]

    assert upd1[0] == upd2[0]  # same SQL text
    assert upd1[1] == upd2[1]  # same bound args (claim_id, score, method, status)


def test_recompute_zero_evidence_writes_floor_and_candidate_status():
    pool = FakePool([])
    belief = _run(recompute_claim_belief(pool, CLAIM_ID))

    assert belief["score"] == pytest.approx(0.1)
    upd = pool.statements[pool.index_of("UPDATE knowledge_nodes SET belief_score")]
    assert upd[1][2] == BELIEF_METHOD
    assert upd[1][3] == "candidate"
