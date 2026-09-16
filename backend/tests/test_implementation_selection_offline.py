"""
DB-free coverage for implementation_selection.py's pure deterministic
pieces: evaluate_requirements' HARD_FALSE/SOFT/SATISFIABLE/UNKNOWN
classification and is_eligible's short-circuit rule. Mirrors the
established pattern (test_applicability_hard_constraints_offline.py) of
proving cascade decision logic without touching Postgres.

Also covers _score's new cost component (Prompt 2 Sec 11) and a
rank_candidates()-level integration test via a fake pool.
"""
import asyncio

import app.execution.implementation_selection as impl_sel
from app.execution.execution_telemetry import ImplementationExecutionStats
from app.execution.implementation_selection import (
    DEFAULT_WEIGHTS,
    evaluate_requirements,
    is_eligible,
    rank_candidates,
)


def _run(coro):
    return asyncio.run(coro)


def _impl(**overrides):
    row = {
        "id": "00000000-0000-4000-8000-000000000001",
        "status": "active",
        "execution_location": "stealth_hosted",
        "scope_type": None,
        "auth_requirements": {},
        "resource_requirements": {},
        "verification_status": "unverified",
    }
    row.update(overrides)
    return row


def test_inactive_status_is_hard_false():
    checks = evaluate_requirements(_impl(status="deprecated"), {})
    assert not is_eligible(checks)
    lifecycle = next(c for c in checks if c.name == "lifecycle")
    assert lifecycle.state == "HARD_FALSE"


def test_active_status_alone_is_eligible():
    checks = evaluate_requirements(_impl(), {})
    assert is_eligible(checks)


def test_execution_location_outside_allowed_set_is_hard_false():
    checks = evaluate_requirements(
        _impl(execution_location="third_party_hosted"),
        {"allowed_execution_locations": ["stealth_hosted", "user_hosted"]},
    )
    assert not is_eligible(checks)
    loc = next(c for c in checks if c.name == "execution_location")
    assert loc.state == "HARD_FALSE"


def test_missing_execution_location_declaration_is_unknown_not_false():
    checks = evaluate_requirements(
        _impl(execution_location=None),
        {"allowed_execution_locations": ["stealth_hosted"]},
    )
    assert is_eligible(checks)  # UNKNOWN never disqualifies
    loc = next(c for c in checks if c.name == "execution_location")
    assert loc.state == "UNKNOWN"


def test_privacy_policy_forbids_third_party_hosted():
    checks = evaluate_requirements(
        _impl(execution_location="third_party_hosted"),
        {"privacy_policy": "no_third_party"},
    )
    assert not is_eligible(checks)
    privacy = next(c for c in checks if c.name == "privacy")
    assert privacy.state == "HARD_FALSE"


def test_scope_mismatch_is_hard_false():
    checks = evaluate_requirements(
        _impl(scope_type="repository"),
        {"required_scope_type": "workspace"},
    )
    assert not is_eligible(checks)


def test_unscoped_implementation_never_conflicts_with_required_scope():
    checks = evaluate_requirements(_impl(scope_type=None), {"required_scope_type": "workspace"})
    assert is_eligible(checks)


def test_missing_credential_declaration_is_unknown():
    checks = evaluate_requirements(
        _impl(auth_requirements={"credentials": ["github_token"]}),
        {},  # caller never declared available_credentials at all
    )
    assert is_eligible(checks)
    auth = next(c for c in checks if c.name == "auth")
    assert auth.state == "UNKNOWN"


def test_declared_missing_credential_is_hard_false():
    checks = evaluate_requirements(
        _impl(auth_requirements={"credentials": ["github_token"]}),
        {"available_credentials": []},
    )
    assert not is_eligible(checks)
    auth = next(c for c in checks if c.name == "auth")
    assert auth.state == "HARD_FALSE"


def test_declared_available_credential_is_satisfiable():
    checks = evaluate_requirements(
        _impl(auth_requirements={"credentials": ["github_token"]}),
        {"available_credentials": ["github_token"]},
    )
    assert is_eligible(checks)
    auth = next(c for c in checks if c.name == "auth")
    assert auth.state == "SATISFIABLE"


def test_resource_mismatch_is_hard_false():
    checks = evaluate_requirements(
        _impl(resource_requirements={"gpu": True}),
        {"available_resources": {"gpu": False}},
    )
    assert not is_eligible(checks)


