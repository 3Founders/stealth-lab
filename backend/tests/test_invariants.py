"""
Unit tests for app/services/invariants.py -- DB-free, solver-backed.

The cases that matter most here are the NEGATIVE ones: an invariant that
cannot be decided must not be reported as violated (that asymmetry is
what keeps invariant-bearing procedures retrievable at all), and a
malformed or hostile expression must be refused rather than evaluated.
"""
import pytest

from app.services.invariants import check_invariants

AMOUNT_LE_BALANCE = [{"kind": "numeric", "expr": "amount <= checking_balance"}]


def test_satisfied_when_relation_holds():
    result = check_invariants(AMOUNT_LE_BALANCE, {"amount": 500, "checking_balance": 1200})
    assert result.satisfied
    assert not result.violated
    assert not result.errors


def test_violated_when_relation_does_not_hold():
    result = check_invariants(AMOUNT_LE_BALANCE, {"amount": 2000, "checking_balance": 1200})
    assert not result.satisfied
    assert result.violated == ["amount <= checking_balance"]


def test_unbound_variable_is_undecidable_not_violated():
    """The retrieval-time normal case: nobody has stated a balance yet.
    Reporting this as violated would make every invariant-bearing
    procedure permanently unretrievable."""
    result = check_invariants(AMOUNT_LE_BALANCE, {"amount": 500})
    assert result.undecidable
    assert not result.violated
    assert result.satisfied  # not disqualifying


@pytest.mark.parametrize("invariants", [None, [], [{"kind": "temporal", "expr": "ignored"}]])
def test_nothing_to_check_is_trivially_satisfied(invariants):
    """Empty is the state of every procedure row that exists today, so
    this is the path the applicability cascade takes in practice. A
    non-numeric `kind` is ignored rather than erroring, so a future
    invariant kind can be added incrementally."""
    result = check_invariants(invariants, {})
    assert result.satisfied
    assert not result.has_problems


def test_malformed_expression_reports_error_and_does_not_raise():
    result = check_invariants([{"kind": "numeric", "expr": "amount <="}], {"amount": 1})
    assert not result.satisfied
    assert result.errors
    assert not result.violated


def test_missing_expr_reports_error():
    result = check_invariants([{"kind": "numeric"}], {})
    assert result.errors


def test_code_execution_attempt_is_refused_at_the_whitelist():
    """Procedures can be LLM-authored and are read back out of a
    database, so an expression string is untrusted input. It must never
    reach eval()."""
    result = check_invariants(
        [{"kind": "numeric", "expr": '__import__("os").system("echo pwned")'}], {},
    )
    assert not result.satisfied
    assert any("not allowed" in e for e in result.errors)
    assert not result.violated


def test_arithmetic_and_boolean_composition():
    inv = [{"kind": "numeric", "expr": "amount + fee <= balance"}]
    assert check_invariants(inv, {"amount": 90, "fee": 5, "balance": 100}).satisfied
    assert not check_invariants(inv, {"amount": 90, "fee": 20, "balance": 100}).satisfied

    compound = [{"kind": "numeric", "expr": "amount > 0 and amount <= balance"}]
    assert check_invariants(compound, {"amount": 50, "balance": 100}).satisfied
    assert not check_invariants(compound, {"amount": -5, "balance": 100}).satisfied
