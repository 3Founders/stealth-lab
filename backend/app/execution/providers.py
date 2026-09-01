"""Directive Phase 14: a generic `ImplementationProvider` abstraction so a
task can express "I need capability X" without hardcoding which specific
provider/tool satisfies it.

WHERE THIS SITS, relative to what already exists: `implementations.py`
owns a closed KIND vocabulary (`IMPLEMENTATION_KINDS`:
deterministic/tool/slm/frontier/human) plus a registry mapping the
subset of kinds this codebase can actually run today to a real executor
strategy name (`resolve_implementation`). That module deliberately never
asks "run WHICH concrete thing" -- only "is this KIND runnable at all,
by anything, right now". A PROVIDER, here, is one concrete way of
REALIZING a given kind -- e.g. "frontier" could in principle be realized
by more than one provider (a hosted API, a local model server); today it
is realized by exactly one. This module is additive and sits BEHIND
`implementations.py`: it does not touch, replace, or widen
`IMPLEMENTATION_KINDS`, `resolve_implementation`, or
`validate_implementation_hint`. A provider's `kind` attribute is always
one of `implementations.IMPLEMENTATION_KINDS`.

HOUSE RULE this module obeys throughout (directive's own words, and this
repo's established discipline -- see `implementations.py`'s own
docstring making the identical promise): "Only actual implementations
should be marked runnable. Do not claim a runtime exists merely because
it is represented in a type vocabulary." Concretely:

  - `FrontierProvider` wraps the ONE real existing executor this
    codebase has for "frontier" today: `app.local_agent.runner
    ._run_local_node` (a real Agent+RepoSandbox tool-calling turn --
    see that function's own docstring and
    `test_graph_executor_coding_live.py`). It is resolved via a LAZY
    import (inside a method, not at module import time) specifically so
    importing `app.execution.providers` never drags in
    `app.local_agent.runner`'s own real transport dependencies (`mcp`,
    `httpx2`) unless a caller actually asks this provider to execute --
    this module stays a lightweight, standalone primitive, matching the
    "build the real primitive, be honest about what's not wired yet"
    posture `.scratch/current_backend_architecture.md` documents for
    this pass (see report: step 2's live call-site wiring was NOT done,
    for exactly this layering reason among others).
  - `DeterministicProvider` wraps the ONE real existing sandboxed-code
    mechanism this codebase has (`app.services.sandbox_executor
    .SubprocessSandboxExecutor`) -- reused, not reimplemented, per
    CLAUDE.md Rule 2 and this repo's "reuse a real existing mechanism
    instead of building a second one" discipline. Same honesty caveat
    that module's own docstring carries forward here: this is NOT a
    security sandbox for adversarial code (see its docstring), fine for
    genuinely deterministic/trusted scripts.
  - Every OTHER kind (`slm`, `tool`, `human`) has NO provider class
    registered here at all. `discover_providers()` still reports them,
    honestly, as `available=False` with a stated reason -- exactly
    matching `resolve_implementation()`'s own `supported=False` posture
    for an unregistered kind. No `execute()` body exists anywhere in
    this module that pretends to call an MCP server, Composio, a human
    approval surface, or an SLM -- there is no class to hold one.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.execution.graph_executor import NodeResult
from app.execution.implementations import IMPLEMENTATION_KINDS
from app.models.plan import PlanNode
from app.services.sandbox_executor import SandboxExecutor, SubprocessSandboxExecutor

# The real signature `app.local_agent.runner._run_local_node` (and any
# other real frontier executor a caller might inject) satisfies: a node
# plus the same keyword shape `LocalAgentRunner._execute_steps` already
# passes it, returning the SAME `NodeResult` `run_node` closures across
# this codebase already return (graph_executor.py's own contract).
FrontierExecutorFn = Callable[..., Awaitable[NodeResult]]


@dataclass(frozen=True)
class ProviderAvailability:
    """What `discover()` answers: can THIS provider actually run, in
    THIS process, right now -- real credentials/imports/runtime checked,
    never assumed. `reason` is always populated, on both branches, so a
    caller (or a test) never has to guess why."""

    available: bool
    reason: str


@dataclass(frozen=True)
class ProviderCapabilityInfo:
    """What `inspect()` answers: what a provider can do and what it
    needs from a caller's `context` dict to do it -- documentation a
    caller can act on, not just prose."""

    kind: str
    provider_name: str
    description: str
    requirements: dict[str, str] = field(default_factory=dict)


class ImplementationProvider(ABC):
    """One concrete way of realizing a given implementation `kind`.

    Method shapes are deliberately aligned with the one real contract
    every `run_node` closure in this codebase already satisfies
    (`graph_executor.execute_task_graph`'s `run_node: PlanNode ->
    Awaitable[NodeResult]`, used identically by
    `app.mcp_server.server`'s closures and
    `app.local_agent.runner.LocalAgentRunner._execute_steps`): `execute`
    takes the same `PlanNode` and returns the same `NodeResult` type
    those closures already return, plus a `context` dict carrying
    whatever per-run inputs a real executor needs (task_description,
    repo_path, model, ... -- exactly the keyword arguments
    `_run_local_node` already takes). Reusing `NodeResult` here (rather
    than inventing a parallel result type) is deliberate: it's the
    existing, real result shape this codebase's own schedulers already
    consume."""

    kind: str  # one of implementations.IMPLEMENTATION_KINDS

    @abstractmethod
    async def discover(self, requirements: dict) -> ProviderAvailability:
        """Can this provider actually run here, right now -- credentials,
        network, or runtime present? Never fabricated; a provider with no
        real backing returns `available=False` with an honest reason
        rather than omitting itself."""

    @abstractmethod
    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        """What can this provider do, and what does it need from a
        caller's `context` dict to do it."""

    async def execute(self, node: PlanNode, context: dict) -> NodeResult:
        """Real dispatch: run `node`'s work via this provider and return
        the same `NodeResult` shape every `run_node` closure in this
        codebase already returns. The base class's default body refuses
        rather than pretending -- a subclass with no real executor
        (there are none registered in `PROVIDER_REGISTRY` for such a
        kind; see module docstring) simply never overrides this."""
        raise NotImplementedError(
            f"{type(self).__name__} ({self.kind!r}) has no real execute() "
            "implementation -- this is the honest default, not a bug."
        )


def _resolve_default_frontier_executor() -> FrontierExecutorFn:
    """Lazy import of the ONE real existing frontier executor -- see
    module docstring for why this is a function-local import rather than
    a module-level one (keeps `app.execution.providers` importable
    without `app.local_agent.runner`'s own real transport deps unless a
    caller actually needs to execute)."""
    from app.local_agent.runner import _run_local_node

    return _run_local_node


class FrontierProvider(ImplementationProvider):
    """Wraps the real frontier-model execution path this codebase has
    today: `app.local_agent.runner._run_local_node`, a real
    Agent+RepoSandbox tool-calling turn (the same mechanism
    `test_graph_executor_coding_live.py` proves live, and the one
    `implementations.py`'s own registry names `_REGISTERED_STRATEGIES =
    {"frontier": "frontier_model_call"}`).

    Dependency-injectable (`run_frontier_node`) for exactly the same
    reason `graph_executor.execute_task_graph` injects `run_node` and
    `_run_local_node` itself is factored out as a swappable seam in
    runner.py: a test can supply the REAL function (proving identical
    behavior with no network/LLM needed, via `_run_local_node`'s own
    real early-refusal path for a nonexistent `repo_path`) or a fake one
    (proving the dispatch plumbing), without this class ever needing to
    special-case "am I in a test"."""

    kind = "frontier"

    def __init__(self, run_frontier_node: Optional[FrontierExecutorFn] = None):
        self._injected = run_frontier_node

    def _executor(self) -> FrontierExecutorFn:
        return self._injected if self._injected is not None else _resolve_default_frontier_executor()

    async def discover(self, requirements: dict) -> ProviderAvailability:
        try:
            self._executor()
        except Exception as exc:  # noqa: BLE001 -- report, don't crash the caller
            return ProviderAvailability(
                False,
                f"real frontier executor is not importable in this process: {exc}",
            )
        if not os.environ.get("GENERAL_COMPUTE_API_KEY"):
            return ProviderAvailability(
                False,
                "GENERAL_COMPUTE_API_KEY is not set -- the real frontier executor "
                "(_run_local_node) requires it to construct its OpenAI-compatible "
                "client and would fail on the first real call",
            )
        return ProviderAvailability(
            True,
            "real frontier executor is importable and GENERAL_COMPUTE_API_KEY is set",
        )

    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        return ProviderCapabilityInfo(
            kind=self.kind,
            provider_name=type(self).__name__,
            description=(
                "One real Agent+RepoSandbox frontier-model tool-calling turn "
                "against a real repo checkout -- wraps "
                "app.local_agent.runner._run_local_node unchanged."
            ),
            requirements={
                "task_description": "str -- the overall task text",
                "repo_path": "str -- must be a real directory on this machine",
                "model": "str, optional (defaults to the injected executor's own default)",
                "max_steps": "int, optional",
                "node_notes": "list[str], optional -- shared mutable running note log",
            },
        )

    async def execute(self, node: PlanNode, context: dict) -> NodeResult:
        executor = self._executor()
        kwargs: dict[str, Any] = {
            "task_description": context["task_description"],
            "repo_path": context["repo_path"],
            "model": context.get("model", "gemma-4-31B-it"),
            "max_steps": context.get("max_steps", 8),
            "node_notes": context.get("node_notes", []),
        }
        return await executor(node, **kwargs)


class DeterministicProvider(ImplementationProvider):
    """Wraps the real, already-tested sandboxed-execution mechanism
    (`app.services.sandbox_executor.SubprocessSandboxExecutor`) for the
    "deterministic" kind -- a fixed script, no model call. Reused
    verbatim, not reimplemented (CLAUDE.md Rule 2): this class adds no
    sandboxing logic of its own, only the `ImplementationProvider`
    contract around the existing `SandboxExecutor` protocol.

    Injectable `executor` for the same testability reason
    `test_sandbox_executor.py` already exercises both
    `SubprocessSandboxExecutor` and `ContainerSandboxExecutor` against
    one shared `SandboxExecutor` protocol -- this class doesn't care
    which one it's handed, and defaults to the unisolated
    `SubprocessSandboxExecutor` (always constructible, no Docker
    dependency) so `discover()` can be honest about availability without
    needing a Docker daemon present."""

    kind = "deterministic"

    def __init__(self, executor: Optional[SandboxExecutor] = None):
        self._executor: SandboxExecutor = executor or SubprocessSandboxExecutor()

    async def discover(self, requirements: dict) -> ProviderAvailability:
        # SubprocessSandboxExecutor only needs a Python interpreter on
        # this machine, which running this very process already proves
        # exists -- real, not assumed. See its own docstring for the
        # honest isolation caveat this provider carries forward via
        # `inspect()` below rather than re-asserting a stronger
        # guarantee than the underlying executor actually gives.
        return ProviderAvailability(
            True,
            f"{type(self._executor).__name__} is constructed and ready "
            "in this process (no external credentials required)",
        )

    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        return ProviderCapabilityInfo(
            kind=self.kind,
            provider_name=type(self).__name__,
            description=(
                "Runs a fixed script via the real sandbox_executor mechanism -- "
                "no model call. NOT a security sandbox when backed by the default "
                "SubprocessSandboxExecutor (see that class's own docstring): "
                "fine for genuinely deterministic/trusted code, not adversarial "
                "input, unless an isolating executor (e.g. ContainerSandboxExecutor) "
                "is injected instead."
            ),
            requirements={
                "code": "str -- the script to run (required)",
                "input_files": "dict[str, bytes], optional -- staged before the run",
                "timeout_seconds": "float, optional (default 30)",
                "network_access": "bool, optional (default False; see executor's own docstring for enforcement caveats)",
            },
        )

    async def execute(self, node: PlanNode, context: dict) -> NodeResult:
        code = context.get("code")
        if not code:
            note = (
                f"step {node.order} ({node.goal}): DeterministicProvider requires "
                "context['code'] (the script to run); none was given"
            )
            return NodeResult(status="failure", notes=note)

        result = await self._executor.run(
            code,
            input_files=context.get("input_files") or {},
            timeout_seconds=context.get("timeout_seconds", 30.0),
            network_access=context.get("network_access", False),
        )
        succeeded = result.exit_code == 0 and not result.timed_out
        note = (
            f"step {node.order} ({node.goal}): deterministic run "
            f"exit_code={result.exit_code} timed_out={result.timed_out}"
        )
        return NodeResult(
            status="success" if succeeded else "failure",
            notes=note,
            data={
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output_files": result.output_files,
                "wall_time_seconds": result.wall_time_seconds,
                "timed_out": result.timed_out,
            },
        )


# Kinds with NO real executor and deliberately NO provider class -- see
# module docstring. `discover_providers()` reports these honestly rather
# than omitting them, exactly matching `resolve_implementation()`'s own
# `supported=False` posture in implementations.py for an unregistered
# kind. Never a stand-in for a class whose execute() would fabricate a
# call to something this environment cannot verify (MCP, Composio, a
# human approval surface, an SLM endpoint).
_UNAVAILABLE_KIND_REASONS: dict[str, str] = {
    "slm": (
        "no real executor wired up yet -- 'slm' is a validated, storable "
        "kind (implementations.py's IMPLEMENTATION_KINDS) but no small/local "
        "model call path exists anywhere in this codebase today"
    ),
    "tool": (
        "no real executor wired up yet -- realizing 'tool' means a named "
        "external tool/API adapter (e.g. MCP, Composio); this environment "
        "cannot verify one of those end-to-end without real external "
        "credentials, so none is registered here"
    ),
    "human": (
        "no real executor wired up yet -- realizing 'human' means a real "
        "human-in-the-loop approval surface; no execute() path for it "
        "exists in this module"
    ),
}

# Queryable registry: which providers exist, keyed by the KIND they
# realize. Only kinds with a real, tested class appear here -- see
# `_UNAVAILABLE_KIND_REASONS` above for the rest.
PROVIDER_REGISTRY: dict[str, ImplementationProvider] = {
    "frontier": FrontierProvider(),
    "deterministic": DeterministicProvider(),
}


@dataclass(frozen=True)
class ProviderDiscoveryEntry:
    kind: str
    provider_name: Optional[str]  # None when no class is registered at all
    availability: ProviderAvailability


async def discover_providers(
    kind: Optional[str] = None, requirements: Optional[dict] = None,
) -> list[ProviderDiscoveryEntry]:
    """What providers exist for `kind` (or every member of
    `IMPLEMENTATION_KINDS` when `kind` is None), and which are actually
    available to run right now. Never fabricates availability: a kind
    with no registered provider class reports `available=False` with an
    honest, specific reason rather than being silently omitted."""
    requirements = requirements or {}
    kinds: tuple[str, ...]
    if kind is None:
        kinds = IMPLEMENTATION_KINDS
    else:
        if kind not in IMPLEMENTATION_KINDS:
            raise ValueError(
                f"unknown implementation kind {kind!r} (valid: {IMPLEMENTATION_KINDS})"
            )
        kinds = (kind,)

    entries: list[ProviderDiscoveryEntry] = []
    for k in kinds:
        provider = PROVIDER_REGISTRY.get(k)
        if provider is not None:
            availability = await provider.discover(requirements)
            entries.append(
                ProviderDiscoveryEntry(
                    kind=k, provider_name=type(provider).__name__, availability=availability,
                )
            )
        else:
            reason = _UNAVAILABLE_KIND_REASONS.get(k, "no real executor wired up yet")
            entries.append(
                ProviderDiscoveryEntry(
                    kind=k, provider_name=None, availability=ProviderAvailability(False, reason),
                )
            )
    return entries


def get_provider(kind: str) -> Optional[ImplementationProvider]:
    """The one real provider class registered for `kind`, or None when
    none is (see `PROVIDER_REGISTRY`/`_UNAVAILABLE_KIND_REASONS`)."""
    return PROVIDER_REGISTRY.get(kind)
