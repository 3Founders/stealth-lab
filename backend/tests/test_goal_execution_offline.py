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


def _impl_node(goal_id, name, impl_id, alternates=None, verification_requirement=None):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=0, chosen="implementation",
        implementation=_impl(impl_id), implementation_alternates=alternates or [],
        verification_requirement=verification_requirement or {},
    )


def _unresolved_node(goal_id, name):
    return ResolvedGoalNode(goal_id=goal_id, goal_name=name, depth=0, chosen="unresolved", unresolved_reason="x")


def _procedure_node(goal_id, name, children, procedure_alternates=None, procedure_id="P-1"):
    return ResolvedGoalNode(
        goal_id=goal_id, goal_name=name, depth=0, chosen="procedure",
        procedure={"id": procedure_id, "name": procedure_id}, children=children,
        procedure_alternates=procedure_alternates or [],
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


# ---------------------------------------------------------------------
# execute_goal_node -- verification (Sec 9)
# ---------------------------------------------------------------------


def test_no_verification_contract_leaves_a_successful_execution_as_success(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    node = _impl_node("G-1", "do it", "I-1")
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "success"
    assert result.attempts[0].verification_state == "unverified"


def test_failed_verification_on_first_choice_triggers_real_fallback(monkeypatch):
    # Verification is a property of the GOAL (same contract applies to
    # every candidate); what varies per attempt is the REAL execution
    # result each implementation produces, which the verifier inspects.
    async def fake_execute(pool, plan_node, context, *, scope):
        data = {"output_files": {"out.txt": b"x"}} if plan_node.implementation_id == "I-2" else {"output_files": {}}
        return NodeResult(status="success", data=data)

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    node = ResolvedGoalNode(
        goal_id="G-1", goal_name="do it", depth=0, chosen="implementation",
        implementation=_impl("I-1"), implementation_alternates=[_impl("I-2")],
        verification_requirement={"method": "artifact_inspection", "expected_files": ["out.txt"]},
    )
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "success"
    assert result.used_implementation_id == "I-2"
    assert result.attempts[0].status == "failure"
    assert result.attempts[0].verification_state == "failed_verification"
    assert result.attempts[1].verification_state == "checked"


def test_verification_never_runs_when_execution_itself_already_failed(monkeypatch):
    calls = []

    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="failure")

    async def fake_verify(contract, node_result=None, *, executor=None):
        calls.append(contract)
        raise AssertionError("verification must not run for a failed execution")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    monkeypatch.setattr(ge, "run_goal_verification", fake_verify)
    node = _impl_node("G-1", "do it", "I-1", verification_requirement={"method": "human_review"})
    result = _run(ge.execute_goal_node(None, node, {}, scope=SCOPE))
    assert result.status == "failure"
    assert calls == []
    assert result.attempts[0].verification_state is None


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


# ---------------------------------------------------------------------
# execute_goal_tree -- alternate-Procedure fallback (Sec 10)
# ---------------------------------------------------------------------


def test_first_procedure_succeeds_no_alternate_procedure_resolved(monkeypatch):
    calls = []

    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success")

    async def fake_resolve_via_procedure(pool, goal_id, procedure, *, context, scope, depth):
        calls.append(procedure["id"])
        raise AssertionError("must not resolve an alternate when the first procedure already succeeded")

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    monkeypatch.setattr(ge, "resolve_goal_via_procedure", fake_resolve_via_procedure)
    tree = _procedure_node(
        "G-parent", "p", children=[_impl_node("G-1", "step one", "I-1")],
        procedure_alternates=[{"id": "P-2", "name": "strategy-b"}],
    )
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "success"
    assert calls == []
    assert result.procedure_results["G-parent"].used_procedure_id == "P-1"
    assert result.procedure_results["G-parent"].human_intervention_needed is False


def test_first_procedure_fails_falls_back_to_real_alternate_procedure(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success" if plan_node.implementation_id == "I-2" else "failure")

    async def fake_resolve_via_procedure(pool, goal_id, procedure, *, context, scope, depth):
        assert procedure["id"] == "P-2"
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name="p", depth=depth, chosen="procedure",
            procedure=procedure, children=[_impl_node("G-2", "alt step", "I-2")],
        )

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    monkeypatch.setattr(ge, "resolve_goal_via_procedure", fake_resolve_via_procedure)
    tree = _procedure_node(
        "G-parent", "p", children=[_impl_node("G-1", "step one", "I-1")],
        procedure_alternates=[{"id": "P-2", "name": "strategy-b"}],
    )
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "success"
    proc_result = result.procedure_results["G-parent"]
    assert proc_result.used_procedure_id == "P-2"
    assert [a.status for a in proc_result.attempts] == ["failure", "success"]
    assert proc_result.human_intervention_needed is False
    # the failed first attempt's own real sub-result is still kept, not discarded
    assert result.node_results["G-1"].status == "failure"
    assert result.node_results["G-2"].status == "success"


def test_every_procedure_failing_sets_human_intervention_needed(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="failure")

    async def fake_resolve_via_procedure(pool, goal_id, procedure, *, context, scope, depth):
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name="p", depth=depth, chosen="procedure",
            procedure=procedure, children=[_impl_node("G-2", "alt step", "I-2")],
        )

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    monkeypatch.setattr(ge, "resolve_goal_via_procedure", fake_resolve_via_procedure)
    tree = _procedure_node(
        "G-parent", "p", children=[_impl_node("G-1", "step one", "I-1")],
        procedure_alternates=[{"id": "P-2", "name": "strategy-b"}],
    )
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "failure"
    proc_result = result.procedure_results["G-parent"]
    assert proc_result.used_procedure_id is None
    assert proc_result.human_intervention_needed is True
    assert len(proc_result.attempts) == 2


