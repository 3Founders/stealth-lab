"""
Offline tests for app/services/claim_impact.py -- the role-aware
claim-invalidation trigger (B5).

The real Postgres bits (the `procedure_claim_refs` JOIN, the
`preconditions @> $1::jsonb` containment scan) are proven for real in
`tests/test_claim_impact_e2e.py`. What is worth proving without a
database is the module's own composition logic:

  * `find_procedures_referencing_claim_grouped` reads the typed
    `procedure_claim_refs` index (via `list_procedures_for_claim`) AND
    UNIONs in the compatibility precondition scan, de-duping by the
    procedure version row id, and groups the result strong vs explanatory
    (`find_procedures_referencing_claim` / `_flat` return that flattened)
  * `propagate_claim_change` marks stale ONLY the strong group and hands
    back the explanatory group untouched

Seams are monkeypatched at the two real module boundaries
(`claim_impact.list_procedures_for_claim`, `claim_impact.mark_procedure_stale`)
rather than through a full fake pool; a tiny fake pool serves only the
one compatibility `fetch`.
"""
from __future__ import annotations

import asyncio

from app.services import claim_impact

CLAIM = "claim-123"


def _run(coro):
    return asyncio.run(coro)


class _CompatPool:
    """Answers only the one compatibility precondition scan
    `claim_impact._compat_precondition_hits` issues."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple] = []

    async def fetch(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        assert "preconditions @> $1::jsonb" in " ".join(sql.split())
        return self._rows


def _patch(monkeypatch, *, typed, mark_calls):
    async def fake_list_procedures_for_claim(pool, claim_id, *, roles=None):
        assert claim_id == CLAIM
        return list(typed)

    async def fake_mark_stale(pool, *, procedure_row_id, reason, detected_by):
        mark_calls.append(
            {"procedure_row_id": procedure_row_id, "reason": reason, "detected_by": detected_by}
        )
        return {"id": procedure_row_id, "staleness": "stale"}

    monkeypatch.setattr(claim_impact, "list_procedures_for_claim", fake_list_procedures_for_claim)
    monkeypatch.setattr(claim_impact, "mark_procedure_stale", fake_mark_stale)


# ---------------------------------------------------------------------
# find_procedures_referencing_claim
# ---------------------------------------------------------------------


def test_find_groups_typed_refs_by_strength(monkeypatch):
    _patch(monkeypatch, typed=[
        {"id": "p1", "name": "P1", "procedure_id": "fam1", "procedure_version": 2, "role": "PRECONDITION"},
        {"id": "p2", "name": "P2", "procedure_id": "fam2", "procedure_version": 1, "role": "APPLICABILITY"},
        {"id": "p3", "name": "P3", "procedure_id": "fam3", "procedure_version": 5, "role": "RATIONALE"},
    ], mark_calls=[])
    pool = _CompatPool(rows=[])

    grouped = _run(claim_impact.find_procedures_referencing_claim_grouped(pool, CLAIM))

    assert {p["id"] for p in grouped["strong"]} == {"p1", "p2"}
    assert {p["id"] for p in grouped["explanatory"]} == {"p3"}
    assert grouped["explanatory"][0]["role"] == "RATIONALE"
    # the compatibility scan was still consulted
    assert len(pool.calls) == 1


def test_find_unions_the_compat_precondition_scan_as_strong(monkeypatch):
    _patch(monkeypatch, typed=[], mark_calls=[])
    pool = _CompatPool(rows=[{"id": "legacy-proc", "name": "Legacy"}])

    grouped = _run(claim_impact.find_procedures_referencing_claim_grouped(pool, CLAIM))

    assert grouped["strong"] == [
        {"procedure_id": "legacy-proc", "procedure_version": None,
         "name": "Legacy", "role": "PRECONDITION", "id": "legacy-proc"}
    ]
    assert grouped["explanatory"] == []


def test_find_dedupes_a_procedure_present_in_both_sources(monkeypatch):
    # same procedure row id "p1" from the typed index and the compat scan
    _patch(monkeypatch, typed=[
        {"id": "p1", "name": "P1", "procedure_id": "fam1", "procedure_version": 2, "role": "PRECONDITION"},
    ], mark_calls=[])
    pool = _CompatPool(rows=[{"id": "p1", "name": "P1"}])

    grouped = _run(claim_impact.find_procedures_referencing_claim_grouped(pool, CLAIM))

    assert len(grouped["strong"]) == 1
    assert grouped["strong"][0]["procedure_id"] == "fam1"   # typed entry won
    assert grouped["explanatory"] == []


def test_find_keeps_a_strong_procedure_out_of_the_explanatory_group(monkeypatch):
    # p1 has BOTH an APPLICABILITY (strong) and a DECISION (explanatory) ref
    _patch(monkeypatch, typed=[
        {"id": "p1", "name": "P1", "procedure_id": "fam1", "procedure_version": 1, "role": "APPLICABILITY"},
        {"id": "p1", "name": "P1", "procedure_id": "fam1", "procedure_version": 1, "role": "DECISION"},
    ], mark_calls=[])
    pool = _CompatPool(rows=[])

    grouped = _run(claim_impact.find_procedures_referencing_claim_grouped(pool, CLAIM))

    assert [p["id"] for p in grouped["strong"]] == ["p1"]
    assert grouped["explanatory"] == []


def test_find_survives_a_typed_index_that_is_unavailable(monkeypatch):
    async def boom(pool, claim_id, *, roles=None):
        raise AssertionError("procedure_claim_refs not modelled by this fake")

    monkeypatch.setattr(claim_impact, "list_procedures_for_claim", boom)
    pool = _CompatPool(rows=[{"id": "legacy", "name": "L"}])

    grouped = _run(claim_impact.find_procedures_referencing_claim_grouped(pool, CLAIM))
    assert [p["id"] for p in grouped["strong"]] == ["legacy"]


def test_find_flat_wrapper_returns_the_old_shape(monkeypatch):
    _patch(monkeypatch, typed=[
        {"id": "p1", "name": "P1", "procedure_id": "fam1", "procedure_version": 2, "role": "PRECONDITION"},
        {"id": "p3", "name": "P3", "procedure_id": "fam3", "procedure_version": 5, "role": "RATIONALE"},
    ], mark_calls=[])
    pool = _CompatPool(rows=[])

    flat = _run(claim_impact.find_procedures_referencing_claim_flat(pool, CLAIM))
    assert {p["id"] for p in flat} == {"p1", "p3"}
    assert all("name" in p and "role" in p for p in flat)


# ---------------------------------------------------------------------
# propagate_claim_change
# ---------------------------------------------------------------------


def test_propagate_marks_stale_only_the_strong_group(monkeypatch):
    mark_calls: list[dict] = []
    _patch(monkeypatch, typed=[
        {"id": "p1", "name": "P1", "procedure_id": "fam1", "procedure_version": 2, "role": "PRECONDITION"},
        {"id": "p2", "name": "P2", "procedure_id": "fam2", "procedure_version": 1, "role": "ASSUMPTION"},
        {"id": "p3", "name": "P3", "procedure_id": "fam3", "procedure_version": 5, "role": "EXPECTED_EFFECT"},
        {"id": "p4", "name": "P4", "procedure_id": "fam4", "procedure_version": 1, "role": "VERIFICATION"},
    ], mark_calls=mark_calls)
    pool = _CompatPool(rows=[])

    result = _run(claim_impact.propagate_claim_change(pool, CLAIM, reason="contradicted"))

    assert result["marked_stale"] == ["p1", "p2"]
    assert {p["id"] for p in result["explanatory_untouched"]} == {"p3", "p4"}
    assert [c["procedure_row_id"] for c in mark_calls] == ["p1", "p2"]
    assert all(c["reason"] == "contradicted" for c in mark_calls)
    assert all(c["detected_by"] == "claim_impact" for c in mark_calls)


def test_propagate_is_a_noop_when_nothing_references_the_claim(monkeypatch):
    mark_calls: list[dict] = []
    _patch(monkeypatch, typed=[], mark_calls=mark_calls)
    pool = _CompatPool(rows=[])

    result = _run(claim_impact.propagate_claim_change(pool, CLAIM, reason="x"))

    assert result == {"marked_stale": [], "explanatory_untouched": []}
    assert mark_calls == []


def test_propagate_never_marks_an_explanatory_only_procedure(monkeypatch):
    mark_calls: list[dict] = []
    _patch(monkeypatch, typed=[
        {"id": "p3", "name": "P3", "procedure_id": "fam3", "procedure_version": 5, "role": "FAILURE_MODE"},
    ], mark_calls=mark_calls)
    pool = _CompatPool(rows=[])

    result = _run(claim_impact.propagate_claim_change(pool, CLAIM, reason="x"))

    assert result["marked_stale"] == []
    assert [p["id"] for p in result["explanatory_untouched"]] == ["p3"]
    assert mark_calls == []


def test_propagate_default_detected_by_is_the_module_name():
    import inspect
    sig = inspect.signature(claim_impact.propagate_claim_change)
    assert sig.parameters["detected_by"].default == "claim_impact"
