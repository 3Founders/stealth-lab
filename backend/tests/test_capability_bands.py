"""
Proving tests for Band 1.9b capability computation
(procedure_extraction/capability.py) -- DB-free by construction: the
module is pure computation over outcome streams.

Traceability (ROADMAP Appendix C):
  #5  Every capability has defined task + evaluation criterion
      -> CapabilityScope rejects blank/missing task/state/environment/
         input/evaluation_criterion fields.
  #10 Failure can reduce capability
      -> injecting a failure into an outcome stream drops the computed
         level (the M1 traceability gate); later successes recover it,
         proving demotion is bidirectional, not a ratchet.
  #12 Capability is evidence-based, not model-brand-based
      -> identical streams differing ONLY in source_metadata.model_brand
         produce step-for-step identical trajectories; flipping an
         actual outcome DOES diverge them (non-vacuity control).

Also proven here, per the board item's own text:
  - routing tiers 0.90/0.70 exist ONLY as named config: monkeypatching
    the module constants changes routing behavior (an inlined literal
    would fail this test).
  - routing consumes P, never the level label (§16 verbatim).
  - D1 band boundaries hold exactly at 0.50/0.70/0.85/0.95 on the pure
    band_for_p ladder, and each level's additional gate actually caps.
"""
import math

import pytest

from app.services.procedure_extraction.capability import (
    LEVEL_2_P_THRESHOLD,
    LEVEL_3_P_THRESHOLD,
    LEVEL_4_P_THRESHOLD,
    LEVEL_5_P_THRESHOLD,
    MIN_ENVIRONMENTS_FOR_GENERALIZED,
    MIN_INDEPENDENT_GROUPS_FOR_REPRODUCED,
    ROUTE_AUTO_THRESHOLD,
    ROUTE_OFFER_THRESHOLD,
    OutcomeRecord,
    RoutingDecision,
    compute_capability,
    band_for_p,
    capability_trajectory,
    route_for_p,
    wilson_interval,
)
from app.services.procedure_extraction.capability import CapabilityScope


def _scope() -> CapabilityScope:
    return CapabilityScope(
        task="install dependency from lockfile",
        state_signature="repo@abc123+clean-tree",
        environment="ubuntu-24.04/py3.12",
        input_signature="requirements.txt[sha256:d0...]",
        evaluation_criterion="pip install -r requirements.txt exits 0 and imports resolve",
    )


def _ok(env="env-a", group=None, **meta):
    return OutcomeRecord(success=True, environment=env,
                         independence_group=group, source_metadata=meta or {})


def _fail(env="env-a", group=None, **meta):
    return OutcomeRecord(success=False, environment=env,
                         independence_group=group, source_metadata=meta or {})


def _decision_fields(rec):
    """Every field brand/metadata is forbidden to influence (#12)."""
    return (
        rec.p_estimate, rec.p_lower, rec.p_upper, rec.evidence_count,
        rec.success_count, rec.independent_groups, tuple(rec.environments_held),
        rec.level, rec.routing,
    )


# ---------------------------------------------------------------------------
# Appendix C #5 -- defined task + evaluation criterion (non-null context)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blank_field", [
    "task", "state_signature", "environment", "input_signature",
    "evaluation_criterion",
])
def test_scope_requires_non_null_context_fields(blank_field):
    # A capability conditioned on nothing is meaningless, not conservative:
    # every one of the five context fields must be present AND non-blank.
    kwargs = {
        "task": "t", "state_signature": "s", "environment": "e",
        "input_signature": "i", "evaluation_criterion": "c",
    }
    kwargs[blank_field] = ""
    with pytest.raises(ValueError):
        CapabilityScope(**kwargs)
    kwargs[blank_field] = "   "
    with pytest.raises(ValueError):
        CapabilityScope(**kwargs)
    del kwargs[blank_field]
    with pytest.raises(ValueError):
        CapabilityScope(**kwargs)


# ---------------------------------------------------------------------------
# Pure ladders -- exact D1 boundaries, no statistics involved
# ---------------------------------------------------------------------------

