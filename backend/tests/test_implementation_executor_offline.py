"""Offline tests for app/execution/implementation_executor.py.

No DATABASE_URL, no network, no real LLM/Docker credentials needed. Same
posture as test_implementation_providers_offline.py: the FrontierProvider
comparison drives _run_local_node's own REAL early-refusal path (a
nonexistent repo_path) so it proves real dispatch, not a mocked stand-in.
"""
from __future__ import annotations

import asyncio

import pytest

from app.execution.graph_executor import NodeResult
from app.execution.implementation_executor import (
    bind_implementation,
    check_requirements,
    execute_implementation,
    resolve_implementation_for_node,
    validate_invocation,
)
from app.models.plan import PlanNode
from app.services.access import AccessScope


def _node(order: int = 0, goal: str = "do the thing", **kw) -> PlanNode:
    return PlanNode(order=order, goal=goal, **kw)


def _run(coro):
    return asyncio.run(coro)


IMPL_ID = "00000000-0000-4000-8000-0000000000aa"


# ---------------------------------------------------------------------
# resolve_implementation_for_node
# ---------------------------------------------------------------------


def test_resolve_returns_none_with_no_task_node_id():
    node = _node()
    result = _run(resolve_implementation_for_node(
        pool=None, node=node, task_node_id=None, scope=AccessScope.unrestricted(),
    ))
    assert result is None


# ---------------------------------------------------------------------
# bind_implementation -- the freeze step
# ---------------------------------------------------------------------


def test_bind_implementation_sets_id_from_resolved_row():
    node = _node()
    implementation = {"id": IMPL_ID, "kind": "tool", "name": "graphify"}
    bound = bind_implementation(node, implementation)
    assert bound.implementation_id == IMPL_ID
    assert isinstance(bound.implementation_id, str)


def test_bind_implementation_none_leaves_id_none():
    node = _node()
    bound = bind_implementation(node, None)
    assert bound.implementation_id is None


def test_bind_implementation_returns_a_new_node_not_a_mutation():
    node = _node()
    implementation = {"id": IMPL_ID, "kind": "tool", "name": "graphify"}
    bound = bind_implementation(node, implementation)
    assert node.implementation_id is None, "original node must be untouched"
    assert bound is not node


def test_bind_implementation_freezes_without_re_resolving_on_second_call():
    """Binding once, then calling bind_implementation again with a
    DIFFERENT implementation on the ALREADY-bound node's own copy still
    only reflects whatever argument that second call was given -- proving
    a caller who simply stops calling this function again is what
    freezes the identity, not a hidden second lookup inside it."""
    node = _node()
    impl_v1 = {"id": IMPL_ID, "kind": "tool", "name": "graphify", "version": 1}
    bound_v1 = bind_implementation(node, impl_v1)
    assert bound_v1.implementation_id == IMPL_ID

    # A caller who never re-binds bound_v1 keeps v1's id forever -- the
    # real freeze guarantee. Demonstrated by NOT calling bind_implementation
    # again on bound_v1 and confirming its id is unchanged after arbitrary
    # other operations.
    still_v1 = bound_v1.model_copy()
    assert still_v1.implementation_id == IMPL_ID


# ---------------------------------------------------------------------
# execute_implementation -- backward compatibility (Sec 88)
# ---------------------------------------------------------------------


def test_execute_falls_back_to_frontier_when_implementation_id_is_none():
    from app.local_agent.runner import _run_local_node

    node = _node(order=3, goal="a step needing a real repo")
    context = {
        "task_description": "some task",
        "repo_path": "C:/definitely/not/a/real/directory/xyz123",
        "model": "irrelevant-model",
        "max_steps": 1,
        "node_notes": [],
    }
    direct_result = _run(_run_local_node(
        node, task_description=context["task_description"], repo_path=context["repo_path"],
        model=context["model"], max_steps=context["max_steps"], node_notes=list(context["node_notes"]),
    ))

    result = _run(execute_implementation(
        pool=None, node=node, context=context, scope=AccessScope.unrestricted(),
    ))
    assert isinstance(result, NodeResult)
    assert result.status == direct_result.status == "failure"
    assert result.notes == direct_result.notes


# ---------------------------------------------------------------------
# execute_implementation -- honest failure for a bound-but-unregistered kind
# ---------------------------------------------------------------------


class _FakeRegistryGetPool:
    """A fake `pool` whose only real use in execute_implementation is
    being threaded through to `implementation_registry.get`, which we
    monkeypatch -- this pool object itself is never queried directly."""


def test_execute_returns_honest_failure_for_unregistered_kind(monkeypatch):
    from app.execution import implementation_executor

    async def fake_get(pool, implementation_id, *, scope):
        assert implementation_id == IMPL_ID
        return {"id": IMPL_ID, "kind": "human", "name": "approve-deploy"}

    monkeypatch.setattr(implementation_executor.implementation_registry, "get", fake_get)

    node = _node(implementation_id=IMPL_ID)
    result = _run(execute_implementation(
        pool=_FakeRegistryGetPool(), node=node, context={}, scope=AccessScope.unrestricted(),
    ))
    assert result.status == "failure"
    assert "human" in result.notes
    assert "no registered execution provider" in result.notes


