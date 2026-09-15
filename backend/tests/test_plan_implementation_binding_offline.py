"""Offline tests for app/execution/implementation_executor.py::
bind_plan_implementations -- the binding STAGE that runs AFTER
compile_plan() and BEFORE persist_compiled_plan().

No DATABASE_URL, no network. Proves the two things that matter offline:

  1. compile_plan()'s own purity is untouched -- this stage is a
     genuinely separate pass, never folded into compile_plan itself, and
     when nothing resolves it hands back the exact same CompiledPlan
     object compile_plan produced (no re-hash, no re-copy, nothing).
  2. when something DOES resolve (via a monkeypatched registry -- no
     real pool needed to prove the composition), the returned CompiledPlan
     is a NEW object (immutability respected via model_copy), the bound
     node carries the real implementation_id, and graph_hash/content_hash
     are recomputed consistently with plans.py's own recipe -- proven by
     asserting the SAME real hashing helpers, called directly on the
     bound nodes, produce identical hashes.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.execution.implementation_executor import bind_plan_implementations
from app.execution.plans import CompiledPlan, _graph_content, canonical_json, compile_plan, sha256_hex
from app.models.plan import ExecutionPlan, TaskGraph
from app.services.access import AccessScope

PROC_ID = uuid4()
PROC_ROW_ID = uuid4()

PROCEDURE_PAYLOAD = {
    "id": PROC_ID,
    "version": 1,
    "name": "deploy-api",
    "goal": "ship the API to staging",
    "steps": [{"order": 0, "goal": "run migrations"}],
}

ONE_NODE = [{"order": 0, "goal": "run migrations", "deps": []}]


def _compile(**overrides) -> CompiledPlan:
    kwargs = dict(
        procedure_id=PROC_ID,
        procedure_version=1,
        procedure_row_id=PROC_ROW_ID,
        procedure_payload=PROCEDURE_PAYLOAD,
        task_description="deploy commit abc123",
        nodes=ONE_NODE,
        extractor_version="plan_compiler@1",
    )
    kwargs.update(overrides)
    return compile_plan(**kwargs)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# (1) compile_plan's own purity is untouched by this stage
# ---------------------------------------------------------------------


def test_no_task_node_ids_returns_compile_plan_output_unchanged():
    """No mapping given -> resolve_implementation_for_node's own honest
    None (no task_node_id, no pool touched) for every node -> nothing to
    bind -> the exact same CompiledPlan object compile_plan() produced,
    not merely an equal one."""
    compiled = _compile()
    bound = _run(bind_plan_implementations(
        pool=None, compiled=compiled, scope=AccessScope.unrestricted(),
    ))
    assert bound is compiled, "nothing resolvable -- binding must be a true no-op pass-through"
    assert bound.graph.nodes[0].implementation_id is None
    assert bound.plan.content_hash == compiled.plan.content_hash
    assert bound.graph.graph_hash == compiled.graph.graph_hash


def test_task_node_ids_mapping_with_no_registry_match_still_leaves_ids_none(monkeypatch):
    """A real mapping is supplied (a task_node_id IS known for the node)
    but pool=None / no implementation resolves -- resolve_implementation_
    for_node calls into implementation_registry.resolve(), monkeypatched
    here to prove the honest 'nothing registered' path leaves the node
    exactly as compile_plan left it."""
    from app.execution import implementation_executor

    async def fake_resolve(pool, task_node_id, *, scope, hint_kinds=None):
        return None

    monkeypatch.setattr(implementation_executor.implementation_registry, "resolve", fake_resolve)

    compiled = _compile()
    bound = _run(bind_plan_implementations(
        pool=object(), compiled=compiled, scope=AccessScope.unrestricted(),
        task_node_ids={0: "some-real-task-node-id"},
    ))
    assert bound is compiled
    assert bound.graph.nodes[0].implementation_id is None


def test_compile_plan_itself_is_never_called_by_binding(monkeypatch):
    """Binding is a genuinely separate pass: it must not re-invoke
    compile_plan under the hood to do its work."""
    from app.execution import implementation_executor

    calls = []
    real_compile_plan = compile_plan

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_compile_plan(*args, **kwargs)

    # compile_plan is not even imported into implementation_executor's
    # namespace -- confirm that directly, which is the real proof.
    assert not hasattr(implementation_executor, "compile_plan")

    compiled = _compile()
    _run(bind_plan_implementations(pool=None, compiled=compiled, scope=AccessScope.unrestricted()))
    assert calls == []


# ---------------------------------------------------------------------
# (2) when something resolves: new object, real id, consistent re-hash
# ---------------------------------------------------------------------


IMPL_ID = "00000000-0000-4000-8000-0000000000bb"


def test_resolved_implementation_produces_new_object_with_bound_id_and_consistent_hashes(monkeypatch):
    from app.execution import implementation_executor

    async def fake_resolve(pool, task_node_id, *, scope, hint_kinds=None):
        assert task_node_id == "real-task-node-id"
        return {"id": IMPL_ID, "kind": "tool", "name": "graphify"}

    monkeypatch.setattr(implementation_executor.implementation_registry, "resolve", fake_resolve)

    compiled = _compile()
    bound = _run(bind_plan_implementations(
        pool=object(), compiled=compiled, scope=AccessScope.unrestricted(),
        task_node_ids={0: "real-task-node-id"},
    ))

    assert bound is not compiled, "a real bind must hand back a NEW CompiledPlan"
    assert bound.graph is not compiled.graph
    assert bound.plan is not compiled.plan
    # original untouched (immutability convention -- model_copy, never mutate)
    assert compiled.graph.nodes[0].implementation_id is None
    assert bound.graph.nodes[0].implementation_id == IMPL_ID

    # graph_hash recomputed with the SAME recipe plans.py's own compile_plan
    # uses -- proven by calling that exact recipe directly here.
    expected_graph_hash = sha256_hex(canonical_json(_graph_content(bound.graph.nodes)))
    assert bound.graph.graph_hash == expected_graph_hash
    assert bound.graph.graph_hash != compiled.graph.graph_hash, (
        "bound node content differs from unbound -- hashes must differ too"
    )
    assert bound.plan.content_hash != compiled.plan.content_hash

    # everything else about the plan (ids, scope, task description, ...)
    # is carried forward unchanged -- only the hashes and the graph's
    # nodes actually changed.
    assert bound.plan.id == compiled.plan.id
    assert bound.graph.id == compiled.graph.id
    assert bound.plan.task_description == compiled.plan.task_description


# ---------------------------------------------------------------------
# (3) opt-in goal-based fallback (Sec 8-10 wiring) -- strictly additive
# ---------------------------------------------------------------------


def test_goal_fallback_defaults_off_and_is_never_called(monkeypatch):
    """use_goal_fallback defaults False -- byte-identical to every
    caller/test written before this parameter existed. Proven by
    asserting select_implementation_for_goal is never even invoked when
    task_node_id-based resolution finds nothing and the flag is omitted."""
    from app.execution import implementation_executor

    called = []

    async def spy_select(pool, goal, *, scope, context=None, weights=None):
        called.append(goal)
        raise AssertionError("must not be called when use_goal_fallback=False")

    monkeypatch.setattr(implementation_executor, "select_implementation_for_goal", spy_select)

    compiled = _compile()
    bound = _run(bind_plan_implementations(pool=None, compiled=compiled, scope=AccessScope.unrestricted()))
    assert bound is compiled
    assert called == []


def test_goal_fallback_binds_when_task_node_id_resolves_nothing(monkeypatch):
    """use_goal_fallback=True: when task_node_id-based resolution finds
    nothing (the overwhelmingly common case today), select_implementation_
    for_goal is tried with the node's own goal text, and a real chosen
    implementation gets bound exactly like a task_node_id-based one would."""
    from app.execution import implementation_executor
    from app.execution.implementation_selection import SelectionResult

    async def fake_resolve(pool, task_node_id, *, scope, hint_kinds=None):
        return None  # nothing linked via task_node_id, the common case

    goal_calls = []

    async def fake_select(pool, goal, *, scope, context=None, weights=None):
        goal_calls.append(goal)
        return SelectionResult(
            goal=goal, candidates_considered=[{"id": IMPL_ID}], ranked=[],
            chosen={"id": IMPL_ID, "kind": "tool", "name": "graphify"},
            rationale="test fixture",
        )

    monkeypatch.setattr(implementation_executor.implementation_registry, "resolve", fake_resolve)
    monkeypatch.setattr(implementation_executor, "select_implementation_for_goal", fake_select)

    compiled = _compile()
    bound = _run(bind_plan_implementations(
        pool=object(), compiled=compiled, scope=AccessScope.unrestricted(), use_goal_fallback=True,
    ))

    assert goal_calls == ["run migrations"]  # PROCEDURE_PAYLOAD's own step goal
    assert bound.graph.nodes[0].implementation_id == IMPL_ID


def test_task_node_id_resolution_wins_over_goal_fallback(monkeypatch):
    """When BOTH would resolve, task_node_id-based resolution runs first
    and wins -- goal-based fallback is never even attempted for that
    node, so a node with real linkage is never second-guessed by a
    goal-text coincidence."""
    from app.execution import implementation_executor

    async def fake_resolve(pool, task_node_id, *, scope, hint_kinds=None):
        return {"id": IMPL_ID, "kind": "tool", "name": "graphify"}

    async def fake_select(pool, goal, *, scope, context=None, weights=None):
        raise AssertionError("must not be called when task_node_id resolution already succeeded")

    monkeypatch.setattr(implementation_executor.implementation_registry, "resolve", fake_resolve)
    monkeypatch.setattr(implementation_executor, "select_implementation_for_goal", fake_select)

    compiled = _compile()
    bound = _run(bind_plan_implementations(
        pool=object(), compiled=compiled, scope=AccessScope.unrestricted(),
        task_node_ids={0: "real-task-node-id"}, use_goal_fallback=True,
    ))
    assert bound.graph.nodes[0].implementation_id == IMPL_ID
