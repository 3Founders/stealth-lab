"""
DB-free coverage for app.execution.goal_execution -- the real
fallback-on-failure Goal-DAG executor (Prompt 2 Sec 7/9/10).
`execute_implementation` (implementation_executor.py's own real,
already-tested chokepoint) is monkeypatched per test so these tests
prove goal_execution's own fallback/walk logic in isolation, not
execute_implementation's own dispatch (covered by
test_implementation_executor_offline.py).
"""
from __future__ import annotations

import asyncio

import app.execution.goal_execution as ge
from app.execution.goal_resolution import ResolvedGoalNode
from app.execution.graph_executor import NodeResult
from app.services.access import AccessScope

SCOPE = AccessScope.unrestricted()


def _run(coro):
    return asyncio.run(coro)


def _impl(id_, name="impl", kind="deterministic"):
    return {"id": id_, "name": name, "kind": kind}


def _impl_node(goal_id, name, impl_id, alternates=None):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=0, chosen="implementation",
        implementation=_impl(impl_id), implementation_alternates=alternates or [],
    )


def _unresolved_node(goal_id, name):
    return ResolvedGoalNode(goal_id=goal_id, goal_name=name, depth=0, chosen="unresolved", unresolved_reason="x")


def _procedure_node(goal_id, name, children):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=0, chosen="procedure",
        procedure={"id": "P-1"}, children=children,
    )


# ---------------------------------------------------------------------
# execute_goal_node -- fallback
# ---------------------------------------------------------------------


def test_first_implementation_succeeds_no_fallback_needed(monkeypatch):
    calls = []

    async def fake_execute(pool, plan_node, context, *, scope):
        calls.append(plan_node.implementation_id)
        return NodeResult(status="success", notes="ok")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    node = _impl_node("G-1", "do it", "I-1", alternates=[_impl("I-2")])
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "success"
    assert result.used_implementation_id == "I-1"
    assert len(result.attempts) == 1
    assert calls == ["I-1"]


def test_first_implementation_fails_falls_back_to_real_alternate(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        if plan_node.implementation_id == "I-1":
            return NodeResult(status="failure", notes="broke")
        return NodeResult(status="success", notes="ok")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    node = _impl_node("G-1", "do it", "I-1", alternates=[_impl("I-2"), _impl("I-3")])
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "success"
    assert result.used_implementation_id == "I-2"
    assert [a.implementation_id for a in result.attempts] == ["I-1", "I-2"]
    assert result.attempts[0].status == "failure"
    assert result.attempts[1].status == "success"


def test_all_candidates_fail_is_an_honest_failure_never_a_different_goal(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="failure", notes=f"{plan_node.implementation_id} broke")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    node = _impl_node("G-1", "do it", "I-1", alternates=[_impl("I-2")])
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "failure"
    assert result.used_implementation_id is None
    assert [a.implementation_id for a in result.attempts] == ["I-1", "I-2"]
    # every attempt targeted THIS SAME goal's own candidates -- never substituted
    assert result.goal_id == "G-1"


def test_no_alternates_means_a_single_real_attempt(monkeypatch):
    calls = []

    async def fake_execute(pool, plan_node, context, *, scope):
        calls.append(plan_node.implementation_id)
        return NodeResult(status="failure")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    node = _impl_node("G-1", "do it", "I-1")
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "failure"
    assert calls == ["I-1"]


# ---------------------------------------------------------------------
# execute_goal_tree -- walk
# ---------------------------------------------------------------------


def test_tree_of_all_successes_is_overall_success(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    tree = _procedure_node("G-parent", "p", children=[
        _impl_node("G-1", "step one", "I-1"),
        _impl_node("G-2", "step two", "I-2"),
    ])
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "success"
    assert set(result.node_results) == {"G-1", "G-2"}


def test_unresolved_leaf_makes_overall_outcome_needs_input(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    tree = _procedure_node("G-parent", "p", children=[
        _impl_node("G-1", "step one", "I-1"),
        _unresolved_node("G-2", "step two (no match)"),
    ])
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "needs_input"
    assert result.unresolved_goal_names == ["step two (no match)"]
    # the resolved sibling still actually ran -- an unresolved branch
    # doesn't silently cancel the rest of the tree
    assert "G-1" in result.node_results


def test_a_real_node_failure_after_exhausting_fallbacks_makes_overall_outcome_failure(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="failure" if plan_node.implementation_id == "I-2" else "success")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    tree = _procedure_node("G-parent", "p", children=[
        _impl_node("G-1", "step one", "I-1"),
        _impl_node("G-2", "step two", "I-2"),
    ])
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "failure"
    assert result.node_results["G-1"].status == "success"
    assert result.node_results["G-2"].status == "failure"


def test_bare_direct_implementation_root_executes_without_a_procedure(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    tree = _impl_node("G-1", "do it directly", "I-1")
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "success"
    assert result.node_results["G-1"].used_implementation_id == "I-1"
