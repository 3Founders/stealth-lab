"""
DB-free coverage for check_procedure_reuse() (applicability.py) -- the
decision core behind the MCP server's check_procedure tool (demo.md C5).

Same FakePool house style as test_applicability_hard_constraints_offline.py
(procedure fixtures) and test_band2_4_handlers.py (fragment-routed fake
pool for the capability-recompute evidence stream) -- this file just wires
those two proven patterns together for the one new orchestration function.
"""
import asyncio

import pytest

from app.services.applicability import (
    CHECK_PROCEDURE_REQUIRE_VERIFIED,
    ProcedureNotFound,
    check_procedure_reuse,
)
from app.services.procedure_extraction.failure_handlers import capability_for_stream

PROC_ROW_ID = "00000000-0000-4000-8000-000000000001"
PROC_STABLE_ID = "00000000-0000-4000-8000-0000000000aa"
CLAIM_ID = "00000000-0000-4000-8000-0000000000c1"
SUPERSEDER_ID = "00000000-0000-4000-8000-0000000000c2"


def _procedure(**overrides):
    row = {
        "id": PROC_ROW_ID,
        "procedure_id": PROC_STABLE_ID,
        "version": 1,
        "name": "pagination procedure",
        "t_invalid": None,
        "staleness": "fresh",
        "availability": "active",
        "verification_state": "verified",
        "approval_status": "approved",
        "scope": {},
        "exclusions": [],
        "preconditions": [],
        "invariants": [],
    }
    row.update(overrides)
    return row


class FakePool:
    """Answers each of check_procedure_reuse's three real query shapes
    (procedure lookup, claim lookup for a failed precondition, SUPERSEDES
    edge lookup) plus project_state()'s own IN-claims fetch and the
    evidence-stream fetch -- routed by SQL fragment, same discipline as
    test_band2_4_handlers.py's FakePool."""

    def __init__(self, *, procedure=None, project_state_claims=(),
                 claim=None, superseder=None, evidence_stream=()):
        self.procedure = procedure
        self.project_state_claims = list(project_state_claims)
        self.claim = claim
        self.superseder = superseder
        self.evidence_stream = list(evidence_stream)
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *params):
        norm = " ".join(sql.split())
        self.fetchrow_calls.append((norm, params))
        if "FROM procedures WHERE procedure_id" in norm:
            return self.procedure
        if "FROM knowledge_nodes WHERE node_type = 'claim'" in norm:
            return self.claim
        if "FROM edges WHERE edge_type = 'SUPERSEDES'" in norm:
            return self.superseder
        raise AssertionError(f"unexpected fetchrow SQL: {norm}")

    async def fetch(self, sql, *params):
        norm = " ".join(sql.split())
        self.fetch_calls.append((norm, params))
        if "FROM evidence WHERE target_type" in norm:
            return self.evidence_stream
        if "FROM knowledge_nodes" in norm:  # project_state()'s IN-claims fetch
            return self.project_state_claims
        raise AssertionError(f"unexpected fetch SQL: {norm}")


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- not found

def test_unknown_procedure_id_raises_procedure_not_found():
    pool = FakePool(procedure=None)
    with pytest.raises(ProcedureNotFound):
        _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))


def test_malformed_uuid_raises_procedure_not_found_without_touching_the_pool():
    pool = FakePool(procedure=None)
    with pytest.raises(ProcedureNotFound):
        _run(check_procedure_reuse(pool, procedure_id="not-a-uuid"))
    assert pool.fetchrow_calls == []


# --------------------------------------------------------------- ALLOW path

def test_fresh_verified_procedure_with_no_preconditions_allows():
    pool = FakePool(procedure=_procedure())
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "ALLOW"
    assert result.procedure == "pagination procedure"
    assert f"procedure:{PROC_ROW_ID}" in result.evidence
    assert "0 successes / 0 attempts" in result.capability_note
    assert "level=unknown" in result.capability_note
    assert "routing=refuse_reuse" in result.capability_note


def test_explicit_invocation_allows_even_when_unverified_and_unapproved():
    """The whole point of CHECK_PROCEDURE_REQUIRE_VERIFIED=False (ticket
    13's named exception): a caller naming this procedure_id explicitly
    is not blocked by verification_state/approval_status alone."""
    assert CHECK_PROCEDURE_REQUIRE_VERIFIED is False
    pool = FakePool(procedure=_procedure(verification_state="proposed",
                                         approval_status="proposed"))
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "ALLOW"