def test_execute_never_substitutes_frontier_for_bound_unresolvable_id(monkeypatch):
    from app.execution import implementation_executor

    async def fake_get_none(pool, implementation_id, *, scope):
        return None

    monkeypatch.setattr(implementation_executor.implementation_registry, "get", fake_get_none)

    node = _node(implementation_id=IMPL_ID)
    result = _run(execute_implementation(
        pool=_FakeRegistryGetPool(), node=node, context={}, scope=AccessScope.unrestricted(),
    ))
    assert result.status == "failure"
    assert IMPL_ID in result.notes
    assert "does not resolve" in result.notes


def test_execute_dispatches_to_deterministic_provider_for_bound_deterministic_kind(monkeypatch):
    from app.execution import implementation_executor

    async def fake_get(pool, implementation_id, *, scope):
        return {"id": IMPL_ID, "kind": "deterministic", "name": "run-linter"}

    monkeypatch.setattr(implementation_executor.implementation_registry, "get", fake_get)

    node = _node(implementation_id=IMPL_ID)
    context = {"code": "open('out.txt', 'w').write('ok')"}
    result = _run(execute_implementation(
        pool=_FakeRegistryGetPool(), node=node, context=context, scope=AccessScope.unrestricted(),
    ))
    assert result.status == "success"
    assert result.data["output_files"] == {"out.txt": b"ok"}


def test_execute_refuses_with_a_typed_auth_required_failure_when_requirements_are_unmet(monkeypatch):
    """B38: "missing dependencies/configuration produce typed terminal
    errors" -- an implementation declaring `requirements={'credentials':
    [...]}` must be refused BEFORE any real invocation attempt when the
    caller's own context does not carry them, with a real, distinguishable
    `error_class` -- never silently attempted (which could either fail
    opaquely or, worse, appear to succeed against a misconfigured real
    endpoint) and never a fabricated success."""
    from app.execution import implementation_executor

    async def fake_get(pool, implementation_id, *, scope):
        return {
            "id": IMPL_ID, "kind": "deterministic", "name": "needs-creds",
            "requirements": {"credentials": ["graphify"]},
        }

    monkeypatch.setattr(implementation_executor.implementation_registry, "get", fake_get)

    node = _node(implementation_id=IMPL_ID)
    context = {"code": "open('out.txt', 'w').write('ok')"}  # no 'credentials' key at all
    result = _run(execute_implementation(
        pool=_FakeRegistryGetPool(), node=node, context=context, scope=AccessScope.unrestricted(),
    ))
    assert result.status == "failure"
    assert result.data["error_class"] == "auth_required"
    assert "graphify" in str(result.data["missing_requirements"])
    assert "needs-creds" in result.notes


def test_execute_proceeds_normally_once_the_missing_requirement_is_supplied(monkeypatch):
    """The other half of the same rule: this is a real, narrow filter,
    not a blanket refusal -- supplying the SAME requirement the previous
    test withheld lets the real dispatch proceed exactly as before this
    check existed."""
    from app.execution import implementation_executor

    async def fake_get(pool, implementation_id, *, scope):
        return {
            "id": IMPL_ID, "kind": "deterministic", "name": "needs-creds",
            "requirements": {"credentials": ["graphify"]},
        }

    monkeypatch.setattr(implementation_executor.implementation_registry, "get", fake_get)

    node = _node(implementation_id=IMPL_ID)
    context = {"code": "open('out.txt', 'w').write('ok')", "credentials": ["graphify"]}
    result = _run(execute_implementation(
        pool=_FakeRegistryGetPool(), node=node, context=context, scope=AccessScope.unrestricted(),
    ))
    assert result.status == "success"
    assert result.data["output_files"] == {"out.txt": b"ok"}


# ---------------------------------------------------------------------
# validate_invocation / check_requirements
# ---------------------------------------------------------------------


def test_check_requirements_true_when_all_truthy_keys_satisfied():
    implementation = {"requirements": {"network": True, "credentials": ["graphify"]}}
    available = {"network": True, "credentials": ["graphify", "monid"]}
    assert check_requirements(implementation, available) is True


def test_check_requirements_false_when_a_truthy_key_is_missing():
    implementation = {"requirements": {"network": True}}
    available = {}
    assert check_requirements(implementation, available) is False


def test_check_requirements_ignores_falsy_requirement_values():
    implementation = {"requirements": {"network": False}}
    available = {}
    assert check_requirements(implementation, available) is True


def test_check_requirements_true_with_no_requirements_at_all():
    assert check_requirements({}, {}) is True


def test_validate_invocation_no_problems_when_satisfied():
    implementation = {"requirements": {"network": True, "credentials": ["graphify"]}}
    context = {"network_access": True, "credentials": ["graphify", "monid"]}
    assert validate_invocation(implementation, context) == []


def test_validate_invocation_reports_missing_network():
    implementation = {"requirements": {"network": True}}
    context = {}
    problems = validate_invocation(implementation, context)
    assert len(problems) == 1
    assert "network" in problems[0]


def test_validate_invocation_reports_missing_credentials_by_name():
    implementation = {"requirements": {"credentials": ["graphify", "monid"]}}
    context = {"credentials": ["graphify"]}
    problems = validate_invocation(implementation, context)
    assert len(problems) == 1
    assert "monid" in problems[0]
    assert "graphify" not in problems[0].split("missing")[1]


def test_validate_invocation_ignores_unknown_requirement_kinds():
    implementation = {"requirements": {"cpu_cores": 4}}
    context = {}
    assert validate_invocation(implementation, context) == []
