"""
DB-free coverage for app.mcp_server.server::_try_registered_implementation
-- the meta-harness Sec 19-21 wiring that lets find_best_way's tier-2
run_node closure actually dispatch through the real, pluggable
implementation_executor.execute_implementation system for a node bound to
a non-frontier implementation, instead of unconditionally calling the
frontier coding agent.

No real DB, no real LLM/network call, no real sandbox -- every dependency
(implementation_registry.get, execute_implementation) is monkeypatched,
proving this function's own branching logic in isolation. This is exactly
why the logic was factored out of the enormous find_best_way closure: it
would otherwise require driving the whole MCP tool (real client, real
sandbox, real ancestor-chain checks) just to prove this branch.
"""
from __future__ import annotations

import asyncio

import app.mcp_server.server as srv
from app.execution.graph_executor import NodeResult
from app.models.plan import PlanNode


def _run(coro):
    return asyncio.run(coro)


def _node(**overrides):
    kwargs = dict(order=0, goal="regenerate schema bindings", implementation_id=None)
    kwargs.update(overrides)
    return PlanNode(**kwargs)


def test_no_implementation_id_returns_none_and_never_touches_registry(monkeypatch):
    async def fail_get(pool, implementation_id, *, scope):
        raise AssertionError("must not be called when node.implementation_id is None")

    monkeypatch.setattr(srv.implementation_registry, "get", fail_get)

    result = _run(srv._try_registered_implementation(
        pool=None, node=_node(implementation_id=None),
        task_description="t", repo_path="/tmp/x", model="m", max_steps=5, node_notes=[],
    ))
    assert result is None


def test_implementation_no_longer_visible_returns_none(monkeypatch):
    async def fake_get(pool, implementation_id, *, scope):
        return None  # deleted/invisible/wrong scope

    monkeypatch.setattr(srv.implementation_registry, "get", fake_get)

    result = _run(srv._try_registered_implementation(
        pool=None, node=_node(implementation_id="00000000-0000-4000-8000-000000000001"),
        task_description="t", repo_path="/tmp/x", model="m", max_steps=5, node_notes=[],
    ))
    assert result is None


def test_frontier_kind_falls_through_and_never_calls_execute_implementation(monkeypatch):
    async def fake_get(pool, implementation_id, *, scope):
        return {"id": implementation_id, "kind": "frontier", "name": "frontier-default"}

    async def fail_execute(pool, node, context, *, scope):
        raise AssertionError("must not dispatch a frontier-kind node through execute_implementation")

    import app.execution.implementation_executor as impl_executor_module
    monkeypatch.setattr(srv.implementation_registry, "get", fake_get)
    monkeypatch.setattr(impl_executor_module, "execute_implementation", fail_execute)

    result = _run(srv._try_registered_implementation(
        pool=None, node=_node(implementation_id="00000000-0000-4000-8000-000000000001"),
        task_description="t", repo_path="/tmp/x", model="m", max_steps=5, node_notes=[],
    ))
    assert result is None


def test_non_frontier_kind_dispatches_and_returns_agent_run_standin(monkeypatch):
    IMPL_ID = "00000000-0000-4000-8000-0000000000bb"

    async def fake_get(pool, implementation_id, *, scope):
        assert implementation_id == IMPL_ID
        return {"id": IMPL_ID, "kind": "deterministic", "name": "regenerate-api"}

    async def fake_execute(pool, node, context, *, scope):
        assert context["task_description"] == "regen the api"
        assert context["repo_path"] == "/tmp/x"
        return NodeResult(
            status="success", notes="ran fine",
            data={"patch": "diff --git a/x b/x", "files_edited": ["schema/api.yaml"],
                  "prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0, "tool_names": []},
        )

    import app.execution.implementation_executor as impl_executor_module
    monkeypatch.setattr(srv.implementation_registry, "get", fake_get)
    monkeypatch.setattr(impl_executor_module, "execute_implementation", fake_execute)

    node_notes: list[str] = []
    outcome = _run(srv._try_registered_implementation(
        pool=None, node=_node(implementation_id=IMPL_ID),
        task_description="regen the api", repo_path="/tmp/x", model="m", max_steps=5,
        node_notes=node_notes,
    ))
    assert outcome is not None
    result, agent_run_standin = outcome
    assert result.status == "success"
    assert agent_run_standin.patch == "diff --git a/x b/x"
    assert agent_run_standin.files_edited == ["schema/api.yaml"]
    assert agent_run_standin.usage.prompt_tokens == 0
    assert agent_run_standin.stop_reason == "finished"
    assert agent_run_standin.error is None
    assert any("regenerate-api" in n for n in node_notes)


def test_non_frontier_failure_is_reported_honestly_not_masked(monkeypatch):
    IMPL_ID = "00000000-0000-4000-8000-0000000000cc"

    async def fake_get(pool, implementation_id, *, scope):
        return {"id": IMPL_ID, "kind": "deterministic", "name": "flaky-script"}

    async def fake_execute(pool, node, context, *, scope):
        return NodeResult(status="failure", notes="exit code 1: script failed", data={})

    import app.execution.implementation_executor as impl_executor_module
    monkeypatch.setattr(srv.implementation_registry, "get", fake_get)
    monkeypatch.setattr(impl_executor_module, "execute_implementation", fake_execute)

    outcome = _run(srv._try_registered_implementation(
        pool=None, node=_node(implementation_id=IMPL_ID),
        task_description="t", repo_path="/tmp/x", model="m", max_steps=5, node_notes=[],
    ))
    result, agent_run_standin = outcome
    assert result.status == "failure"
    assert agent_run_standin.error == "exit code 1: script failed"
    assert agent_run_standin.stop_reason == "exit code 1: script failed"