def test_unresolved_step_does_not_spend_a_procedure_fallback_attempt(monkeypatch):
    async def fake_resolve_via_procedure(pool, goal_id, procedure, *, context, scope, depth):
        raise AssertionError("an unresolved (structural gap) child must not trigger an alternate-procedure attempt")

    monkeypatch.setattr(ge, "resolve_goal_via_procedure", fake_resolve_via_procedure)
    tree = _procedure_node(
        "G-parent", "p", children=[_unresolved_node("G-1", "no match")],
        procedure_alternates=[{"id": "P-2", "name": "strategy-b"}],
    )
    result = _run(ge.execute_goal_tree(None, tree, {}, scope=SCOPE))
    assert result.outcome == "needs_input"
    proc_result = result.procedure_results["G-parent"]
    assert len(proc_result.attempts) == 1
    assert proc_result.human_intervention_needed is False


def test_nested_procedure_child_also_gets_real_alternate_fallback(monkeypatch):
    async def fake_execute(pool, plan_node, context, *, scope):
        return NodeResult(status="success" if plan_node.implementation_id == "I-2" else "failure")

    async def fake_resolve_via_procedure(pool, goal_id, procedure, *, context, scope, depth):
        return ResolvedGoalNode(
            goal_id=goal_id, goal_name="inner", depth=depth, chosen="procedure",
            procedure=procedure, children=[_impl_node("G-2", "alt step", "I-2")],
        )

    monkeypatch.setattr(ge, "execute_implementation", fake_execute)
    monkeypatch.setattr(ge, "resolve_goal_via_procedure", fake_resolve_via_procedure)
    inner = _procedure_node(
        "G-inner", "inner", children=[_impl_node("G-1", "step one", "I-1")],
        procedure_alternates=[{"id": "P-2", "name": "strategy-b"}], procedure_id="P-1a",
    )
    outer = _procedure_node("G-outer", "outer", children=[inner], procedure_id="P-outer")
    result = _run(ge.execute_goal_tree(None, outer, {}, scope=SCOPE))
    assert result.outcome == "success"
    assert result.procedure_results["G-inner"].used_procedure_id == "P-2"
    assert result.procedure_results["G-outer"].used_procedure_id == "P-outer"
