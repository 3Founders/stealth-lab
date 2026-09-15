"""
DB-free coverage for app.execution.goal_cost -- the real recursive
ExpectedCost aggregation (execu.md Sec 13). implementation_execution_stats
is monkeypatched per-implementation-id so these tests prove the
aggregation/confidence logic in isolation, not the DB read itself
(covered by test_execution_telemetry_offline.py).
"""
from __future__ import annotations

import asyncio

import app.execution.goal_cost as gc
from app.execution.execution_telemetry import ImplementationExecutionStats
from app.execution.goal_resolution import ResolvedGoalNode


def _run(coro):
    return asyncio.run(coro)


def _stats(implementation_id, *, sample_count, success_count, mean_wall_seconds=None,
           mean_prompt_tokens=None, mean_completion_tokens=None):
    success_rate = (success_count / sample_count) if sample_count else None
    return ImplementationExecutionStats(
        implementation_id=implementation_id, sample_count=sample_count, success_count=success_count,
        success_rate=success_rate, mean_wall_seconds=mean_wall_seconds,
        mean_prompt_tokens=mean_prompt_tokens, mean_completion_tokens=mean_completion_tokens,
    )


def _impl_node(goal_id, name, impl_id, depth=0):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=depth, chosen="implementation",
        implementation={"id": impl_id, "name": f"impl-{name}", "kind": "deterministic"},
    )


def _unresolved_node(goal_id, name, depth=0):
    return ResolvedGoalNode(goal_id=goal_id, goal_name=name, depth=depth, chosen="unresolved", unresolved_reason="x")


def _procedure_node(goal_id, name, children, depth=0):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=depth, chosen="procedure",
        procedure={"id": "P-1"}, children=children,
    )


# ---------------------------------------------------------------------
# estimate_implementation_cost
# ---------------------------------------------------------------------


