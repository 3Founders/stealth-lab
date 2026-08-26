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


# --- every whitelisted operator, not just the two exercised above ---
# (_ExprBuilder's Eq/NotEq comparisons, Sub/Mult/Div binops, and unary
# +/- branches had zero test coverage before this -- only Lt/LtE/Gt/GtE
# and Add were ever exercised, which is most of the actual security/
# correctness surface of the whitelist.)

def test_equality_and_inequality_comparisons():
    eq = [{"kind": "numeric", "expr": "amount == balance"}]
    assert check_invariants(eq, {"amount": 100, "balance": 100}).satisfied
    assert check_invariants(eq, {"amount": 100, "balance": 200}).violated == ["amount == balance"]

    neq = [{"kind": "numeric", "expr": "amount != balance"}]
    assert check_invariants(neq, {"amount": 100, "balance": 200}).satisfied
    assert check_invariants(neq, {"amount": 100, "balance": 100}).violated == ["amount != balance"]


def test_subtraction_multiplication_and_division_binops():
    sub = [{"kind": "numeric", "expr": "balance - amount >= 0"}]
    assert check_invariants(sub, {"balance": 100, "amount": 40}).satisfied
    assert not check_invariants(sub, {"balance": 100, "amount": 200}).satisfied

    mult = [{"kind": "numeric", "expr": "quantity * unit_price <= budget"}]
    assert check_invariants(mult, {"quantity": 3, "unit_price": 10, "budget": 100}).satisfied
    assert not check_invariants(mult, {"quantity": 3, "unit_price": 40, "budget": 100}).satisfied

    div = [{"kind": "numeric", "expr": "total / count <= max_average"}]
    assert check_invariants(div, {"total": 90, "count": 3, "max_average": 30}).satisfied
    assert not check_invariants(div, {"total": 90, "count": 3, "max_average": 20}).satisfied


def test_unary_plus_and_minus():
    minus = [{"kind": "numeric", "expr": "-balance <= 0"}]
    assert check_invariants(minus, {"balance": 5}).satisfied

    plus = [{"kind": "numeric", "expr": "+amount <= limit"}]
    assert check_invariants(plus, {"amount": 5, "limit": 10}).satisfied
    assert not check_invariants(plus, {"amount": 15, "limit": 10}).satisfied


def test_non_numeric_constant_is_refused():
    result = check_invariants(
        [{"kind": "numeric", "expr": "status == 'closed'"}], {"status": 1},
    )
    assert not result.satisfied
    assert any("only numeric constants" in e for e in result.errors)
    assert not result.violated


def test_chained_comparison_is_refused_not_silently_reinterpreted():
    """`a <= b <= c` is a real Python chain, not two comparisons -- the
    whitelist explicitly refuses it rather than guessing an intent."""
    result = check_invariants(
        [{"kind": "numeric", "expr": "0 <= amount <= balance"}], {"amount": 5, "balance": 10},
    )
    assert not result.satisfied
    assert any("single comparisons" in e for e in result.errors)


def test_disallowed_comparison_operator_is_refused():
    result = check_invariants(
        [{"kind": "numeric", "expr": "amount in balance"}], {"amount": 1, "balance": 2},
    )
    assert not result.satisfied
    assert any("is not allowed" in e for e in result.errors)


def test_disallowed_binop_operator_is_refused():
    result = check_invariants(
        [{"kind": "numeric", "expr": "amount % 3 <= 1"}], {"amount": 7},
    )
    assert not result.satisfied
    assert any("is not allowed" in e for e in result.errors)


def test_disallowed_unary_operator_is_refused():
    result = check_invariants(
        [{"kind": "numeric", "expr": "~amount <= 0"}], {"amount": 5},
    )
    assert not result.satisfied
    assert any("is not allowed" in e for e in result.errors)


def test_z3_unavailable_via_a_real_import_failure_not_just_a_monkeypatched_check(monkeypatch):
    """_z3_available()'s own try/except ImportError branch, exercised
    with a genuinely-failing import rather than the higher-level
    monkeypatch used below -- sys.modules[name] = None is the documented
    way to make `import z3` itself raise ImportError."""
    import sys
    monkeypatch.setitem(sys.modules, "z3", None)

    result = check_invariants(AMOUNT_LE_BALANCE, {"amount": 1, "checking_balance": 2})

    assert not result.satisfied
    assert any("z3-solver is not installed" in e for e in result.errors)


def test_solver_returned_unknown_is_undecidable_not_violated_or_satisfied(monkeypatch):
    """The genuine-timeout branch, forced deterministically: mock
    Solver.check() itself to return z3.unknown rather than relying on a
    real query being slow enough to hit the timeout, which would be
    flaky across machines for arithmetic this simple."""
    import z3
    monkeypatch.setattr(z3.Solver, "check", lambda self: z3.unknown)

    result = check_invariants(AMOUNT_LE_BALANCE, {"amount": 500, "checking_balance": 1200})

    assert not result.violated
    assert result.undecidable
    assert "solver returned unknown" in result.undecidable[0]


def test_authoring_reports_solver_unknown_for_manual_review(monkeypatch):
    import z3
    monkeypatch.setattr(z3.Solver, "check", lambda self: z3.unknown)

    problems = authoring_problems(AMOUNT_LE_BALANCE)

    assert len(problems) == 1
    assert "could not be decided" in problems[0]


# --- honest degradation: missing z3 dependency ---
# (_z3_available()'s own False branch, and both check_invariants' and
# authoring_problems' handling of it, were never exercised -- z3 is
# always installed in every environment this suite runs in, so the only
# way to prove the degradation path is to monkeypatch the availability
# check itself rather than actually uninstall the dependency.)

def test_missing_z3_is_reported_as_an_error_not_silently_satisfied(monkeypatch):
    import app.services.invariants as invariants_module
    monkeypatch.setattr(invariants_module, "_z3_available", lambda: False)

    result = invariants_module.check_invariants(AMOUNT_LE_BALANCE, {"amount": 1, "checking_balance": 2})

    assert not result.satisfied
    assert any("z3-solver is not installed" in e for e in result.errors)
    assert not result.violated


def test_missing_z3_makes_authoring_validation_report_one_problem(monkeypatch):
    import app.services.invariants as invariants_module
    monkeypatch.setattr(invariants_module, "_z3_available", lambda: False)

    problems = invariants_module.authoring_problems(AMOUNT_LE_BALANCE)

    assert len(problems) == 1
    assert "z3-solver is not installed" in problems[0]


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


def test_authoring_reports_a_missing_expr_key():
    problems = authoring_problems([{"kind": "numeric"}])
    assert problems and "no usable 'expr'" in problems[0]


def test_authoring_reports_each_defective_invariant_individually():
    problems = authoring_problems([
        {"kind": "numeric", "expr": "x < x"},
        {"kind": "numeric", "expr": "ok_var >= 0"},
        {"kind": "numeric", "expr": "broken +++"},
    ])
    assert len(problems) == 2
    assert any("x < x" in p for p in problems)
    assert any("broken +++" in p for p in problems)
