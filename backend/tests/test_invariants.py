"""
Unit tests for app/services/invariants.py -- DB-free, solver-backed.

The cases that matter most here are the NEGATIVE ones: an invariant that
cannot be decided must not be reported as violated (that asymmetry is
what keeps invariant-bearing procedures retrievable at all), and a
malformed or hostile expression must be refused rather than evaluated.
"""
import asyncio

import pytest

from app.services.invariants import (
    DEFAULT_SOLVER_TIMEOUT_MS,
    authoring_problems,
    check_invariants,
    check_invariants_async,
)

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


# --- ticket 1.8b: bounded solver runtime + off-event-loop entry point ---

def test_solver_timeout_is_a_plumbed_keyword_not_an_implicit_global():
    """The timeout must actually reach the Solver on both the sync and
    async paths (smoke: a generous budget changes no decision; the bound
    itself is z3's contract, not ours to re-prove)."""
    assert check_invariants(
        AMOUNT_LE_BALANCE, {"amount": 500, "checking_balance": 1200}, timeout_ms=250,
    ).satisfied

    async def _run():
        return await check_invariants_async(
            AMOUNT_LE_BALANCE, {"amount": 500, "checking_balance": 1200},
            timeout_ms=250,
        )

    assert asyncio.run(_run()).satisfied
    assert DEFAULT_SOLVER_TIMEOUT_MS >= 1000  # sane default, documented as configuration


def test_check_invariants_async_matches_the_sync_decision():
    """Same function semantics, worker-thread placement -- the retrieval
    cascade's entry point must not diverge from the unit-tested one."""
    async def _run():
        return (
            await check_invariants_async(AMOUNT_LE_BALANCE, {"amount": 2000, "checking_balance": 1200}),
            await check_invariants_async(AMOUNT_LE_BALANCE, {"amount": 500}),
        )

    violated_result, unbound_result = asyncio.run(_run())
    assert violated_result.violated == ["amount <= checking_balance"]
    assert unbound_result.undecidable and unbound_result.satisfied


# --- ticket 1.8b: authoring-time validation (validators.V6's engine) ---

def test_authoring_clean_for_satisfiable_and_non_numeric():
    assert authoring_problems(AMOUNT_LE_BALANCE) == []
    assert authoring_problems(None) == []
    assert authoring_problems([]) == []
    # Non-numeric kinds are the runtime path's business, not authoring's.
    assert authoring_problems([{"kind": "temporal", "expr": "ignored"}]) == []


def test_authoring_rejects_an_unsatisfiable_expression():
    problems = authoring_problems(
        [{"kind": "numeric", "expr": "amount <= balance and amount > balance"}],
    )
    assert len(problems) == 1
    assert "unsatisfiable" in problems[0]


def test_authoring_rejects_constant_contradictions_too():
    assert any("unsatisfiable" in p for p in authoring_problems(
        [{"kind": "numeric", "expr": "1 > 2"}],
    ))


def test_authoring_rejects_what_runtime_would_error_on():
    problems = authoring_problems([{"kind": "numeric", "expr": "amount <="}])
    assert problems and "could not be parsed" in problems[0]


def test_authoring_reports_each_defective_invariant_individually():
    problems = authoring_problems([
        {"kind": "numeric", "expr": "x < x"},
        {"kind": "numeric", "expr": "ok_var >= 0"},
        {"kind": "numeric", "expr": "broken +++"},
    ])
    assert len(problems) == 2
    assert any("x < x" in p for p in problems)
    assert any("broken +++" in p for p in problems)
