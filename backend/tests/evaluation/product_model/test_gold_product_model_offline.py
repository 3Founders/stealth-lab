"""Gold-labeled offline coverage for the Problem/Benchmark/Solution/Evaluation
product layer (task spec sections 2-4), against the REAL
app.services.product_model service -- no DB, no mocks of the thing under
test.

Two independent things are proven here, both without a database:

1. evaluations_comparable() is a pure function -- comparability.json gold-
   matrix-drives it exactly like gold_applicability drives
   check_hard_constraints.
2. product_model's anti-fabrication / input-validation gates reject BEFORE
   ever opening a transaction: create_problem's blank-title check,
   associate_solution's solution_type whitelist check, and
   complete_evaluation's empty-execution_ids check (spec section 3's "no
   caller-fabricated completion") all raise synchronously before the first
   `async with tenant_transaction(...)` line in product_model.py -- so they
   are exercised here with pool=None, proving the reject is real code
   executing, not a test double standing in for it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.product_model import (
    associate_solution,
    complete_evaluation,
    create_problem,
    evaluations_comparable,
)
from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set

GOLD_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "gold_product_model" / "comparability_cases.json"
)


def run_case(case: dict) -> dict:
    ok, reason = evaluations_comparable(case["a"], case["b"])
    expected = case["expected"]
    if expected == "comparable":
        success = ok and reason is None
    else:
        success = (not ok) and reason is not None and expected in reason
    return {
        "success": success,
        "failure_reason": None if success else f"expected {expected!r}, got ok={ok} reason={reason!r}",
        "metrics": {"comparable": ok, "reason": reason},
    }


def test_gold_comparability_matrix():
    cases = load_gold_set(GOLD_PATH)
    results = run_gold_set("gold_product_model_comparability", cases, run_case)
    failures = [r for r in results if not r.success]
    assert not failures, "\n".join(f"{r.scenario_id}: {r.failure_reason}" for r in failures)


# ---------------------------------------------------------------------------
# Anti-fabrication / input-validation gates that reject before touching a
# pool -- real product_model.py code paths, genuinely offline-testable.
# ---------------------------------------------------------------------------


def test_create_problem_rejects_blank_title_without_touching_the_pool():
    with pytest.raises(ValueError, match="title"):
        asyncio.run(create_problem(pool=None, title="   "))


def test_associate_solution_rejects_unknown_solution_type_without_touching_the_pool():
    with pytest.raises(ValueError, match="solution_type"):
        asyncio.run(
            associate_solution(
                pool=None, problem_id="p1", solution_type="not_a_real_type", target_id="t1",
            )
        )


def test_complete_evaluation_rejects_empty_execution_ids_without_touching_the_pool():
    """Spec section 3: 'a completed Evaluation cannot exist without real
    underlying execution/evidence lineage' -- proven here as a synchronous,
    DB-free reject, matching product_model.py's own docstring ('an
    untrusted caller CANNOT fabricate a completed result')."""
    with pytest.raises(ValueError, match="no linked executions"):
        asyncio.run(complete_evaluation(pool=None, evaluation_id="e1", execution_ids=[]))
