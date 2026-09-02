"""
Offline unit tests for the product-model service logic that needs no DB:
comparability (§18) and the leaderboard's derived states + ties +
conditional leaders (§17, §19). Runs with DATABASE_URL unset.
"""
from __future__ import annotations

import pytest

from app.services.product_model import (
    BEST_VERIFIED_FLOOR,
    MIN_RUNS_FOR_RANKING,
    _band,
    evaluations_comparable,
)


def _eval(benchmark_id="b1", verification="deterministic", env=None, status="completed"):
    return {
        "benchmark_id": benchmark_id,
        "status": status,
        "methodology": {"verification": verification},
        "environment": env or {"runtime": "linux", "python": "3.13"},
    }


# ---- evaluations_comparable (§18) ----
def test_same_benchmark_same_semantics_same_env_is_comparable():
    ok, reason = evaluations_comparable(_eval(), _eval())
    assert ok and reason is None


def test_different_benchmark_is_not_comparable():
    ok, reason = evaluations_comparable(_eval("b1"), _eval("b2"))
    assert not ok and "benchmark" in reason


def test_different_verification_semantics_is_not_comparable():
    ok, reason = evaluations_comparable(_eval(verification="deterministic"),
                                       _eval(verification="llm_judge"))
    assert not ok and "verification" in reason


def test_different_material_environment_is_not_comparable():
    ok, reason = evaluations_comparable(_eval(env={"gpu": "a100"}), _eval(env={"gpu": "none"}))
    assert not ok and "environment" in reason


def test_non_material_env_keys_are_ignored_for_comparability():
    a = _eval(env={"runtime": "linux", "operator": "alice", "run_id": "1"})
    b = _eval(env={"runtime": "linux", "operator": "bob", "run_id": "2"})
    ok, _ = evaluations_comparable(a, b)
    assert ok


def test_incomplete_evaluations_are_not_comparable():
    ok, reason = evaluations_comparable(_eval(status="running"), _eval())
    assert not ok and "completed" in reason


# ---- _band: derived states (§17, §19) ----
def test_insufficient_evidence_below_min_runs():
    assert _band(0.99, MIN_RUNS_FOR_RANKING - 1, verified=3) == "INSUFFICIENT_EVIDENCE"


def test_insufficient_evidence_when_no_verified_successes():
    assert _band(0.0, 50, verified=0) == "INSUFFICIENT_EVIDENCE"


def test_best_verified_needs_high_wilson_lower_and_enough_runs():
    assert _band(BEST_VERIFIED_FLOOR + 0.05, 40, verified=38) == "BEST_VERIFIED"


def test_high_performing_band():
    assert _band(0.55, 40, verified=25) == "HIGH_PERFORMING"


def test_promising_band_has_evidence_but_low_confidence():
    assert _band(0.30, 40, verified=14) == "PROMISING"


def test_small_n_perfect_rate_does_not_reach_best_verified():
    # n=2, 2/2 -> Wilson lower is far below the floor AND n < MIN_RUNS.
    from app.services.procedure_extraction.capability import wilson_interval
    lo, _ = wilson_interval(2, 2)
    assert _band(lo, 2, verified=2) == "INSUFFICIENT_EVIDENCE"
    assert lo < BEST_VERIFIED_FLOOR  # 100%, n=2 is not a strong signal (§17)
