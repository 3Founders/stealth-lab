"""
DB-free coverage for app/services/claim_traversal.py's three read-only,
bounded traversal modes (EXPLAIN/RESEARCH/DECIDE).

Same FakePool idiom as test_applicability_hard_constraints_offline.py:
a hand-rolled pool that inspects the SQL text of the exact, already-real
queries this module's dependencies issue (get_procedure's `fetchrow`,
project_state's `fetch` against knowledge_nodes, get_claim_relations'
`fetch` against edges) -- no shared fake, per this repo's own convention
that each offline test file hand-rolls its own.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.claim_traversal import (
    MAX_RESEARCH_HOPS,
    decide,
    explain,
    research,
)

NOW = datetime.now(timezone.utc)
PROC_ID = "00000000-0000-4000-8000-000000000001"


def _procedure(preconditions, **overrides):
    row = {
        "id": PROC_ID,
        "t_invalid": None,
        "staleness": "fresh",
        "availability": "active",
        "verification_state": "verified",
        "approval_status": "approved",
        "scope": {},
        "exclusions": [],
        "preconditions": preconditions,
        "invariants": [],
    }
    row.update(overrides)
    return row


def _claim(claim_id, subject, predicate, obj, truth_state="IN"):
    return {
        "id": claim_id,
        "properties": {
            "subject": subject, "predicate": predicate, "object": obj,
            "truth_state": truth_state,
        },
        "t_valid": NOW - timedelta(days=1),
        "t_invalid": None,
    }


def _edge(edge_id, source_id, target_id, relation, properties=None):
    return {
        "id": edge_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
        "created_by": "test",
        "t_valid": NOW - timedelta(days=1),
        "properties": properties or {},
    }


class FakePool:
    def __init__(self, *, procedures=(), claims=(), edges=()):
        self._procedures = {p["id"]: p for p in procedures}
        self._claims = list(claims)
        self._edges = list(edges)
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((" ".join(sql.split()), params))
        if "FROM procedures WHERE id" in sql:
            row = self._procedures.get(params[0])
            return dict(row) if row else None
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        if "FROM knowledge_nodes" in sql:
            subjects = set(params[0])
            return [c for c in self._claims if c["properties"].get("subject") in subjects]
        if "FROM edges" in sql:
            claim_id, wanted = params[0], set(params[1])
            return [
                e for e in self._edges
                if e["relation"] in wanted and (e["source_id"] == claim_id or e["target_id"] == claim_id)
            ]
        raise AssertionError(f"unexpected fetch: {sql}")


def _run(coro):
    return asyncio.run(coro)


# --- EXPLAIN -----------------------------------------------------------

def test_explain_reports_satisfied_precondition_with_its_supporting_claim():
    """Fails without a real implementation: no supporting_claims would
    ever be populated, or satisfied would default False."""
    procedure = _procedure([{"subject": "project:p", "predicate": "uses", "object": "pandas"}])
    claim = _claim("claim-1", "project:p", "uses", "pandas")
    pool = FakePool(procedures=[procedure], claims=[claim])

    result = _run(explain(pool, PROC_ID))

    assert result.procedure_row_id == PROC_ID
    assert len(result.preconditions) == 1
    pc = result.preconditions[0]
    assert pc.satisfied is True
    assert len(pc.supporting_claims) == 1
    assert pc.supporting_claims[0]["id"] == "claim-1"


def test_explain_flags_unsatisfied_precondition_with_no_supporting_claims():
    procedure = _procedure([{"subject": "project:p", "predicate": "uses", "object": "pandas"}])
    # A claim exists for the subject, but says something else -- must
    # not count as supporting.
    claim = _claim("claim-2", "project:p", "uses", "numpy")
    pool = FakePool(procedures=[procedure], claims=[claim])

    result = _run(explain(pool, PROC_ID))

    pc = result.preconditions[0]
    assert pc.satisfied is False
    assert pc.supporting_claims == []


def test_explain_raises_for_unknown_procedure_row():
    pool = FakePool()
    with pytest.raises(ValueError):
        _run(explain(pool, "does-not-exist"))


# --- RESEARCH ------------------------------------------------------------

def test_research_buckets_relations_into_supporting_contradicting_other():
    """Fails without real bucketing logic: all three lists would stay
    empty or everything would land in one bucket."""
    edges = [
        _edge("e1", "claim-a", "claim-b", "SUPPORTS"),
        _edge("e2", "claim-c", "claim-a", "CONTRADICTS"),
        _edge("e3", "claim-a", "claim-d", "DEPENDS_ON"),
    ]
    pool = FakePool(edges=edges)

    result = _run(research(pool, "claim-a"))

    assert result.claim_id == "claim-a"
    assert {r.edge_id for r in result.supporting} == {"e1"}
    assert {r.edge_id for r in result.contradicting} == {"e2"}
    assert {r.edge_id for r in result.other} == {"e3"}
    assert all(r.hop == 1 for r in result.supporting + result.contradicting + result.other)


def test_research_reaches_hop_two_but_never_hop_three():
    """claim-a -SUPPORTS-> claim-b -SUPPORTS-> claim-c -SUPPORTS-> claim-d.
    claim-d must NOT appear -- proves the 2-hop cap is real, not just a
    default that happens not to be exercised."""
    edges = [
        _edge("e1", "claim-a", "claim-b", "SUPPORTS"),
        _edge("e2", "claim-b", "claim-c", "SUPPORTS"),
        _edge("e3", "claim-c", "claim-d", "SUPPORTS"),
    ]
    pool = FakePool(edges=edges)

    result = _run(research(pool, "claim-a"))

    found_edge_ids = {r.edge_id for r in result.supporting}
    assert found_edge_ids == {"e1", "e2"}
    hops = {r.edge_id: r.hop for r in result.supporting}
    assert hops["e1"] == 1
    assert hops["e2"] == 2


def test_research_rejects_hop_count_above_the_hard_cap():
    pool = FakePool()
    with pytest.raises(ValueError):
        _run(research(pool, "claim-a", max_hops=MAX_RESEARCH_HOPS + 1))


# --- DECIDE ----------------------------------------------------------------

def test_decide_returns_applicable_verbatim_when_all_constraints_pass():
    procedure = _procedure([])
    pool = FakePool(procedures=[procedure])

    result = _run(decide(pool, PROC_ID, {}))

    assert result.applicability.applicable is True
    assert result.closest_precondition_claims is None


def test_decide_surfaces_closest_claim_when_precondition_fails():
    """Fails without the diagnostic add-on: closest_precondition_claims
    would stay None even though a (non-matching) claim exists for the
    subject."""
    procedure = _procedure([{"subject": "project:p", "predicate": "uses", "object": "pandas"}])
    claim = _claim("claim-3", "project:p", "uses", "numpy")  # exists, doesn't satisfy
    pool = FakePool(procedures=[procedure], claims=[claim])

    result = _run(decide(pool, PROC_ID, {}))

    assert result.applicability.applicable is False
    assert result.applicability.failed_constraints[0].startswith("precondition:")
    assert result.closest_precondition_claims is not None
    assert result.closest_precondition_claims["subject"] == "project:p"
    found_ids = {c["id"] for c in result.closest_precondition_claims["claims"]}
    assert found_ids == {"claim-3"}


def test_decide_does_not_surface_closest_claim_for_non_precondition_failures():
    """Disqualified on staleness, never reaches the precondition stage --
    closest_precondition_claims must stay None, and project_state (the
    knowledge_nodes fetch) must never be reached."""
    procedure = _procedure(
        [{"subject": "project:p", "predicate": "uses", "object": "pandas"}],
        staleness="stale",
    )
    pool = FakePool(procedures=[procedure])

    result = _run(decide(pool, PROC_ID, {}))

    assert result.applicability.applicable is False
    assert result.applicability.failed_constraints == ["staleness"]
    assert result.closest_precondition_claims is None
    assert pool.fetch_calls == []


def test_decide_raises_for_unknown_procedure_row():
    pool = FakePool()
    with pytest.raises(ValueError):
        _run(decide(pool, "does-not-exist", {}))
