"""
DB-free coverage for claim_conditioned_retrieval.py's orchestrator:
find_applicable_procedures() survivors -> per-candidate Claim retrieval ->
judge -> hard filter (REQUIRED-only) -> rerank -> explanation. Follows the
FakePool convention established in test_applicability_hard_constraints_offline.py.

find_applicable_procedures itself is monkeypatched (its own decision logic
is covered by that file and test_applicability_cascade_offline.py) so these
tests isolate the NEW second-stage logic this module adds.
"""
import asyncio

import pytest

from app.services.applicability_judge import ApplicabilityJudgment, MockJudge
from app.services.claim_conditioned_retrieval import (
    compute_policy_score,
    find_applicable_candidates,
)


def _run(coro):
    return asyncio.run(coro)


def _procedure(pid="p1", version=1, **overrides):
    row = {
        "id": pid, "procedure_id": pid, "version": version,
        "name": f"proc-{pid}", "goal": "do the thing",
        "verification_state": "verified", "preconditions": [],
        "expected_effects": [], "_similarity_score": 0.8,
    }
    row.update(overrides)
    return row


class FakePool:
    """No cache table reachable (no fetchrow/execute) -- exercises the
    orchestrator's cache-miss-goes-to-judge path without needing a real
    applicability_judgment_cache table. `fetch` answers ONLY
    _capability_ranked_hits' evidence query (the one real DB read this
    orchestrator's rerank stage always makes, judge outcome notwithstanding)
    with an empty evidence stream -- capability_for_stream degrades that
    honestly to p_estimate=0.0, matching applicability.py's own documented
    behavior for a survivor with no recorded evidence."""

    async def fetch(self, sql, *params):
        return []


def _patch_find(monkeypatch, survivors):
    async def fake_find(pool, **kwargs):
        return survivors
    monkeypatch.setattr(
        "app.services.claim_conditioned_retrieval.find_applicable_procedures", fake_find,
    )


def _patch_relevant_claims(monkeypatch, claims_by_procedure_goal):
    async def fake_get_relevant_claims(pool, *, goal, top_k, access_scope=None):
        for key, claims in claims_by_procedure_goal.items():
            if key in goal:
                return claims
        return []
    monkeypatch.setattr(
        "app.services.claim_conditioned_retrieval.get_relevant_claims", fake_get_relevant_claims,
    )


def _patch_capability(monkeypatch):
    async def fake_capability(pool, survivors):
        return [(s["id"], "procedures", i) for i, s in enumerate(survivors)]
    monkeypatch.setattr(
        "app.services.claim_conditioned_retrieval._capability_ranked_hits", fake_capability,
    )


# --------------------------------------------------------------- fallback behavior


def test_no_judge_is_unavailable_and_never_returns_a_similarity_ranking(monkeypatch):
    survivors = [_procedure("p1"), _procedure("p2")]
    _patch_find(monkeypatch, survivors)

    result = _run(find_applicable_candidates(
        FakePool(), goal_text="fix the bug", judge=None, limit=10,
    ))
    assert result.contextual_judgment_status == "SEMANTIC_JUDGMENT_UNAVAILABLE"
    assert result.candidates == []  # embedding-only ranking is NOT passed off as validated
    assert result.unjudged_candidate_ids == ["p1", "p2"]


def test_explicit_caller_opt_out_returns_similarity_order_labelled_not_requested(monkeypatch):
    _patch_find(monkeypatch, [_procedure("p1"), _procedure("p2")])
    result = _run(find_applicable_candidates(
        FakePool(), goal_text="fix the bug", judge=None, claim_conditioned=False, limit=10,
    ))
    assert result.contextual_judgment_status == "not_requested"
    assert [c.procedure["id"] for c in result.candidates] == ["p1", "p2"]


def test_no_survivors_short_circuits_cleanly(monkeypatch):
    _patch_find(monkeypatch, [])
    result = _run(find_applicable_candidates(
        FakePool(), goal_text="fix the bug", judge=MockJudge(), limit=10,
    ))
    assert result.candidates == []
    assert result.contextual_judgment_status == "ok"


# --------------------------------------------------------------- three-valued semantics


def test_required_contradiction_hard_filters_the_candidate(monkeypatch):
    """CASE B: a REQUIRED precondition strongly contradicted by a Claim
    removes the candidate entirely, not just demotes it."""
    survivors = [_procedure("p1", preconditions=[
        {"subject": "docker", "predicate": "available", "object": True},
    ])]
    _patch_find(monkeypatch, survivors)
    _patch_relevant_claims(monkeypatch, {
        "do the thing": [{"claim_id": "c1", "statement": "docker available is unavailable"}],
    })
    _patch_capability(monkeypatch)

    result = _run(find_applicable_candidates(
        FakePool(), goal_text="fix the bug", judge=MockJudge(), limit=10,
    ))
    assert result.candidates == []
    assert result.observability.verdict_counts.get("INAPPLICABLE") == 1


