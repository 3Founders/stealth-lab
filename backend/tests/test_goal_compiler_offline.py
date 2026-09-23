"""
Pure-logic tests for app.execution.goal_compiler.flatten_goal_tree --
the deterministic Sec 16-18 DAG flattening over an already-resolved
ResolvedGoalNode tree. No database, no I/O; every tree is hand-built.
"""
from __future__ import annotations

from app.execution.goal_compiler import compiled_goal_to_run_md, flatten_goal_tree
from app.execution.goal_resolution import ResolvedGoalNode


def _impl_leaf(goal_id, name, impl_id="I-1", kind="command", depth=0):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=depth, chosen="step",
        step={"order": 0, "procedure_id": impl_id, "binding": {"kind": kind, kind: name}},
        rationale=f"chosen impl for {name}",
    )


def _unresolved_leaf(goal_id, name, reason="no route", depth=0):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=depth, chosen="unresolved", unresolved_reason=reason,
    )


def _procedure_node(goal_id, name, children, depth=0):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=depth, chosen="procedure",
        procedure={"id": "P-1", "procedure_id": "P-1", "name": "some-procedure", "version": 1},
        children=children, rationale="selected some-procedure",
    )


# ---------------------------------------------------------------------
# cardinality (Sec 17)
# ---------------------------------------------------------------------


def test_goal_resolving_straight_to_implementation_is_one_node():
    tree = _impl_leaf("G-1", "find references", impl_id="I-42", kind="command")
    nodes = flatten_goal_tree(tree)
    assert len(nodes) == 1
    assert nodes[0].kind == "step"
    assert nodes[0].procedure_id == "I-42"
    assert nodes[0].executor == "deterministic"
    assert nodes[0].deps == []


def test_unresolved_goal_becomes_one_human_node():
    tree = _unresolved_leaf("G-1", "impossible goal", reason="no procedure linked")
    nodes = flatten_goal_tree(tree)
    assert len(nodes) == 1
    assert nodes[0].kind == "human"
    assert nodes[0].executor == "human"
    assert nodes[0].rationale == "no procedure linked"


def test_procedure_with_all_implementation_children_emits_one_node_per_step():
    tree = _procedure_node("G-parent", "safely modify generated API", children=[
        _impl_leaf("G-1", "identify source of truth", impl_id="I-1", depth=1),
        _impl_leaf("G-2", "regenerate bindings", impl_id="I-2", depth=1),
        _impl_leaf("G-3", "verify generated-drift", impl_id="I-3", depth=1),
    ])
    nodes = flatten_goal_tree(tree)
    assert len(nodes) == 3
    assert [n.procedure_id for n in nodes] == ["I-1", "I-2", "I-3"]


def test_procedure_node_itself_emits_nothing_only_its_children_do():
    """Sec 2: Procedure is decomposition, not executable work -- only
    leaves become real DAG nodes."""
    tree = _procedure_node("G-parent", "p", children=[_impl_leaf("G-1", "child", depth=1)])
    nodes = flatten_goal_tree(tree)
    assert len(nodes) == 1
    assert all(n.goal_id != "G-parent" for n in nodes)


# ---------------------------------------------------------------------
# dependency chaining
# ---------------------------------------------------------------------


def test_sibling_steps_chain_in_order():
    tree = _procedure_node("G-parent", "p", children=[
        _impl_leaf("G-1", "first", impl_id="I-1", depth=1),
        _impl_leaf("G-2", "second", impl_id="I-2", depth=1),
        _impl_leaf("G-3", "third", impl_id="I-3", depth=1),
    ])
    nodes = flatten_goal_tree(tree)
    assert nodes[0].deps == []
    assert nodes[1].deps == [nodes[0].node_id]
    assert nodes[2].deps == [nodes[1].node_id]


