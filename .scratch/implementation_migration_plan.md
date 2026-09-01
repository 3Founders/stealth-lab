# Implementation Migration Plan

Written 2026-09-01. How the codebase moves from today's KIND-level
`implementation_hint` preference to real, durable implementation
identities — without breaking anything already working (directive Sec 27-28).

## Today's real state (post this wave)

```
ProcedureStep.allowed_implementations  (JSONB, advisory, e.g.
    [{"type": "tool", "name": "Graphify"}])
              │
              │  read at compile time into...
              ▼
PlanNode.implementation_hint  (validated tuple of KINDS, e.g. ("tool",))
              │
              │  resolved at EXECUTION time (not compile time) by...
              ▼
implementations.resolve_implementation(hint)
              │
              ▼
providers.PROVIDER_REGISTRY[kind].execute(node, context)
```

`PlanNode.implementation_id` and `Execution.implementation_id` (both
real columns) stay `None` throughout this whole path today — nothing
sets them. This is the exact gap this wave's `implementations` table +
`implementation_registry.py` close at the STORAGE layer; wiring
resolution into this path at COMPILE time (so `implementation_id` is
actually frozen before execution, per directive Sec 20-21) is
`implementation_executor.py`'s job, built in parallel this same wave —
see that module's own report for whether it reached the real
`compile_plan()` call site or stayed a tested, standalone primitive.

## The compatibility layer (directive Sec 27)

`allowed_implementations`' legacy shape (`{"type": ..., "name": ...}`)
is NOT rewritten by this migration, and the existing corpus of already-
captured procedures is NOT touched (fresh-start rule: no backfills).
Going forward, a NEW procedure step MAY additionally carry a durable
`implementation_id` reference once one has been registered for its task
via `implementation_registry.register()` + `implementation_tasks`; an
OLD step with only a legacy hint continues to resolve via the pre-
existing KIND-level path exactly as it does today. No procedure needs to
be rewritten for the system to keep working.

## Rollout, in real, gated steps (not all landed this wave)

1. **Schema + registry primitives exist.** (This wave, landed.)
   `implementations`/`implementation_tasks`, `implementation_registry.py`.
   Zero behavior change to any existing execution path — nothing calls
   this module yet from a real hot path.
2. **Executor wiring exists as a tested, callable primitive.** (This
   wave, parallel agent — see its own report for exact scope.)
   `implementation_executor.py::resolve_implementation_for_node`/
   `bind_implementation`/`execute_implementation`, proven to fall back
   to today's real frontier-default behavior when nothing durable is
   registered (the overwhelmingly common case immediately after this
   wave, since almost nothing has been registered yet).
3. **Compile-time binding wired into `plans.py::compile_plan`'s real
   hot path.** NOT confirmed done this wave — depends on the parallel
   agent's own risk assessment (see its report). If not done, this is
   the next real, small, reviewable change: call `resolve_implementation_
   for_node` once per node during compilation, `bind_implementation` the
   result, persist as today. Backward-compatible by construction (a node
   with no durable implementation resolves to `implementation_id=None`,
   identical to today).
4. **Real durable implementations get registered** for a handful of
   genuinely available cases (deterministic scripts via
   `DeterministicProvider`, the one real frontier path via
   `FrontierProvider`) — a real, small, manually-curated seed, not a
   bulk migration of anything (there is nothing to migrate FROM: no
   prior implementation identities existed).
5. **Capability data accumulates** as those registered implementations
   actually run (`capabilities.py::record_implementation_outcome`, the
   parallel agent's own deliverable this wave).
6. **A real router** (directive Sec 38, NOT built this wave) starts
   choosing AMONG multiple resolved candidates by capability/cost/
   latency — this needs step 5's real evidence to exist first; building
   it before any evidence accumulates would mean routing on nothing.

## What this migration explicitly does NOT do

- Does not require rewriting any existing procedure's
  `allowed_implementations` field.
- Does not change `resolve_implementation()`'s (implementations.py)
  existing behavior or signature.
- Does not remove or reinterpret `implementation_hint`.
- Does not silently upgrade an already-frozen plan's implementation
  binding when a newer version of that implementation is later
  registered (directive Sec 42/91 — replay must resolve to the SAME
  identity, proven live this wave in
  `test_implementation_registry_e2e.py`'s own version-pinning logic at
  the registry layer; the executor-level replay proof is the parallel
  agent's own deliverable).
