"""Offline tests for app.execution.providers (directive Phase 14).

No DATABASE_URL, no network, no real LLM/Docker credentials needed. The
FrontierProvider/_run_local_node comparison below deliberately drives
_run_local_node's own REAL early-refusal path (a nonexistent repo_path)
rather than mocking it away, so this proves FrontierProvider really
calls through to the real function and preserves its real outcome
shape -- not a fabricated comparison.
"""
from __future__ import annotations

import asyncio

import pytest

from app.execution.graph_executor import NodeResult
from app.execution.implementations import IMPLEMENTATION_KINDS
from app.execution.providers import (
    DeterministicProvider,
    FrontierProvider,
    ProviderAvailability,
    discover_providers,
    get_provider,
)
from app.models.plan import PlanNode


def _node(order: int = 0, goal: str = "do the thing") -> PlanNode:
    return PlanNode(order=order, goal=goal)


# ---------------------------------------------------------------------
# discover_providers(): honest reporting across the whole vocabulary
# ---------------------------------------------------------------------


def test_discover_providers_covers_every_implementation_kind():
    entries = asyncio.run(discover_providers())
    kinds_seen = {e.kind for e in entries}
    assert kinds_seen == set(IMPLEMENTATION_KINDS)


def test_unavailable_kinds_report_available_false_with_honest_reason():
    # 'tool' now has a REAL executor (McpToolAdapter, B25/B27's Adapter
    # Resolver, app.execution.adapters.build_adapter) -- it correctly
    # reports a provider_name, not None. Only 'slm'/'human' still have
    # no real executor anywhere in this codebase.
    entries = asyncio.run(discover_providers())
    by_kind = {e.kind: e for e in entries}
    for kind in ("slm", "human"):
        entry = by_kind[kind]
        assert entry.provider_name is None
        assert entry.availability.available is False
        assert entry.availability.reason  # non-empty, real explanation
        assert "no real executor" in entry.availability.reason


def test_tool_kind_reports_a_real_adapter_via_the_adapter_resolver():
    entries = asyncio.run(discover_providers())
    by_kind = {e.kind: e for e in entries}
    entry = by_kind["tool"]
    assert entry.provider_name == "McpToolAdapter"
    # availability itself depends on whether the real `mcp` client
    # package is importable in this environment -- checked for real by
    # McpToolAdapter.discover(), not assumed either way here.
    assert entry.availability.reason


def test_unknown_kind_is_rejected_not_silently_ignored():
    with pytest.raises(ValueError):
        asyncio.run(discover_providers(kind="not-a-real-kind"))


def test_get_provider_returns_none_for_unregistered_kinds():
    assert get_provider("slm") is None
    assert get_provider("tool") is None
    assert get_provider("human") is None
    assert get_provider("frontier") is not None
    assert get_provider("deterministic") is not None


# ---------------------------------------------------------------------
# DeterministicProvider: discover/inspect are real and honest
# ---------------------------------------------------------------------


def test_deterministic_provider_discover_reports_available():
    provider = DeterministicProvider()
    availability = asyncio.run(provider.discover({}))
    assert isinstance(availability, ProviderAvailability)
    assert availability.available is True
    assert "SubprocessSandboxExecutor" in availability.reason


def test_deterministic_provider_inspect_names_real_requirements():
    provider = DeterministicProvider()
    info = asyncio.run(provider.inspect({}))
    assert info.kind == "deterministic"
    assert "code" in info.requirements


# ---------------------------------------------------------------------
# DeterministicProvider.execute(): a REAL sandboxed script actually runs
# ---------------------------------------------------------------------


def test_deterministic_provider_execute_runs_a_real_sandboxed_script():
    provider = DeterministicProvider()
    node = _node(goal="write a file")
    context = {"code": "open('output.txt', 'w').write('hello from deterministic provider')"}
    result = asyncio.run(provider.execute(node, context))
    assert isinstance(result, NodeResult)
    assert result.status == "success"
    assert result.data["exit_code"] == 0
    assert result.data["output_files"] == {"output.txt": b"hello from deterministic provider"}