def test_band_ladder_hits_exact_d1_boundaries():
    assert band_for_p(0.49) == 1
    assert band_for_p(LEVEL_2_P_THRESHOLD) == 2            # 0.50 inclusive
    assert band_for_p(LEVEL_3_P_THRESHOLD - 1e-9) == 2
    assert band_for_p(LEVEL_3_P_THRESHOLD) == 3            # 0.70 inclusive
    assert band_for_p(LEVEL_4_P_THRESHOLD - 1e-9) == 3
    assert band_for_p(LEVEL_4_P_THRESHOLD) == 4            # 0.85 inclusive
    assert band_for_p(LEVEL_5_P_THRESHOLD - 1e-9) == 4
    assert band_for_p(LEVEL_5_P_THRESHOLD) == 5            # 0.95 inclusive
    assert band_for_p(1.0) == 5


def test_route_ladder_hits_exact_ratified_tiers_on_p():
    assert route_for_p(ROUTE_AUTO_THRESHOLD) is RoutingDecision.AUTO_ROUTE
    assert route_for_p(ROUTE_AUTO_THRESHOLD - 1e-9) is RoutingDecision.OFFER_AS_CANDIDATE
    assert route_for_p(ROUTE_OFFER_THRESHOLD) is RoutingDecision.OFFER_AS_CANDIDATE
    assert route_for_p(ROUTE_OFFER_THRESHOLD - 1e-9) is RoutingDecision.REFUSE_REUSE
    assert route_for_p(0.0) is RoutingDecision.REFUSE_REUSE


def test_routing_tiers_are_named_config_not_literals(monkeypatch):
    # If 0.90/0.70 were inlined at the decision site, retuning the named
    # constant could not move behavior. It can (and therefore must live
    # in exactly one place).
    monkeypatch.setattr(
        "app.services.procedure_extraction.capability.ROUTE_AUTO_THRESHOLD", 0.60)
    monkeypatch.setattr(
        "app.services.procedure_extraction.capability.ROUTE_OFFER_THRESHOLD", 0.40)
    assert route_for_p(0.61) is RoutingDecision.AUTO_ROUTE
    assert route_for_p(0.59) is RoutingDecision.OFFER_AS_CANDIDATE
    assert route_for_p(0.39) is RoutingDecision.REFUSE_REUSE


def test_band_thresholds_are_named_config_too(monkeypatch):
    monkeypatch.setattr(
        "app.services.procedure_extraction.capability.LEVEL_2_P_THRESHOLD", 0.30)
    assert band_for_p(0.31) == 2
    assert band_for_p(0.29) == 1


def test_routing_never_reads_the_level_label():
    # §16 verbatim: "All routing thresholds apply to P itself; the level
    # label is presentation, never a routing input." Construct the case
    # where label and P disagree: P high enough to auto-route but the env
    # gate fails, so the PRESENTATION label stays low.
    outcomes = [_ok(env="env-a", group=f"g{i}") for i in range(50)]
    rec = compute_capability(outcomes, _scope(), verification_plan_satisfied=True)

    p_lower, _ = wilson_interval(rec.success_count, rec.evidence_count)
    assert p_lower >= ROUTE_AUTO_THRESHOLD          # P says go
    assert len(rec.environments_held) < MIN_ENVIRONMENTS_FOR_GENERALIZED
    assert rec.level <= 3                           # label says not generalized
    assert rec.routing is RoutingDecision.AUTO_ROUTE  # decision read P


# ---------------------------------------------------------------------------
# Wilson estimator properties + hand-computed spot checks
# ---------------------------------------------------------------------------

def test_wilson_known_values():
    lower, upper = wilson_interval(1, 1)
    assert math.isclose(lower, 0.2065, abs_tol=1e-3)
    assert math.isclose(upper, 1.0, abs_tol=1e-9)

    lower, upper = wilson_interval(5, 5)
    assert math.isclose(lower, 0.5656, abs_tol=1e-3)   # 5 lucky runs != trusted

    assert wilson_interval(0, 0) == (0.0, 1.0)          # maximum ignorance


def test_wilson_bounds_tighten_with_evidence_volume():
    # §16: "P intervals whose bounds tighten as evidence volume grows".
    widths = []
    for n in (10, 100, 1000):
        lo, hi = wilson_interval(int(n * 0.9), n)
        widths.append(hi - lo)
    assert widths[0] > widths[1] > widths[2]


