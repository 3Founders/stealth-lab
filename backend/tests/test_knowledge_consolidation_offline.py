"""
Offline (no real DB) tests for the knowledge/verification/ranking
consolidation pass: the canonical evidence-trust classifier
(app.services.evidence_trust), the canonical ranking bucket/sort logic
(app.economy.ranking), and the duplicate-vs-improvement decision
(app.economy.verification.evaluate_layer2's parent_similarity handling).

DB-dependent invariants (exact version linkage, the A/B/C parent-
attribution chain across supersession, benchmark used/validated derived
from real `evaluations` rows) live in tests/test_knowledge_consolidation_e2e.py,
gated on DATABASE_URL like every other *_e2e.py file in this suite.
"""
from __future__ import annotations

import asyncio

from app.economy import ranking
from app.services import evidence_trust as et


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Canonical evidence trust (Part 9 vocabulary)
# ---------------------------------------------------------------------------

def test_verified_success_requires_both_type_and_trusted_writer():
    real = {"evidence_type": "execution_result", "outcome_status": "success", "created_by": et.OUTCOME_WRITER_STAMP}
    assert et.trust_state(real) == et.VERIFIED_SUCCESS

    # Same outcome_status, but NOT the trusted writer -- a self-report or
    # LLM judgment must never read as verified.
    claimed = {"evidence_type": "human_review", "outcome_status": "success", "created_by": "someone"}
    assert et.trust_state(claimed) == et.CLAIMED_SUCCESS

    # Right evidence_type, but NOT the trusted writer -- exactly the gap
    # the consolidation pass closes: type alone is not enough.
    untrusted_execution_type = {"evidence_type": "execution_result", "outcome_status": "success", "created_by": "someone-else"}
    assert et.trust_state(untrusted_execution_type) == et.CLAIMED_SUCCESS


def test_verified_failure_requires_trusted_writer_too():
    real_failure = {"evidence_type": "reproduction", "outcome_status": "failure", "created_by": et.OUTCOME_WRITER_STAMP}
    assert et.trust_state(real_failure) == et.VERIFIED_FAILURE

    # No CLAIMED_FAILURE in the spec's 4-state vocabulary -- an unverified
    # failure claim folds into UNKNOWN, never silently becomes "verified".
    unverified_failure = {"evidence_type": "reproduction", "outcome_status": "failure", "created_by": "someone"}
    assert et.trust_state(unverified_failure) == et.UNKNOWN


def test_non_outcome_bearing_evidence_type_can_never_reach_verified():
    """A document/human_review/observation row can carry a claim -- it can
    never become VERIFIED, even if (implausibly) created_by happened to
    match the trusted writer stamp; only a real execution_result/
    reproduction row can."""
    doc = {"evidence_type": "document", "outcome_status": "success", "created_by": et.OUTCOME_WRITER_STAMP}
    assert et.trust_state(doc) == et.CLAIMED_SUCCESS

    doc_no_outcome = {"evidence_type": "document", "outcome_status": None, "created_by": et.OUTCOME_WRITER_STAMP}
    assert et.trust_state(doc_no_outcome) == et.UNKNOWN


def test_summarize_success_count_means_verified_only():
    """The exact bug this consolidation fixes: `success_count` used to
    count ANY row with outcome_status='success'. It must now count
    verified_success only."""
    rows = [
        {"evidence_type": "execution_result", "outcome_status": "success", "created_by": et.OUTCOME_WRITER_STAMP},
        {"evidence_type": "human_review", "outcome_status": "success", "created_by": "a-human"},  # claimed, not verified
        {"evidence_type": "execution_result", "outcome_status": "failure", "created_by": et.OUTCOME_WRITER_STAMP},
        {"evidence_type": "document", "outcome_status": None, "created_by": "someone"},
    ]
    summary = et.summarize(rows)
    assert summary["verified_success"] == 1
    assert summary["claimed_success"] == 1
    assert summary["verified_failure"] == 1
    assert summary["unknown"] == 1
    assert summary["success_count"] == 1, "success_count must mean VERIFIED success, not any success-labelled row"
    assert summary["failure_count"] == 1
    assert summary["total"] == 4


# ---------------------------------------------------------------------------
# Canonical ranking bucket (Part 7)
# ---------------------------------------------------------------------------

def test_bucket_verified_state_wins_regardless_of_capability():
    capability = {"evidence_count": 0, "success_count": 0}
    assert ranking.verification_bucket("verified", capability) == "verified"


def test_bucket_needs_evidence_when_nothing_recorded():
    assert ranking.verification_bucket("candidate", {"evidence_count": 0, "success_count": 0}) == "needs_evidence"


def test_bucket_verified_failure_when_all_evidence_is_failure():
    assert ranking.verification_bucket("candidate", {"evidence_count": 3, "success_count": 0}) == "verified_failure"


def test_bucket_candidate_when_mixed_or_insufficient_for_verified():
    assert ranking.verification_bucket("candidate", {"evidence_count": 3, "success_count": 2}) == "candidate"


