"""Gold-labeled applicability benchmark (spec section 12).

Runs the REAL app.services.applicability.check_hard_constraints cascade --
no re-implementation of cascade logic -- against a labeled matrix covering
applicable / non_applicable / partial_match / unknown / stale / superseded /
conflicting. The FakePool here is the same shape as
backend/tests/test_applicability_hard_constraints_offline.py's FakePool and
ClaimAwareFakePool combined (unified because several cases need both the
subject-based and claim-id-based branches distinguishable within one gold
set), so this never re-derives project_state()'s own semantics -- it only
supplies canned rows.

Where check_hard_constraints' own boolean output can't distinguish two gold
categories (both "no claim exists" and "a claim exists but contradicts"
produce the identical `precondition:...` failure -- CWA fail-closed, by
design, see applicability.py's module docstring), the finer gold label is
assigned from the fixture's own claims data, not from the code's return
value. classify_outcome() documents exactly which branch does which.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.applicability import check_hard_constraints
from tests.evaluation.harness import metrics
from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set
from tests.evaluation.harness.results import write_jsonl

GOLD_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gold_applicability" / "cases.json"

REJECT_LABELS = {"non_applicable", "partial_match", "unknown", "stale", "superseded", "conflicting"}


class GoldPool:
    """Answers project_state()'s subject-based claim fetch and
    _claim_matches_precondition's claim-id-based fetch differently, exactly
    like the existing offline suite's ClaimAwareFakePool -- distinguished by
    SQL shape, not by which test constructed it."""

    def __init__(self, *, claims_by_subject=(), claim_rows_by_id=None):
        self._claims_by_subject = list(claims_by_subject)
        self._claim_rows_by_id = dict(claim_rows_by_id or {})
        self.fetch_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        if "FROM knowledge_nodes" in sql and "id = $1::uuid" in sql:
            row = self._claim_rows_by_id.get(params[0])
            return [row] if row is not None else []
        return self._claims_by_subject

    async def fetchval(self, sql, *params):
        return 0


def _build_procedure(overrides: dict) -> dict:
    row = {
        "id": "00000000-0000-4000-8000-000000000001",
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
    overrides = dict(overrides)
    if "t_invalid_days_from_now" in overrides:
        days = overrides.pop("t_invalid_days_from_now")
        overrides["t_invalid"] = datetime.now(timezone.utc) + timedelta(days=days)
    row.update(overrides)
    return row


def classify_outcome(case: dict, result) -> str:
    """Maps the real cascade's (applicable, failed_constraints) output, plus
    the case's own claims fixture (for the precondition branch only), to one
    of the 7 gold labels."""
    if result.applicable:
        return "applicable"
    reason = result.failed_constraints[0] if result.failed_constraints else ""
    if reason == "staleness":
        return "stale"
    if reason == "temporal_validity":
        return "superseded"
    if reason in ("scope",):
        # Distinguish zero-overlap / missing-key (non_applicable) from a
        # multi-key scope where at least one key was actually satisfied
        # (partial_match) using the case's own fixture, since
        # check_hard_constraints only reports the first key it evaluated,
        # not "how many of N keys matched".
        procedure_scope = case["procedure"].get("scope", {})
        current_scope = case.get("current_scope", {})
        satisfied_keys = 0
        for key, allowed in procedure_scope.items():
            allowed_set = set(allowed) if isinstance(allowed, list) else {allowed}
            current = current_scope.get(key)
            if not current:
                continue
            current_set = set(current) if isinstance(current, list) else {current}
            if allowed_set & current_set:
                satisfied_keys += 1
        if len(procedure_scope) > 1 and 0 < satisfied_keys < len(procedure_scope):
            return "partial_match"
        return "non_applicable"
    if reason in ("exclusions", "availability", "verification_state", "approval_status"):
        return "non_applicable"
    if reason.startswith("precondition:"):
        claims = case.get("claims_by_subject", [])
        claim_rows = case.get("claim_rows_by_id", {})
        if case["procedure"]["preconditions"][0].get("claim_id"):
            # claim_id path: "conflicting" iff a same-subject claim exists
            # elsewhere (the narrowing case), else "unknown".
            return "conflicting" if claims else "unknown"
        return "conflicting" if claims else "unknown"
    if reason.startswith("invariant:"):
        return "non_applicable"
    return "non_applicable"


def run_case(case: dict) -> dict:
    procedure = _build_procedure(case["procedure"])
    pool = GoldPool(
        claims_by_subject=case.get("claims_by_subject", ()),
        claim_rows_by_id=case.get("claim_rows_by_id"),
    )
    result = asyncio.run(
        check_hard_constraints(
            pool,
            procedure,
            current_scope=case.get("current_scope", {}),
            require_verified=True,
            invariant_bindings=case.get("invariant_bindings"),
        )
    )
    predicted = classify_outcome(case, result)
    success = predicted == case["expected"]
    return {
        "success": success,
        "failure_reason": None if success else f"expected {case['expected']!r}, got {predicted!r} ({result.failed_constraints})",
        "metrics": {"predicted": predicted, "gold": case["expected"]},
    }


def _load_cases():
    return load_gold_set(GOLD_PATH)


def test_gold_applicability_matrix():
    cases = _load_cases()
    results = run_gold_set("gold_applicability", cases, run_case)

    failures = [r for r in results if not r.success]
    assert not failures, "\n".join(f"{r.scenario_id}: {r.failure_reason}" for r in failures)

    predicted = [r.metrics["predicted"] for r in results]
    gold = [r.metrics["gold"] for r in results]

    precision, recall = metrics.precision_recall(predicted, gold, positive_label="applicable")
    assert precision == 1.0
    assert recall == 1.0

    false_accept = metrics.false_accept_rate(
        predicted, gold, accept_label="applicable", reject_labels=REJECT_LABELS
    )
    assert false_accept == 0.0, (
        f"SAFETY: false-accept rate is {false_accept}, expected 0.0 -- this means "
        "check_hard_constraints automatically selected a procedure that should "
        "have been rejected (spec section 12's named critical safety metric)"
    )

    false_reject = metrics.false_reject_rate(predicted, gold, accept_label="applicable")
    assert false_reject == 0.0

    unknown_gold = [g for g in gold if g == "unknown"]
    if unknown_gold:
        abstention = metrics.abstention_accuracy(predicted, gold, unknown_label="unknown")
        assert abstention == 1.0, (
            f"SAFETY: abstention accuracy is {abstention}, expected 1.0 -- an "
            "'unknown' gold case (no claim recorded either way) was not "
            "correctly rejected from automatic selection"
        )

    counts = metrics.confusion_counts(predicted, gold)
    assert all("->" in k for k in counts)  # sanity: confusion_counts ran at all


def test_gold_applicability_exports_baseline_results(tmp_path):
    """Confirms the harness plumbing (run_gold_set + write_jsonl) produces
    one row per gold case with the schema evaluation-results/v1-baseline
    depends on -- exported to a tmp path here; the real v1-baseline export
    happens once in Phase 8, not on every test run."""
    cases = _load_cases()
    out = tmp_path / "applicability_results.jsonl"
    results = run_gold_set("gold_applicability", cases, run_case, results_path=out)
    assert out.exists()
    assert len(results) == len(cases)
    write_jsonl(results, out, mode="w")  # idempotent re-export, same schema