def test_satisfied_precondition_allows_via_project_state():
    procedure = _procedure(preconditions=[
        {"subject": "project:p", "predicate": "has_test_runner", "object": "pytest"},
    ])
    pool = FakePool(
        procedure=procedure,
        project_state_claims=[{
            "id": CLAIM_ID,
            "properties": {"predicate": "has_test_runner", "object": "pytest"},
            "t_valid": None,
            "t_invalid": None,
        }],
    )
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "ALLOW"


def test_evidence_stream_feeds_capability_note_identically_to_capability_for_stream():
    stream = [
        {"outcome_status": "success", "context_key": "ctx-a", "independence_group": "g1"},
        {"outcome_status": "success", "context_key": "ctx-b", "independence_group": "g2"},
        {"outcome_status": "failure", "context_key": "ctx-a", "independence_group": None},
    ]
    pool = FakePool(procedure=_procedure(), evidence_stream=stream)
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))

    expected = capability_for_stream("procedure", PROC_ROW_ID, stream)
    assert f"{expected.success_count} successes / {expected.evidence_count} attempts" \
        in result.capability_note
    assert f"level={expected.level_label}" in result.capability_note

    stream_reads = [c for c in pool.fetch_calls if "FROM evidence WHERE target_type" in c[0]]
    assert len(stream_reads) == 1
    sql, args = stream_reads[0]
    assert "target_type = 'procedure'" in sql
    assert "target_version = $2" in sql
    assert args == (PROC_ROW_ID, 1)


# ------------------------------------------------------------ WOULD_REFUSE

def test_stale_procedure_refuses_citing_staleness():
    pool = FakePool(procedure=_procedure(staleness="stale"))
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "WOULD_REFUSE"
    assert "stale" in result.reason
    assert result.evidence == [f"procedure:{PROC_ROW_ID}"]


def test_non_active_availability_refuses():
    pool = FakePool(procedure=_procedure(availability="quarantined"))
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "WOULD_REFUSE"
    assert "quarantined" in result.reason


def test_precondition_with_no_claim_at_all_refuses_cwa_fail_closed():
    procedure = _procedure(preconditions=[
        {"subject": "project:p", "predicate": "has_test_runner", "object": "pytest"},
    ])
    pool = FakePool(procedure=procedure, project_state_claims=[], claim=None)
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "WOULD_REFUSE"
    assert "no claim satisfies precondition" in result.reason
    assert "CWA fail-closed" in result.reason
    assert result.evidence == [f"procedure:{PROC_ROW_ID}"]


def test_precondition_claim_believed_in_but_wrong_object_refuses_with_the_real_mismatch():
    procedure = _procedure(preconditions=[
        {"subject": "project:p", "predicate": "pydantic_version", "object": "v1"},
    ])
    pool = FakePool(
        procedure=procedure,
        project_state_claims=[],  # the IN-claim project_state() sees doesn't match object=v1
        claim={"id": CLAIM_ID, "properties": {
            "truth_state": "IN", "object": "v2",
        }},
    )
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "WOULD_REFUSE"
    assert "currently holds object='v2'" in result.reason
    assert "required 'v1'" in result.reason
    assert result.evidence == [f"claim:{CLAIM_ID}"]


def test_precondition_claim_superseded_names_both_claims_demo_md_style():
    """The demo.md §3 headline example: 'precondition claim cl_17 ...
    superseded by cl_23' -- this proves the real SUPERSEDES-edge lookup
    produces exactly that shape, citing both real claim ids as evidence."""
    procedure = _procedure(preconditions=[
        {"subject": "project:p", "predicate": "pydantic_version", "object": "v1"},
    ])
    pool = FakePool(
        procedure=procedure,
        project_state_claims=[],
        claim={"id": CLAIM_ID, "properties": {"truth_state": "OUT", "object": "v1"}},
        superseder={"source_id": SUPERSEDER_ID},
    )
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "WOULD_REFUSE"
    assert f"precondition claim {CLAIM_ID}" in result.reason
    assert f"superseded by {SUPERSEDER_ID}" in result.reason
    assert result.evidence == [f"claim:{CLAIM_ID}", f"claim:{SUPERSEDER_ID}"]


def test_precondition_claim_out_with_no_superseder_edge_discloses_the_gap_honestly():
    procedure = _procedure(preconditions=[
        {"subject": "project:p", "predicate": "pydantic_version", "object": "v1"},
    ])
    pool = FakePool(
        procedure=procedure,
        project_state_claims=[],
        claim={"id": CLAIM_ID, "properties": {"truth_state": "OUT", "object": "v1"}},
        superseder=None,
    )
    result = _run(check_procedure_reuse(pool, procedure_id=PROC_STABLE_ID))
    assert result.verdict == "WOULD_REFUSE"
    assert "no SUPERSEDES edge recorded" in result.reason
    assert result.evidence == [f"claim:{CLAIM_ID}"]
