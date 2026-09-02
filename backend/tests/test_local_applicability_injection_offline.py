"""
Proving test: app/local_agent/local_applicability.py precondition/invariant
evaluation treats untrusted procedure content as DATA, never as executable
Python/SQL/shell.

Threat model (directive item 1): a candidate procedure can enter the local
store from an UNTRUSTED source (imported from git history, a republished
procedure, a chat/agent-trace bootstrap import). Its `preconditions` and
`invariants` fields are attacker-controlled strings/dicts. This test proves
that a hostile predicate/subject/object -- or a hostile invariant `expr` --
can never do anything but fail equality comparison or fail the invariant
whitelist parse; it is structurally impossible for either path to reach
`eval`/`exec`/a shell, not merely "we didn't hit a bug this time".

DB-free by construction: local_applicability.py never touches app.db or
asyncpg (see its own module docstring), so this whole file needs no
DATABASE_URL and no live services.
"""
from __future__ import annotations

import os

from app.local_agent.local_applicability import (
    check_local_hard_constraints,
    _evaluate_precondition,
)
from app.services.environment_facts import EnvironmentFact

MARKER_PATH = None  # set per-test to a tmp_path-scoped marker file


def _base_procedure(**overrides) -> dict:
    procedure = {
        "id": "row-1",
        "t_invalid": None,
        "staleness": "fresh",
        "availability": "active",
        "verification_state": "verified",
        "scope": {},
        "exclusions": [],
        "preconditions": [],
        "invariants": [],
    }
    procedure.update(overrides)
    return procedure


def test_malicious_precondition_predicate_is_unknown_not_executed(tmp_path):
    """A precondition whose predicate/subject/object carry shell/python
    injection payloads must be rejected as `precondition:unknown` (the
    predicate is not in the closed probe vocabulary) -- never evaluated,
    never causing a side effect."""
    marker = tmp_path / "INJECTED"
    hostile_precondition = {
        "subject": "pandas",
        "predicate": "; rm -rf / #",
        "object": f"__import__('os').system('touch {marker}')",
    }
    procedure = _base_procedure(preconditions=[hostile_precondition])

    result = check_local_hard_constraints(
        procedure, current_scope={}, environment_facts=[],
    )

    assert result.applicable is False
    assert result.failed_constraints == [
        f"precondition:unknown:predicate={hostile_precondition['predicate']!r} is not in the local "
        "probe's closed vocabulary -- cannot be resolved locally, and an "
        "unresolvable precondition is never treated as satisfied"
    ]
    assert not marker.exists(), "hostile precondition object must never be executed"


def test_precondition_equality_match_never_interprets_object_as_code(tmp_path):
    """Even when the predicate IS in the closed vocabulary (so the
    precondition is actually resolved), the comparison is plain string
    equality against a locally-probed fact -- a hostile `object` value
    that looks like a shell/python payload is just a string that fails
    (or, if it happens to equal the probed string verbatim, passes) `==`,
    never something interpreted."""
    from app.services.environment_facts import PROBE_PREDICATE_VOCABULARY

    predicate = PROBE_PREDICATE_VOCABULARY[0]
    marker = tmp_path / "INJECTED2"
    hostile_object = f"$(touch {marker})"

    facts = [EnvironmentFact(predicate=predicate, object="3.11.0")]
    reason = _evaluate_precondition(
        {"subject": "python", "predicate": predicate, "object": hostile_object},
        {predicate: {"3.11.0"}},
    )
    assert reason is not None
    assert "precondition:violated" in reason
    assert not marker.exists(), "hostile precondition object must never be shell-interpreted"

    # And the literal payload survives unharmed in the reason string --
    # proof it was carried as data, not stripped/executed/re-parsed.
    # (repr() of the raw string is used, so on Windows a `\` becomes
    # `\\` in the reason text -- compare against repr() rather than the
    # raw string to be robust to that escaping.)
    assert repr(hostile_object) in reason


def test_invariant_engine_injection_refused_through_the_local_cascade(tmp_path):
    """Same whitelist-vs-eval guarantee app/services/invariants.py already
    proves in isolation (test_invariants.py::
    test_code_execution_attempt_is_refused_at_the_whitelist), asserted
    here through the ACTUAL caller path an untrusted local procedure
    would go through: check_local_hard_constraints -> check_invariants.
    A hostile `expr` must disqualify the procedure (never silently pass,
    never execute) and must never raise."""
    marker = tmp_path / "INJECTED3"
    hostile_invariant = [{
        "kind": "numeric",
        "expr": f"__import__('os').system('touch {marker}')",
    }]
    procedure = _base_procedure(invariants=hostile_invariant)

    result = check_local_hard_constraints(
        procedure, current_scope={}, environment_facts=[], invariant_bindings={},
    )

    assert result.applicable is False
    assert any(c.startswith("invariant:") for c in result.failed_constraints)
    assert not marker.exists(), "hostile invariant expr must never be executed"


def test_invariant_sql_shaped_expr_is_a_parse_refusal_not_a_query():
    """A precondition/invariant author could try a SQL-injection-shaped
    payload instead of a Python one, betting that `expr` reaches some
    string-built query somewhere downstream. It doesn't: the whole
    pipeline is ast.parse + a structural comparator, so this is just an
    unparseable expression -- reported in errors, never sent anywhere as
    SQL."""
    from app.services.invariants import check_invariants

    result = check_invariants(
        [{"kind": "numeric", "expr": "amount <= balance; DROP TABLE procedures; --"}],
        {"amount": 1, "balance": 2},
    )
    assert not result.satisfied
    assert result.errors
    assert not result.violated
