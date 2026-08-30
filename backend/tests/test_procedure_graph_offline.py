"""Proving tests for app/execution/procedure_graph.py -- the pure
steps -> linear-deps PlanNode conversion, no DB, no LLM."""
from __future__ import annotations

from app.execution.procedure_graph import steps_to_linear_nodes


def test_linear_chain_derived_from_order():
    steps = [
        {"order": 0, "goal": "locate the bug"},
        {"order": 1, "goal": "fix the bug"},
        {"order": 2, "goal": "verify the fix"},
    ]
    nodes = steps_to_linear_nodes(steps)
    assert [(n.order, n.deps) for n in nodes] == [(0, []), (1, [0]), (2, [1])]


def test_out_of_order_input_is_sorted_first():
    """A procedure's steps aren't guaranteed to arrive pre-sorted (JSONB
    array order isn't a DB-enforced invariant) -- this must sort by the
    real `order` field, not trust input order."""
    steps = [
        {"order": 2, "goal": "third"},
        {"order": 0, "goal": "first"},
        {"order": 1, "goal": "second"},
    ]
    nodes = steps_to_linear_nodes(steps)
    assert [n.goal for n in nodes] == ["first", "second", "third"]
    assert [(n.order, n.deps) for n in nodes] == [(0, []), (1, [0]), (2, [1])]


def test_single_step_procedure_has_no_deps():
    nodes = steps_to_linear_nodes([{"order": 0, "goal": "do the whole thing"}])
    assert len(nodes) == 1
    assert nodes[0].deps == []


def test_non_contiguous_order_values_still_chain_correctly():
    """REAL BUG this pass found live: a real stored procedure's `order`
    values are not guaranteed contiguous/0-indexed (some real rows are
    1-indexed). deps must be derived from POSITION in sorted order, not
    from `order - 1` arithmetic, which broke against exactly this shape."""
    steps = [
        {"order": 1, "goal": "first"},
        {"order": 2, "goal": "second"},
        {"order": 3, "goal": "third"},
    ]
    nodes = steps_to_linear_nodes(steps)
    assert [(n.order, n.deps) for n in nodes] == [(1, []), (2, [1]), (3, [2])]


def test_legacy_action_shaped_steps_are_handled():
    """REAL BUG this pass found live: a real, older-shaped procedure
    stores steps with `action` instead of `goal` (the same legacy shape
    _render_step() already defends against) -- a bare `s["goal"]` raised
    KeyError against it. This pins the fallback."""
    steps = [{"order": 0, "action": "Call Bash (16x)"}]
    nodes = steps_to_linear_nodes(steps)
    assert nodes[0].goal == "Call Bash (16x)"


def test_extra_step_fields_are_ignored():
    """A real stored step can carry more than order/goal (e.g. an inline
    tool binding) -- this conversion only needs the two fields it uses."""
    steps = [
        {"order": 0, "goal": "step one", "allowed_implementations": [{"name": "bash"}]},
    ]
    nodes = steps_to_linear_nodes(steps)
    assert nodes[0].goal == "step one"
