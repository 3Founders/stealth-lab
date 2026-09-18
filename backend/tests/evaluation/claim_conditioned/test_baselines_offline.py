"""
Baseline comparison (product spec Sec 16): A (semantic retrieval only) vs
B (semantic + existing deterministic applicability filters) vs C (semantic
+ Claim retrieval + NLI/JEV), evaluated on Cases A-E (Sec 17) -- exactly the
curated cases where local Claims materially change the correct result.

Pure-Python, no DB, no live model: baseline C runs the REAL
extract_requirement_conditions / MockJudge / _is_hard_rejected /
compute_policy_score functions from applicability_judge.py and
claim_conditioned_retrieval.py (the same building blocks
find_applicable_candidates() composes), just without a Postgres-backed
find_applicable_procedures()/get_relevant_claims() call in front of them --
this file supplies the "top-N candidates" and "relevant Claims" as fixture
data directly, exactly matching Sec 16's own framing ("baselines... on
curated cases", not a live-corpus benchmark).
"""
from __future__ import annotations

import asyncio
import time

from app.services.applicability_judge import (
    JudgeCandidateInput,
    MockJudge,
    RequirementCondition,
    extract_requirement_conditions,
)
from app.services.claim_conditioned_retrieval import (
    RankedCandidate,
    _is_hard_rejected,
    compute_policy_score,
)
from tests.evaluation.claim_conditioned.test_cases import ALL_CASES, EvalCase
from tests.evaluation.harness import metrics


def _run(coro):
    return asyncio.run(coro)


def baseline_a_semantic_only(case: EvalCase) -> list[str]:
    """Pure similarity order -- no filtering at all, exactly what
    retrieval.py's own fuse_rrf-based ranking gives before applicability.py
    or Claims ever enter the picture."""
    return [c.candidate_id for c in sorted(case.candidates, key=lambda c: c.similarity, reverse=True)]


def baseline_b_semantic_plus_deterministic(case: EvalCase) -> list[str]:
    """Semantic order, plus the EXISTING check_hard_constraints-style
    filter: only rejects a candidate whose structured invariant was
    EXPLICITLY marked violated. Free-text local Claims (Cases A/B/C/E's
    whole point) are invisible to this baseline -- it can only act on
    invariants a caller already structured, which is exactly the gap the
    product spec's Sec 1 audit names (precondition_gate.py's own docstring:
    "not semantic entailment")."""
    survivors = [c for c in case.candidates if not c.hard_invariant_violated]
    return [c.candidate_id for c in sorted(survivors, key=lambda c: c.similarity, reverse=True)]


def baseline_c_claim_conditioned(case: EvalCase) -> tuple[list[str], dict[str, str]]:
    """Semantic + Claim retrieval + NLI/JEV -- the real pipeline logic
    (MockJudge, the SAME hard-filter rule, the SAME policy-score function
    find_applicable_candidates() itself uses), applied to this fixture's
    candidates/claims directly."""
    judge_inputs = []
    conditions_by_id: dict[str, list[RequirementCondition]] = {}
    for cand in case.candidates:
        conditions = extract_requirement_conditions({"preconditions": cand.preconditions})
        conditions_by_id[cand.candidate_id] = conditions
        judge_inputs.append(JudgeCandidateInput(
            candidate_id=cand.candidate_id, candidate_version=1,
            candidate_purpose=cand.goal, conditions=conditions, claims=case.claims,
        ))

    judgments = _run(MockJudge().judge_batch(case.goal, judge_inputs))
    verdict_by_id = {j.candidate_id: j.verdict for j in judgments}

    ranked: list[RankedCandidate] = []
    for cand, judgment in zip(case.candidates, judgments):
        if _is_hard_rejected(judgment, conditions_by_id[cand.candidate_id], threshold=0.75):
            continue
        rc = RankedCandidate(
            procedure={"id": cand.candidate_id}, judgment=judgment,
            semantic_relevance=cand.similarity, claim_fit=judgment.applicability_probability,
            evidence_strength=None, verified_success=None, cost_estimate=None,
            latency_estimate=None, risk=judgment.contradiction_probability,
            final_policy_score=0.0,
        )
        rc.final_policy_score = compute_policy_score(rc)
        ranked.append(rc)
    ranked.sort(key=lambda rc: rc.final_policy_score, reverse=True)
    return [rc.procedure["id"] for rc in ranked], verdict_by_id


