# Provider Adapter Contract

Written 2026-09-01. Describes the real `ImplementationProvider` contract
(`app/execution/providers.py`, built the previous wave, unchanged this
wave) and how a new adapter is added correctly.

## The contract

```python
class ImplementationProvider(ABC):
    kind: str  # one of implementations.IMPLEMENTATION_KINDS (or the
               # registry's wider REGISTRABLE_KINDS if only storable,
               # not yet runnable)

    async def discover(self, requirements: dict) -> ProviderAvailability:
        """Can this provider actually run HERE, RIGHT NOW -- real
        credentials/network/runtime checked, never assumed."""

    async def inspect(self, task_requirements: dict) -> ProviderCapabilityInfo:
        """What can it do, what does it need from a caller's context."""

    async def execute(self, node: PlanNode, context: dict) -> NodeResult:
        """Real dispatch. Base class default: NotImplementedError --
        the honest default, not a bug, for any kind with no real
        registered class."""
```

`NodeResult` is reused verbatim from `graph_executor.py` — the exact
type every real `run_node` closure in this codebase already returns.
This is deliberate: a provider is a drop-in alternative to those
closures' own inline execution, not a parallel result shape.

## The house rule every adapter must obey

**Only actual implementations should be marked runnable.** Concretely:

1. `discover()` must perform a REAL check (import succeeds, an env var
   with a real name is actually set, a real client can actually be
   constructed) — never `return ProviderAvailability(True, "...")`
   unconditionally.
2. `execute()` must call a REAL, already-existing execution mechanism in
   this codebase (or a genuinely new one you built and tested end-to-end
   in the same change) — never a stub that returns a plausible-looking
   fake result.
3. A kind with no real backing mechanism gets **no provider class at
   all** — list it in `discover_providers()`'s honest-unavailable
   reasons instead (see `providers.py::_UNAVAILABLE_KIND_REASONS`).
   Do not write a class whose `execute()` raises `NotImplementedError`
   "for now" as a placeholder for a future adapter — that's exactly the
   ambiguity `resolve_implementation()`'s own `supported=False` posture
   exists to avoid at the kind-registry layer, and duplicating it at the
   provider layer with a half-built class adds a second, softer place
   for "runnable" to accidentally mean "sort of."

## Adapters that exist today

| Adapter | kind | Wraps | Verified how |
|---|---|---|---|
| `FrontierProvider` | frontier | `app.local_agent.runner._run_local_node` | Real early-refusal-path comparison test (identical `NodeResult` via direct call vs. through the provider) |
| `DeterministicProvider` | deterministic | `app.services.sandbox_executor.SubprocessSandboxExecutor` | Real sandboxed script execution, real result |

## Adapters that do NOT exist, and the bar for adding one

`MCPProvider`, `GraphifyProvider`, `MonidProvider`, `ComposioProvider`,
`WASMProvider`, `SLMProvider`, `ComputerUseProvider` — none built. Each
was considered and explicitly deferred, both the previous wave and this
one, for the same reason: this environment has no real, verifiable
credentials or running servers for any of them, and building one that
cannot be proven to actually work end-to-end would violate the house
rule above by construction — a `discover()` that always reports
available, or an `execute()` that has never actually been run against
the real service it claims to call, is exactly the fabricated
runnability this repo's conventions forbid.

**The bar for adding one for real**: a real, working credential/server
this environment (or a CI environment with real secrets) can reach, plus
at least one test that calls `.execute()` and gets back a real result
from the real external system — not a mock standing in for "we'll wire
this up later." Until that bar is met, the honest state is
`discover_providers()` reporting the kind as `available=False` with a
named reason, which is exactly what `providers.py` already does for
`tool`/`slm`/`human`.

## Adding a real one, when the bar is met

1. Subclass `ImplementationProvider`, set `kind` to the correct member of
   `implementations.IMPLEMENTATION_KINDS` (extend that tuple first, via
   its own module, if the kind is genuinely new — never invent a kind
   string outside the closed vocabulary).
2. Implement `discover()`/`inspect()`/`execute()` for real, per the house
   rule above.
3. Register it in `providers.PROVIDER_REGISTRY[kind] = YourProvider()`.
4. Remove the kind's entry from `_UNAVAILABLE_KIND_REASONS` if it was
   there.
5. Write the real, end-to-end-verified test first (this repo's test-
   first discipline, CLAUDE.md).
6. If the provider realizes a `tool`-kind implementation with a durable
   identity (e.g. "Graphify's `query_graph` tool specifically," not "the
   tool kind in general"), register that identity via
   `implementation_registry.register()` (this wave's registry) so it
   becomes addressable by id, separate from the provider CLASS
   registration in `PROVIDER_REGISTRY` (which is keyed by kind, one class
   per kind — see `.scratch/implementation_registry_architecture.md`'s
   "resolution vs. execution" section for why these two registries are
   deliberately different granularities).