def test_wilson_lower_bound_moves_both_directions():
    lo_clean, _ = wilson_interval(10, 10)
    lo_after_failure, _ = wilson_interval(10, 11)
    lo_recovered, _ = wilson_interval(20, 21)
    assert lo_after_failure < lo_clean                  # failure demotes P
    assert lo_recovered > lo_after_failure              # success re-promotes


# ---------------------------------------------------------------------------
# Full-record behavior: gates cap levels; demotion on failure (#10)
# ---------------------------------------------------------------------------

def test_no_evidence_is_level_zero_and_refused():
    rec = compute_capability([], _scope())
    assert rec.level == 0
    assert rec.level_label == "unknown"
    assert rec.routing is RoutingDecision.REFUSE_REUSE
    assert rec.p_estimate == 0.0


def test_all_failures_stay_unknown_and_refused():
    # The ladder has no "known bad" rung; §16's L1 interval is "> 0".
    rec = compute_capability([_fail()] * 7, _scope())
    assert rec.level == 0
    assert rec.routing is RoutingDecision.REFUSE_REUSE


def test_single_success_is_observed_only():
    rec = compute_capability([_ok(group="g1")], _scope())
    assert rec.level == 1
    assert rec.level_label == "observed"
    assert rec.routing is RoutingDecision.REFUSE_REUSE  # 1/1 Wilson LB ≈ 0.207


def test_level_two_requires_distinct_independence_groups():
    many_same_group = [_ok(group="same-replay") for _ in range(40)]
    rec = compute_capability(many_same_group, _scope())
    p_lower, _ = wilson_interval(40, 40)
    assert p_lower >= LEVEL_2_P_THRESHOLD               # P alone would band 2+
    assert rec.independent_groups == 1
    assert rec.level == 1                               # gate caps back to observed

    ungrouped = [_ok() for _ in range(40)]
    assert compute_capability(ungrouped, _scope()).level == 1  # absence != independence

    split = [_ok(group="a") for _ in range(20)] + [_ok(group="b") for _ in range(20)]
    assert compute_capability(split, _scope()).level >= 2


def test_level_three_requires_satisfied_verification_plan():
    stream = [_ok(group=f"g{i}") for i in range(200)]   # P well above 0.70
    without = compute_capability(stream, _scope())
    with_plan = compute_capability(
        stream, _scope(), verification_plan_satisfied=True)
    assert without.level < 3
    assert with_plan.level == 3


def test_level_four_requires_two_environments_with_success():
    stream = ([_ok("env-a", f"a{i}") for i in range(150)]
              + [_fail("env-b", f"b{i}") for i in range(3)])
    one_env = compute_capability(stream, _scope(), verification_plan_satisfied=True)
    p_lower, _ = wilson_interval(one_env.success_count, one_env.evidence_count)
    assert p_lower >= LEVEL_4_P_THRESHOLD               # P alone bands 4
    assert one_env.environments_held == ["env-a"]       # failures hold nowhere
    assert one_env.level == 3                           # capped

    stream.append(_ok("env-b", "b-success"))
    both = compute_capability(stream, _scope(), verification_plan_satisfied=True)
    assert both.environments_held == ["env-a", "env-b"]
    assert both.level == 4


def test_level_five_requires_completed_review_even_with_perfect_stats():
    def big_stream():
        return ([_ok("env-a", f"a{i}") for i in range(250)]
                + [_ok("env-b", f"b{i}") for i in range(250)])

    stats_only = compute_capability(
        big_stream(), _scope(),
        verification_plan_satisfied=True, completed_review=False)
    p_lower, _ = wilson_interval(stats_only.success_count, stats_only.evidence_count)
    assert p_lower >= LEVEL_5_P_THRESHOLD               # perfect statistics
    assert stats_only.level == 4                        # review gate holds it down

    reviewed = compute_capability(
        big_stream(), _scope(),
        verification_plan_satisfied=True, completed_review=True)
    assert reviewed.level == 5
    assert reviewed.level_label == "trusted"