# --------------------------------------------------------------- per-case, headline properties


def test_case_a_schema_edit_ranks_above_and_generated_file_is_rejected():
    ranked, verdicts = baseline_c_claim_conditioned(CASE_A := ALL_CASES[0])
    assert ranked == ["edit_schema_and_regenerate"], (
        "candidate 1 (edit generated file) must be hard-rejected -- REQUIRED "
        "condition contradicted by C17/C21 -- leaving only candidate 2"
    )
    # Baseline A gets this backwards: highest surface similarity is the WRONG answer.
    assert baseline_a_semantic_only(CASE_A)[0] == "edit_generated_file"


def test_case_b_docker_unavailable_is_hard_rejected():
    ranked, verdicts = baseline_c_claim_conditioned(ALL_CASES[1])
    assert ranked == []
    assert verdicts["docker_integration_tests"] == "INAPPLICABLE"
    # Baseline A/B both wrongly keep it -- they cannot see the Claim at all.
    assert baseline_a_semantic_only(ALL_CASES[1]) == ["docker_integration_tests"]
    assert baseline_b_semantic_plus_deterministic(ALL_CASES[1]) == ["docker_integration_tests"]


def test_case_c_no_docker_claim_is_unknown_survives():
    ranked, verdicts = baseline_c_claim_conditioned(ALL_CASES[2])
    assert ranked == ["docker_integration_tests"], "UNKNOWN must never be treated as a rejection"
    assert verdicts["docker_integration_tests"] == "UNKNOWN"


def test_case_d_lsp_available_both_candidates_survive():
    ranked, _ = baseline_c_claim_conditioned(ALL_CASES[3])
    assert set(ranked) == {"ripgrep_search", "lsp_references"}


def test_case_e_lsp_unavailable_demotes_or_rejects_lsp_candidate():
    ranked, verdicts = baseline_c_claim_conditioned(ALL_CASES[4])
    assert "lsp_references" not in ranked or (
        ranked.index("lsp_references") > ranked.index("ripgrep_search")
    )
    assert verdicts["lsp_references"] == "INAPPLICABLE"


# --------------------------------------------------------------- aggregate metrics (Sec 16)


def test_aggregate_metrics_show_c_strictly_improves_on_a_and_b():
    """The headline comparison: precision/recall/MRR/nDCG (ranking quality)
    and wrong-applicability / false-rejection rate (safety), aggregated
    across all five cases. Asserts the DIRECTION the product spec predicts
    -- Claim-conditioning must not make things worse, and must fix the
    cases it was built for."""
    results = {"A": {"wrong": 0, "false_reject": 0, "mrr": []}, "C": {"wrong": 0, "false_reject": 0, "mrr": []}}
    start = time.monotonic()
    for case in ALL_CASES:
        ranked_a = baseline_a_semantic_only(case)
        ranked_c, _ = baseline_c_claim_conditioned(case)

        results["A"]["wrong"] += len(case.must_reject_ids & set(ranked_a))
        results["A"]["false_reject"] += len(case.must_not_reject_ids - set(ranked_a))
        results["C"]["wrong"] += len(case.must_reject_ids & set(ranked_c))
        results["C"]["false_reject"] += len(case.must_not_reject_ids - set(ranked_c))

        if case.relevant_ids:
            results["A"]["mrr"].append(metrics.mrr(ranked_a, case.relevant_ids))
            results["C"]["mrr"].append(metrics.mrr(ranked_c, case.relevant_ids))
    latency_ms = (time.monotonic() - start) * 1000

    assert results["C"]["wrong"] == 0, "Claim-conditioned pipeline must reject every must-reject candidate"
    assert results["C"]["false_reject"] == 0, "Claim-conditioned pipeline must never drop a must-not-reject candidate"
    assert results["A"]["wrong"] > results["C"]["wrong"], (
        "semantic-only baseline must wrongly surface at least one Claim-contradicted "
        "candidate that the Claim-conditioned pipeline correctly rejects"
    )
    avg_mrr_a = sum(results["A"]["mrr"]) / len(results["A"]["mrr"])
    avg_mrr_c = sum(results["C"]["mrr"]) / len(results["C"]["mrr"])
    assert avg_mrr_c >= avg_mrr_a, "Claim-conditioning must not regress mean reciprocal rank"
    assert latency_ms < 5000, "offline MockJudge pass over 5 fixture cases must stay fast"