def test_unknown_requirement_never_hard_filters_the_candidate(monkeypatch):
    """CASE C: no Claim exists for a REQUIRED condition -> UNKNOWN, the
    candidate survives (demoted, never rejected)."""
    survivors = [_procedure("p1", preconditions=[
        {"subject": "docker", "predicate": "available", "object": True},
    ])]
    _patch_find(monkeypatch, survivors)
    _patch_relevant_claims(monkeypatch, {})  # no relevant claims at all
    _patch_capability(monkeypatch)

    result = _run(find_applicable_candidates(
        FakePool(), goal_text="fix the bug", judge=MockJudge(), limit=10,
    ))
    assert len(result.candidates) == 1
    assert result.candidates[0].judgment.verdict == "UNKNOWN"


def test_preferred_contradiction_demotes_but_does_not_reject(monkeypatch):
    """Sec 7/8: only REQUIRED conditions hard-reject. A PREFERRED
    condition's contradiction should never remove the candidate."""
    survivors = [_procedure("p1")]
    _patch_find(monkeypatch, survivors)
    _patch_capability(monkeypatch)

    class _PreferredJudge:
        model = "test"
        model_version = "v1"

        async def judge_batch(self, goal, candidates):
            return [
                ApplicabilityJudgment(
                    candidate_id=c.candidate_id, goal_or_query=goal,
                    applicability_probability=0.4, contradiction_probability=0.9,
                    preconditions_met_probability=0.5, verdict="INAPPLICABLE",
                    blocking_claim_ids=["c-gpu"], model="test",
                )
                for c in candidates
            ]

    _patch_relevant_claims(monkeypatch, {})
    result = _run(find_applicable_candidates(
        FakePool(), goal_text="use a GPU if possible", judge=_PreferredJudge(), limit=10,
    ))
    # No REQUIRED conditions on this candidate at all -> _is_hard_rejected's
    # own "has_required" guard means an INAPPLICABLE verdict here still
    # cannot hard-reject.
    assert len(result.candidates) == 1


# --------------------------------------------------------------- judge failure fallback


def test_judge_raising_yields_pending_not_a_fabricated_ranking(monkeypatch):
    survivors = [_procedure("p1")]
    _patch_find(monkeypatch, survivors)
    _patch_relevant_claims(monkeypatch, {})
    _patch_capability(monkeypatch)

    class _BrokenJudge:
        model = "broken"
        model_version = "v1"

        async def judge_batch(self, goal, candidates):
            raise RuntimeError("judge service down")

    result = _run(find_applicable_candidates(
        FakePool(), goal_text="fix the bug", judge=_BrokenJudge(), limit=10,
    ))
    assert result.contextual_judgment_status == "PENDING_SEMANTIC_JUDGMENT"
    assert result.candidates == []
    assert result.observability.pending_judgments == 1


# --------------------------------------------------------------- ranking


def test_compute_policy_score_rewards_claim_fit_and_penalizes_risk():
    from app.services.claim_conditioned_retrieval import RankedCandidate

    strong = RankedCandidate(
        procedure={}, judgment=None, semantic_relevance=0.9, claim_fit=0.9,
        evidence_strength=0.9, verified_success=0.9, cost_estimate=None,
        latency_estimate=None, risk=0.0, final_policy_score=0.0,
    )
    risky = RankedCandidate(
        procedure={}, judgment=None, semantic_relevance=0.9, claim_fit=0.2,
        evidence_strength=0.2, verified_success=0.2, cost_estimate=None,
        latency_estimate=None, risk=0.9, final_policy_score=0.0,
    )
    assert compute_policy_score(strong) > compute_policy_score(risky)


def test_explanation_exposes_claim_ids_not_only_prose(monkeypatch):
    survivors = [_procedure("p1", preconditions=[
        {"subject": "generated_client", "predicate": "is", "object": "derived"},
    ])]
    _patch_find(monkeypatch, survivors)
    _patch_relevant_claims(monkeypatch, {
        "do the thing": [
            {"claim_id": "C17", "statement": "generated_client is derived from schema.yaml"},
        ],
    })
    _patch_capability(monkeypatch)

    result = _run(find_applicable_candidates(
        FakePool(), goal_text="modify generated API client", judge=MockJudge(), limit=10,
    ))
    assert len(result.candidates) == 1
    explanation = result.candidates[0].explanation()
    assert explanation["procedure_id"] == "p1"
    assert "verdict" in explanation and "scores" in explanation
    # Supported (no negation marker in the claim statement) -> C17 should
    # appear as a supporting id, exposed as an ID, not just prose.
    assert "C17" in explanation["supporting_claim_ids"]