def test_deterministic_provider_execute_reports_real_failure():
    provider = DeterministicProvider()
    node = _node(goal="raise an error")
    context = {"code": "raise ValueError('deliberate failure')"}
    result = asyncio.run(provider.execute(node, context))
    assert result.status == "failure"
    assert result.data["exit_code"] != 0
    assert "ValueError" in result.data["stderr"]


def test_deterministic_provider_execute_without_code_fails_honestly():
    provider = DeterministicProvider()
    node = _node()
    result = asyncio.run(provider.execute(node, {}))
    assert result.status == "failure"
    assert "context['code']" in result.notes


# ---------------------------------------------------------------------
# FrontierProvider: discover/inspect are real and honest
# ---------------------------------------------------------------------


def test_frontier_provider_discover_with_injected_executor_and_no_api_key(monkeypatch):
    async def fake_executor(node, **kwargs):
        return NodeResult(status="success", notes="fake")

    monkeypatch.delenv("GENERAL_COMPUTE_API_KEY", raising=False)
    provider = FrontierProvider(run_frontier_node=fake_executor)
    availability = asyncio.run(provider.discover({}))
    assert availability.available is False
    assert "GENERAL_COMPUTE_API_KEY" in availability.reason


def test_frontier_provider_discover_with_injected_executor_and_api_key(monkeypatch):
    async def fake_executor(node, **kwargs):
        return NodeResult(status="success", notes="fake")

    monkeypatch.setenv("GENERAL_COMPUTE_API_KEY", "test-key-not-real")
    provider = FrontierProvider(run_frontier_node=fake_executor)
    availability = asyncio.run(provider.discover({}))
    assert availability.available is True


def test_frontier_provider_inspect_names_real_requirements():
    provider = FrontierProvider(run_frontier_node=lambda node, **kw: None)
    info = asyncio.run(provider.inspect({}))
    assert info.kind == "frontier"
    assert "repo_path" in info.requirements
    assert "task_description" in info.requirements


# ---------------------------------------------------------------------
# FrontierProvider.execute(): identical outcome shape to calling the
# real _run_local_node directly -- proof the abstraction doesn't
# regress the existing behavior. Uses _run_local_node's own real
# early-refusal path (nonexistent repo_path) so this needs no network,
# no LLM, and no OpenAI credentials, while still exercising the REAL
# function, not a stand-in.
# ---------------------------------------------------------------------


def test_frontier_provider_execute_matches_direct_run_local_node_call():
    from app.local_agent.runner import _run_local_node

    node = _node(order=3, goal="a step that needs a real repo")
    task_description = "some task"
    repo_path = "C:/definitely/not/a/real/directory/xyz123"

    direct_notes: list[str] = []
    direct_result = asyncio.run(
        _run_local_node(
            node, task_description=task_description, repo_path=repo_path,
            model="irrelevant-model", max_steps=1, node_notes=direct_notes,
        )
    )

    provider_notes: list[str] = []
    provider = FrontierProvider(run_frontier_node=_run_local_node)
    context = {
        "task_description": task_description, "repo_path": repo_path,
        "model": "irrelevant-model", "max_steps": 1, "node_notes": provider_notes,
    }
    provider_result = asyncio.run(provider.execute(node, context))

    assert provider_result.status == direct_result.status == "failure"
    assert provider_result.notes == direct_result.notes
    assert provider_notes == direct_notes


def test_frontier_provider_execute_with_default_executor_resolves_real_run_local_node():
    """No injected callable at all -- FrontierProvider must lazily resolve
    the SAME real app.local_agent.runner._run_local_node function used
    above, proving the default path (not just the DI seam) is real."""
    from app.local_agent import runner as runner_module

    provider = FrontierProvider()
    node = _node(order=0, goal="default executor path")
    context = {
        "task_description": "t", "repo_path": "C:/also/not/real/abc999",
        "model": "m", "max_steps": 1, "node_notes": [],
    }
    result = asyncio.run(provider.execute(node, context))
    assert result.status == "failure"
    assert "REFUSED" in result.notes
    # Confirm the resolved executor really is the module's real function,
    # not a coincidentally-matching fake.
    assert provider._executor() is runner_module._run_local_node