def test_nested_procedures_chain_across_the_whole_flattened_sequence():
    """A Procedure step whose own child Goal ALSO resolves through a
    Procedure (nested decomposition) -- every leaf across both levels
    still chains into one single linear sequence, matching Sec 18's own
    "flatten into concrete execution DAG" contract."""
    inner = _procedure_node("G-inner", "inner-procedure", children=[
        _impl_leaf("G-1a", "inner step a", impl_id="I-1a", depth=2),
        _impl_leaf("G-1b", "inner step b", impl_id="I-1b", depth=2),
    ], depth=1)
    tree = _procedure_node("G-outer", "outer-procedure", children=[
        inner,
        _impl_leaf("G-2", "outer step two", impl_id="I-2", depth=1),
    ])
    nodes = flatten_goal_tree(tree)
    assert [n.procedure_id for n in nodes] == ["I-1a", "I-1b", "I-2"]
    assert nodes[0].deps == []
    assert nodes[1].deps == [nodes[0].node_id]
    assert nodes[2].deps == [nodes[1].node_id]  # outer step two depends on the LAST inner leaf


def test_unresolved_step_in_the_middle_still_chains_correctly():
    """Step -> zero executable nodes (Sec 17) becomes a human node here,
    not a gap in the dependency chain -- the next real step still waits
    on it."""
    tree = _procedure_node("G-parent", "p", children=[
        _impl_leaf("G-1", "first", impl_id="I-1", depth=1),
        _unresolved_leaf("G-2", "unmatched step", depth=1),
        _impl_leaf("G-3", "third", impl_id="I-3", depth=1),
    ])
    nodes = flatten_goal_tree(tree)
    assert len(nodes) == 3
    assert nodes[1].kind == "human"
    assert nodes[2].deps == [nodes[1].node_id]
    assert nodes[1].deps == [nodes[0].node_id]


def test_node_ids_are_unique_and_stable_across_a_large_tree():
    children = [_impl_leaf(f"G-{i}", f"step {i}", impl_id=f"I-{i}", depth=1) for i in range(20)]
    tree = _procedure_node("G-parent", "p", children=children)
    nodes = flatten_goal_tree(tree)
    ids = [n.node_id for n in nodes]
    assert len(ids) == len(set(ids)) == 20


# ---------------------------------------------------------------------
# compiled_goal_to_run_md (Sec 13: compile-time, not-yet-executed trace)
# ---------------------------------------------------------------------


def test_compiled_run_md_marks_implementation_nodes_as_planned_never_success():
    tree = _impl_leaf("G-1", "find references", impl_id="I-42")
    nodes = flatten_goal_tree(tree)
    md = compiled_goal_to_run_md(nodes)
    assert "GOAL_RUN|-|planned" in md
    assert "GOAL_NODE|G-1|step|planned|find references|binding=-" in md
    assert "success" not in md and "failure" not in md


def test_compiled_run_md_marks_human_nodes_as_needs_input():
    tree = _unresolved_leaf("G-1", "no match", reason="nothing links")
    nodes = flatten_goal_tree(tree)
    md = compiled_goal_to_run_md(nodes)
    assert "GOAL_NODE|G-1|human|needs_input|no match|binding=-" in md


def test_compiled_run_md_never_fabricates_an_execution_id():
    tree = _impl_leaf("G-1", "find references")
    nodes = flatten_goal_tree(tree)
    md = compiled_goal_to_run_md(nodes)
    assert "GOAL_RUN|-|" in md  # '-' is the honest "no real execution attempt" id


def test_compiled_run_md_covers_a_multi_node_real_procedure_chain():
    tree = _procedure_node("G-parent", "p", children=[
        _impl_leaf("G-1", "first", impl_id="I-1", depth=1),
        _unresolved_leaf("G-2", "unmatched", depth=1),
        _impl_leaf("G-3", "third", impl_id="I-3", depth=1),
    ])
    nodes = flatten_goal_tree(tree)
    md = compiled_goal_to_run_md(nodes)
    data_lines = [ln for ln in md.splitlines() if ln.startswith("GOAL_NODE|")]
    assert len(data_lines) == 3
    assert "GOAL_NODE|G-1|step|planned|first|binding=-" in md
    assert "GOAL_NODE|G-2|human|needs_input|unmatched|binding=-" in md
    assert "GOAL_NODE|G-3|step|planned|third|binding=-" in md