def test_unresolved_resource_requirement_is_unknown():
    checks = evaluate_requirements(
        _impl(resource_requirements={"gpu": True}),
        {"available_resources": {}},
    )
    assert is_eligible(checks)
    resources = next(c for c in checks if c.name == "resources")
    assert resources.state == "UNKNOWN"


def test_no_declared_available_resources_is_unknown_not_false():
    checks = evaluate_requirements(
        _impl(resource_requirements={"gpu": True}),
        {},
    )
    assert is_eligible(checks)
    resources = next(c for c in checks if c.name == "resources")
    assert resources.state == "UNKNOWN"


# ---------------------------------------------------------------------
# _score's cost component (Prompt 2 Sec 11)
# ---------------------------------------------------------------------


def _stats(sample_count, success_count, mean_wall_seconds=None):
    return ImplementationExecutionStats(
        implementation_id="I-1", sample_count=sample_count, success_count=success_count,
        success_rate=(success_count / sample_count) if sample_count else None,
        mean_wall_seconds=mean_wall_seconds,
    )


def test_cost_component_omitted_below_sample_threshold():
    stats = _stats(sample_count=4, success_count=4, mean_wall_seconds=10.0)
    components = impl_sel._score(_impl(), {}, None, DEFAULT_WEIGHTS, stats)
    assert "cost" not in [c.name for c in components]


def test_cost_component_omitted_when_no_stats_at_all():
    components = impl_sel._score(_impl(), {}, None, DEFAULT_WEIGHTS, None)
    assert "cost" not in [c.name for c in components]


def test_cost_component_present_and_decreasing_in_expected_wall_seconds():
    cheap = _stats(sample_count=5, success_count=5, mean_wall_seconds=10.0)
    expensive = _stats(sample_count=5, success_count=5, mean_wall_seconds=200.0)
    cheap_score = next(c for c in impl_sel._score(_impl(), {}, None, DEFAULT_WEIGHTS, cheap) if c.name == "cost")
    expensive_score = next(c for c in impl_sel._score(_impl(), {}, None, DEFAULT_WEIGHTS, expensive) if c.name == "cost")
    assert 0 < expensive_score.value < cheap_score.value <= 1


def test_cost_component_at_half_life_is_exactly_half():
    stats = _stats(sample_count=5, success_count=5, mean_wall_seconds=impl_sel._COST_HALF_LIFE_SECONDS)
    cost = next(c for c in impl_sel._score(_impl(), {}, None, DEFAULT_WEIGHTS, stats) if c.name == "cost")
    assert cost.value == 0.5


def test_cost_component_omitted_when_success_rate_zero_expected_attempts_undefined():
    # success_rate=0 -> expected_attempts is None (never infinite) ->
    # expected_wall_seconds is None -> cost stays neutral, not penalized
    # to the max for "never succeeds".
    stats = _stats(sample_count=5, success_count=0, mean_wall_seconds=1.0)
    components = impl_sel._score(_impl(), {}, None, DEFAULT_WEIGHTS, stats)
    assert "cost" not in [c.name for c in components]


# ---------------------------------------------------------------------
# rank_candidates() integration -- real batched cost stats wired in
# ---------------------------------------------------------------------


class _FakeRankingPool:
    def __init__(self, cost_rows):
        self._cost_rows = cost_rows

    async def fetch(self, sql, *params):
        n = " ".join(sql.split())
        if "FROM evidence" in n:
            return []
        if "FROM implementation_execution_telemetry" in n and "GROUP BY" in n:
            return self._cost_rows
        raise AssertionError(f"unexpected fetch: {n[:80]}")


def test_lower_cost_candidate_ranks_higher_all_else_equal():
    cheap = _impl(id="I-cheap")
    expensive = _impl(id="I-expensive")
    pool = _FakeRankingPool([
        {
            "implementation_id": "I-cheap", "sample_count": 5, "success_count": 5,
            "mean_wall_seconds": 5.0, "mean_prompt_tokens": None,
            "mean_completion_tokens": None, "mean_llm_calls": None,
        },
        {
            "implementation_id": "I-expensive", "sample_count": 5, "success_count": 5,
            "mean_wall_seconds": 500.0, "mean_prompt_tokens": None,
            "mean_completion_tokens": None, "mean_llm_calls": None,
        },
    ])
    result = _run(rank_candidates(pool, [expensive, cheap], goal="do-a-thing"))
    assert result.chosen["id"] == "I-cheap"