def test_injecting_failure_drops_level_then_success_recovers():
    # Appendix C #10 -- M1's traceability gate, on the trajectory view.
    # (Hand-computed: one failure off a 120/120 stream leaves the Wilson
    # lower bound at ~0.955, still inside the 0.95 trusted band -- the
    # gate must be crossed, so three are injected.)
    scope = _scope()
    healthy = [_ok(env=f"env-{i % 3}", group=f"g{i}")
              for i in range(120)]
    flags = dict(verification_plan_satisfied=True, completed_review=True)

    before = compute_capability(healthy, scope, **flags)
    assert before.level == 5

    with_failures = compute_capability(healthy + [_fail()] * 3, scope, **flags)
    assert with_failures.level < before.level           # THE traceability gate
    assert with_failures.p_estimate < before.p_estimate

    trajectory = capability_trajectory(healthy + [_fail(), _fail(), _fail()]
                                       + [_ok() for _ in range(80)],
                                       scope, **flags)
    levels = [r.level for r in trajectory]
    peak = levels.index(max(levels))
    trough = min(levels[peak:])
    assert trough < max(levels[: peak + 1])             # demoted after failures...
    assert levels[-1] > trough                          # ...and bidirectionally recovered


def test_one_failure_can_cross_a_routing_tier_downward():
    # Hand-computed: 50/50 Wilson LB ≈ 0.9285 (auto); 50/51 after one
    # failure ≈ 0.8970 (below the ratified auto tier) -- a single real
    # failure is enough to stop trusting at the margin, which is exactly
    # the fail-closed posture the lower-bound estimate buys.
    scope = _scope()
    stream = [_ok(group=f"g{i}") for i in range(50)]
    before = compute_capability(stream, scope)
    after = compute_capability(stream + [_fail()], scope)
    assert before.routing is RoutingDecision.AUTO_ROUTE
    assert after.p_estimate < ROUTE_AUTO_THRESHOLD
    assert after.routing is RoutingDecision.OFFER_AS_CANDIDATE


# ---------------------------------------------------------------------------
# Appendix C #12 -- evidence-based, never model-brand-based
# ---------------------------------------------------------------------------

def test_identical_streams_different_brands_identical_trajectories():
    scope = _scope()
    stream_a = [
        _ok("env-a", "g1", model_brand="claude-opus"),
        _ok("env-b", "g2", model_brand="claude-opus"),
        _fail("env-a", "g3", model_brand="claude-opus"),
        _ok("env-b", "g4", model_brand="claude-opus"),
    ]
    stream_b = [
        _ok("env-a", "g1", model_brand="gpt-frontend"),
        _ok("env-b", "g2", model_brand="gpt-frontend"),
        _fail("env-a", "g3", model_brand="gpt-frontend"),
        _ok("env-b", "g4", model_brand="gpt-frontend"),
    ]
    traj_a = capability_trajectory(stream_a, scope, verification_plan_satisfied=True)
    traj_b = capability_trajectory(stream_b, scope, verification_plan_satisfied=True)
    assert len(traj_a) == len(traj_b) > 0
    for ra, rb in zip(traj_a, traj_b):
        assert _decision_fields(ra) == _decision_fields(rb)

    final_a = compute_capability(stream_a, scope, verification_plan_satisfied=True)
    final_b = compute_capability(stream_b, scope, verification_plan_satisfied=True)
    assert _decision_fields(final_a) == _decision_fields(final_b)


def test_brand_test_is_non_vacuous_outcome_flips_diverge():
    # Guard for the test above: if metadata truly never mattered, the
    # assertion would pass even against a broken implementation that
    # ignored ALL inputs. Flipping a real outcome MUST diverge.
    scope = _scope()
    base = [
        _ok("env-a", "g1", model_brand="brand-x"),
        _ok("env-b", "g2", model_brand="brand-x"),
    ]
    flipped = [
        _ok("env-a", "g1", model_brand="brand-x"),
        _fail("env-b", "g2", model_brand="brand-x"),
    ]
    ra = compute_capability(base, scope)
    rb = compute_capability(flipped, scope)
    assert _decision_fields(ra) != _decision_fields(rb)


# ---------------------------------------------------------------------------
# Trajectory determinism / sanity
# ---------------------------------------------------------------------------

def test_trajectory_is_deterministic_and_cumulative():
    scope = _scope()
    stream = [_ok("env-a", "g1"), _ok("env-b", "g2"), _fail("env-a", None)]
    t1 = capability_trajectory(stream, scope)
    t2 = capability_trajectory(list(reversed(stream[::-1])), scope)  # same order, fresh objects
    assert [_decision_fields(r) for r in t1] == [_decision_fields(r) for r in t2]
    assert [r.evidence_count for r in t1] == [1, 2, 3]  # cumulative prefixes