def test_zero_samples_is_honest_none_confidence(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return _stats(implementation_id, sample_count=0, success_count=0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    cost = _run(gc.estimate_implementation_cost(None, "I-1"))
    assert cost.confidence == "none"
    assert cost.sample_count == 0
    assert cost.monetary_cost_usd is None


def test_low_confidence_below_empirical_threshold(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return _stats(implementation_id, sample_count=4, success_count=4, mean_wall_seconds=1.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    cost = _run(gc.estimate_implementation_cost(None, "I-1"))
    assert cost.confidence == "low"


def test_empirical_confidence_at_threshold(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return _stats(implementation_id, sample_count=5, success_count=5, mean_wall_seconds=1.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    cost = _run(gc.estimate_implementation_cost(None, "I-1"))
    assert cost.confidence == "empirical"


def test_expected_attempts_is_real_geometric_expectation(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return _stats(implementation_id, sample_count=10, success_count=5, mean_wall_seconds=2.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    cost = _run(gc.estimate_implementation_cost(None, "I-1"))
    assert cost.success_rate == 0.5
    assert cost.expected_attempts == 2.0  # 1 / 0.5
    assert cost.expected_wall_seconds == 4.0  # 2.0 mean * 2.0 expected attempts


def test_zero_success_rate_leaves_expected_attempts_none_not_infinite(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return _stats(implementation_id, sample_count=3, success_count=0, mean_wall_seconds=1.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    cost = _run(gc.estimate_implementation_cost(None, "I-1"))
    assert cost.success_rate == 0.0
    assert cost.expected_attempts is None
    assert cost.expected_wall_seconds is None  # cannot scale by an unknown multiplier


# ---------------------------------------------------------------------
# estimate_goal_cost -- leaves
# ---------------------------------------------------------------------


def test_unresolved_goal_has_no_cost():
    node = _unresolved_node("G-1", "impossible")
    cost = _run(gc.estimate_goal_cost(None, node))
    assert cost.confidence == "none"
    assert "unresolved" in cost.basis


def test_implementation_leaf_delegates_to_estimate_implementation_cost(monkeypatch):
    async def fake_stats(pool, implementation_id):
        assert implementation_id == "I-1"
        return _stats(implementation_id, sample_count=5, success_count=5, mean_wall_seconds=3.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    node = _impl_node("G-1", "do it", "I-1")
    cost = _run(gc.estimate_goal_cost(None, node))
    assert cost.confidence == "empirical"
    assert cost.expected_wall_seconds == 3.0


# ---------------------------------------------------------------------
# estimate_goal_cost -- procedure aggregation
# ---------------------------------------------------------------------


def test_procedure_sums_real_child_costs(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return {
            "I-1": _stats("I-1", sample_count=5, success_count=5, mean_wall_seconds=1.0),
            "I-2": _stats("I-2", sample_count=5, success_count=5, mean_wall_seconds=2.0),
        }[implementation_id]
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    tree = _procedure_node("G-parent", "p", children=[
        _impl_node("G-1", "step one", "I-1", depth=1),
        _impl_node("G-2", "step two", "I-2", depth=1),
    ])
    cost = _run(gc.estimate_goal_cost(None, tree))
    assert cost.confidence == "empirical"
    assert cost.expected_wall_seconds == 3.0  # 1.0 + 2.0
    assert cost.sample_count == 10


def test_procedure_with_one_unresolved_child_is_honestly_none():
    tree = _procedure_node("G-parent", "p", children=[
        _unresolved_node("G-1", "unmatched step", depth=1),
    ])
    cost = _run(gc.estimate_goal_cost(None, tree))
    assert cost.confidence == "none"
    assert "unmatched step" in cost.basis


def test_procedure_with_partial_data_is_none_not_a_misleading_partial_sum(monkeypatch):
    """If ANY child has zero real samples, the aggregate must not silently
    sum only the children that DO have data -- that would understate the
    real total cost, which is worse than an honest 'unknown'."""
    async def fake_stats(pool, implementation_id):
        if implementation_id == "I-1":
            return _stats("I-1", sample_count=5, success_count=5, mean_wall_seconds=1.0)
        return _stats(implementation_id, sample_count=0, success_count=0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    tree = _procedure_node("G-parent", "p", children=[
        _impl_node("G-1", "step one", "I-1", depth=1),
        _impl_node("G-2", "step two (never run)", "I-2", depth=1),
    ])
    cost = _run(gc.estimate_goal_cost(None, tree))
    assert cost.confidence == "none"
    assert cost.expected_wall_seconds is None
    assert "step two (never run)" in cost.basis


def test_procedure_confidence_is_low_when_any_child_is_low(monkeypatch):
    async def fake_stats(pool, implementation_id):
        if implementation_id == "I-1":
            return _stats("I-1", sample_count=5, success_count=5, mean_wall_seconds=1.0)
        return _stats(implementation_id, sample_count=2, success_count=2, mean_wall_seconds=1.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    tree = _procedure_node("G-parent", "p", children=[
        _impl_node("G-1", "empirical step", "I-1", depth=1),
        _impl_node("G-2", "low-confidence step", "I-2", depth=1),
    ])
    cost = _run(gc.estimate_goal_cost(None, tree))
    assert cost.confidence == "low"
    assert cost.expected_wall_seconds == 2.0  # still a real sum -- low confidence, not "none"


def test_nested_procedure_aggregates_across_both_levels(monkeypatch):
    async def fake_stats(pool, implementation_id):
        return _stats(implementation_id, sample_count=5, success_count=5, mean_wall_seconds=1.0)
    monkeypatch.setattr(gc, "implementation_execution_stats", fake_stats)

    inner = _procedure_node("G-inner", "inner", children=[
        _impl_node("G-1a", "a", "I-1a", depth=2),
        _impl_node("G-1b", "b", "I-1b", depth=2),
    ], depth=1)
    outer = _procedure_node("G-outer", "outer", children=[inner, _impl_node("G-2", "c", "I-2", depth=1)])
    cost = _run(gc.estimate_goal_cost(None, outer))
    assert cost.expected_wall_seconds == 3.0  # 1.0 + 1.0 + 1.0
    assert cost.sample_count == 15
