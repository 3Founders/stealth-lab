"""
MCP hardening B1/B2, offline half: `classify_intent()`'s deterministic
policy table and `_parse_precondition_constraint()`'s reversal of
check_hard_constraints()'s own constraint-name formatting, plus
`RouteDecision`'s own validation. None of this touches a database --
see test_route_decision_e2e.py for the live-DB half (decide_route's
cascade integration, persist/get round trip).
"""
import pytest

from app.services.route_decision import (
    INTENTS,
    ROUTE_STATES,
    RouteDecision,
    _parse_precondition_constraint,
    classify_intent,
)


@pytest.mark.parametrize(
    "mode,expected",
    [("lookup_only", "assist"), ("plan_only", "plan"), ("full_run", "execute")],
)
def test_classify_intent_explicit_modes_bypass_text_classification(mode, expected):
    # An explicit mode always wins over phrasing -- a caller who already
    # said mode="full_run" has told us the intent regardless of wording.
    assert classify_intent("how should I even approach this?", mode) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("How should I structure this module?", "assist"),
        ("What's the best way to cache this?", "assist"),
        ("Is it better to use a queue here?", "assist"),
        ("Does this even work?", "assist"),  # trailing '?' fallback
        ("Give me a plan for migrating the auth layer", "plan"),
        ("Please break this down into steps", "plan"),
        ("Fix the null pointer bug in parser.py", "execute"),
        ("please implement rate limiting", "execute"),
        ("refactor the auth module", "execute"),
        ("something with no verb or question cue at all", "ambiguous"),
    ],
)
def test_classify_intent_auto_mode_policy_table(text, expected):
    assert classify_intent(text, "auto") == expected


def test_classify_intent_plan_pattern_wins_over_execute_verb():
    # "break down"/"break this down" is a _PLAN_PATTERNS phrase, checked
    # before the execute-verb regex -- must not be misread as "break"
    # the execute verb (which isn't even in _EXECUTE_VERBS, but the
    # broader point: plan patterns take priority in the cascade).
    assert classify_intent("break down this migration task", "auto") == "plan"


@pytest.mark.parametrize(
    "constraint,expected",
    [
        (
            "precondition:subject=project:1,predicate=language,object=python",
            {"subject": "project:1", "predicate": "language", "object": "python"},
        ),
        (
            "precondition:subject=project:1,predicate=version_gte,object=None",
            {"subject": "project:1", "predicate": "version_gte", "object": None},
        ),
    ],
)
def test_parse_precondition_constraint_roundtrips_check_hard_constraints_format(constraint, expected):
    assert _parse_precondition_constraint(constraint) == expected


@pytest.mark.parametrize(
    "constraint",
    [
        "temporal_validity", "staleness", "availability", "verification_state",
        "approval_status", "scope", "exclusions", "invariant:x > 3",
        "precondition:predicate=foo,object=bar",  # malformed: no subject
    ],
)
def test_parse_precondition_constraint_rejects_non_precondition_and_malformed(constraint):
    assert _parse_precondition_constraint(constraint) is None


def test_route_decision_rejects_invalid_route():
    with pytest.raises(ValueError):
        RouteDecision(route="not_a_real_route", reason="x", intent="assist",
                       task_description="t", mode="auto")


def test_route_decision_rejects_invalid_intent():
    with pytest.raises(ValueError):
        RouteDecision(route="assist", reason="x", intent="not_a_real_intent",
                       task_description="t", mode="auto")


@pytest.mark.parametrize("route", ROUTE_STATES)
def test_route_decision_accepts_every_declared_route_state(route):
    RouteDecision(route=route, reason="x", intent="assist", task_description="t", mode="auto")


@pytest.mark.parametrize("intent", INTENTS)
def test_route_decision_accepts_every_declared_intent(intent):
    RouteDecision(route="assist", reason="x", intent=intent, task_description="t", mode="auto")
