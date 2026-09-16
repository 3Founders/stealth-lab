"""
Pure-logic tests for app.execution.cost_math -- the shared expected-
attempts / expected-value arithmetic used by both goal_cost.py (disclosed
estimation) and implementation_selection.py (cost-informed ranking).
No DB, no I/O.
"""
from __future__ import annotations

from app.execution.cost_math import expected_attempts, expected_value


def test_expected_attempts_none_when_success_rate_none():
    assert expected_attempts(None) is None


def test_expected_attempts_none_when_success_rate_zero_not_infinite():
    assert expected_attempts(0.0) is None


def test_expected_attempts_real_geometric_expectation():
    assert expected_attempts(0.5) == 2.0
    assert expected_attempts(1.0) == 1.0


def test_expected_value_none_when_mean_none():
    assert expected_value(None, 2.0) is None


def test_expected_value_none_when_attempts_none():
    assert expected_value(3.0, None) is None


def test_expected_value_real_product():
    assert expected_value(2.0, 3.0) == 6.0