def test_rank_procedures_for_goal_orders_by_bucket_then_wilson_lower_bound(monkeypatch):
    """Offline: get_solution_view is monkeypatched with fixed fixtures --
    proves the SORT/BUCKET logic without a database. A specialized
    procedure whose applicability matches the given context is bonus-
    weighted, never penalized for narrow applicability (Part 6)."""
    fixtures = {
        "p-verified": {
            "procedure_row_id": "p-verified", "procedure_id": "pid-1", "version": 1,
            "display_name": "Verified way", "display_description": "d", "applicability_summary": "general purpose",
            "created_by": "alice", "verification_state": "verified", "staleness": "fresh",
            "capability": {"p_lower": 0.5, "p_upper": 0.9, "evidence_count": 12, "success_count": 12, "independent_groups": 3},
            "cost": {},
        },
        "p-niche-candidate": {
            "procedure_row_id": "p-niche-candidate", "procedure_id": "pid-2", "version": 1,
            "display_name": "Niche way", "display_description": "d", "applicability_summary": "kubernetes clusters only",
            "created_by": "bob", "verification_state": "candidate", "staleness": "fresh",
            "capability": {"p_lower": 0.3, "p_upper": 0.8, "evidence_count": 4, "success_count": 3, "independent_groups": 1},
            "cost": {},
        },
        "p-needs-evidence": {
            "procedure_row_id": "p-needs-evidence", "procedure_id": "pid-3", "version": 1,
            "display_name": "New way", "display_description": "d", "applicability_summary": "",
            "created_by": "carol", "verification_state": "candidate", "staleness": "fresh",
            "capability": {"p_lower": 0.0, "p_upper": 1.0, "evidence_count": 0, "success_count": 0, "independent_groups": 0},
            "cost": {},
        },
        "p-disproven": {
            "procedure_row_id": "p-disproven", "procedure_id": "pid-4", "version": 1,
            "display_name": "Failed way", "display_description": "d", "applicability_summary": "",
            "created_by": "dave", "verification_state": "candidate", "staleness": "fresh",
            "capability": {"p_lower": 0.0, "p_upper": 0.3, "evidence_count": 5, "success_count": 0, "independent_groups": 1},
            "cost": {},
        },
    }

    async def fake_get_solution_view(pool, procedure_row_id, *, scope):
        return fixtures.get(procedure_row_id)

    monkeypatch.setattr(ranking, "get_solution_view", fake_get_solution_view)

    ranked = _run(ranking.rank_procedures_for_goal(
        pool=object(), procedure_row_ids=list(fixtures.keys()), scope=object(), context_key=None,
    ))

    buckets_in_order = [r["bucket"] for r in ranked]
    assert buckets_in_order == ["verified", "candidate", "needs_evidence", "verified_failure"], buckets_in_order
    assert ranked[0]["procedure_row_id"] == "p-verified"
    assert ranked[-1]["procedure_row_id"] == "p-disproven"
    for i, r in enumerate(ranked):
        assert r["rank"] == i + 1
        assert r["of"] == len(ranked)


def test_niche_procedure_is_not_penalized_for_matching_context(monkeypatch):
    """Part 6: a specialized procedure whose applicability matches the
    caller's context must be able to outrank a more general one with
    similar evidence -- narrowness is not itself a penalty."""
    fixtures = {
        "general": {
            "procedure_row_id": "general", "procedure_id": "pid-1", "version": 1,
            "display_name": "General", "display_description": "d", "applicability_summary": "any environment",
            "created_by": "alice", "verification_state": "candidate", "staleness": "fresh",
            "capability": {"p_lower": 0.40, "p_upper": 0.8, "evidence_count": 5, "success_count": 4, "independent_groups": 2},
            "cost": {},
        },
        "specialized": {
            "procedure_row_id": "specialized", "procedure_id": "pid-2", "version": 1,
            "display_name": "Kubernetes-specific", "display_description": "d", "applicability_summary": "kubernetes clusters with istio",
            "created_by": "bob", "verification_state": "candidate", "staleness": "fresh",
            "capability": {"p_lower": 0.39, "p_upper": 0.8, "evidence_count": 5, "success_count": 4, "independent_groups": 2},
            "cost": {},
        },
    }

    async def fake_get_solution_view(pool, procedure_row_id, *, scope):
        return fixtures.get(procedure_row_id)

    monkeypatch.setattr(ranking, "get_solution_view", fake_get_solution_view)

    ranked = _run(ranking.rank_procedures_for_goal(
        pool=object(), procedure_row_ids=list(fixtures.keys()), scope=object(), context_key="kubernetes",
    ))
    assert ranked[0]["procedure_row_id"] == "specialized", "a matching-context specialist with comparable evidence should outrank the generalist"
    assert ranked[0]["context_matched"] is True


# ---------------------------------------------------------------------------
# Duplicate vs meaningful improvement (Part 3)
# ---------------------------------------------------------------------------

def test_near_identical_rewrite_of_declared_parent_is_rejected():
    from app.economy.verification import evaluate_layer2

    async def _run_it():
        return await evaluate_layer2(
            submission={"name": "x"}, duplicate={"score": None, "best_match_kind": None},
            layer1={"passed": True, "issues": []},
            parent_similarity={"score": 0.985, "method": "cosine_vs_parent"},
        )
    result = _run(_run_it())
    assert result["decision"] == "reject"
    assert "parent" in result["notes"][0]


def test_moderately_similar_improvement_is_flagged_for_review_not_rejected():
    from app.economy.verification import evaluate_layer2

    async def _run_it():
        return await evaluate_layer2(
            submission={"name": "x"}, duplicate={"score": None, "best_match_kind": None},
            layer1={"passed": True, "issues": []},
            parent_similarity={"score": 0.90, "method": "cosine_vs_parent"},
        )
    result = _run(_run_it())
    assert result["decision"] == "needs_review"
    assert "substantive change" in result["notes"][0]


def test_genuinely_different_improvement_is_not_penalized_for_similarity():
    """A specialized adaptation that is still meaningfully different from
    its parent must not be auto-rejected merely for textual overlap."""
    from app.economy.verification import evaluate_layer2

    async def _run_it():
        return await evaluate_layer2(
            submission={"name": "x"}, duplicate={"score": None, "best_match_kind": None},
            layer1={"passed": True, "issues": []},
            parent_similarity={"score": 0.55, "method": "cosine_vs_parent"},
        )
    result = _run(_run_it())
    assert result["decision"] == "candidate"
