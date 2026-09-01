# Implementation Registry — Phase 0 Audit

Written 2026-09-01, against `main` @ `ed5502e` (post Solution Search
wave). Real inspection, not derived from prior conversation summaries.

## 1. What `implementation_id` means today (before this pass)

Two real, typed, unrelated `implementation_id` fields already existed,
both with **zero real writers** (confirmed by grep before this pass):

- `PlanNode.implementation_id: Optional[str] = None`
  (`app/models/plan.py:77`) — a per-node field on a COMPILED plan.
  Nothing in `plans.py::compile_plan` ever sets it; the field the
  registry (`app/execution/implementations.py::resolve_implementation`)
  and `providers.py` actually key off is the SIBLING field
  `implementation_hint: Optional[ImplementationHint]` (a preference
  tuple of KINDS, not a concrete identity).
- `Execution.implementation_id: Optional[UUID] = None`
  (`app/models/plan.py:231`, backed by a real column since
  `db/23_plan_persistence.sql`) — meant to record which concrete
  implementation an execution actually used. Confirmed by grep: no
  real writer anywhere sets this column on insert
  (`plan_persistence.py::record_plan_execution` accepts no such
  parameter).

**Conclusion**: both columns are real, both are forward-compatible
handles exactly like `evidence.source_id`/`content_ref` were before
their own writers existed (migration 24's own documented pattern) —
placeholders for an object this migration adds, not evidence of a
half-built registry.

## 2. Where implementations are created / persisted / resolved / executed

- **Created**: nowhere, before this pass. No writer of any kind.
- **Persisted**: nowhere durable. `ProcedureStep.allowed_implementations`
  (a JSONB field on a procedure step, e.g. `[{"type": "tool", "name":
  "Graphify"}]`) is the only PRE-EXISTING place implementation
  preference is stored at all, and it is advisory/free-text, not a
  reference to any row.
- **Resolved**: `app/execution/implementations.py::resolve_implementation`
  — answers "is KIND X runnable at all, by anything, right now", via a
  small in-process dict (`_REGISTERED_STRATEGIES = {"frontier": ...}`).
  This is a KIND-level resolver, never identity-level (it cannot answer
  "which SPECIFIC Graphify-vs-Monid implementation").
- **Executed**: `app/execution/providers.py` (built the previous wave)
  — `FrontierProvider`/`DeterministicProvider`, each wrapping one real
  existing executor. Neither is durable-identity-aware; `PROVIDER_
  REGISTRY` is keyed by KIND, one class per kind, same granularity as
  `implementations.py`.

## 3. Which provider/executor owns each kind, today

| kind | real executor | owned by |
|---|---|---|
| frontier | `app.local_agent.runner._run_local_node` | `providers.FrontierProvider` |
| deterministic | `app.services.sandbox_executor.SubprocessSandboxExecutor` | `providers.DeterministicProvider` |
| tool | none | unregistered — `providers.discover_providers()` reports `available=False`, honest reason |
| slm | none | unregistered, same honest-unavailable posture |
| human | none | unregistered, same |

## 4. How procedure steps advertise implementations, and how plans bind them

`PlanNode.implementation_hint` (a preference-ordered tuple of KINDS,
validated by `implementations.py::validate_implementation_hint`) is the
only real binding mechanism today, and it binds a KIND preference, never
a concrete implementation identity. `resolve_implementation()` is called
at EXECUTION time inside `local_agent/runner.py`'s dispatch (the one real
caller, per the prior wave's audit), not at COMPILE/freeze time — so
today's real behavior does NOT yet satisfy directive Sec 20's
"resolve → bind → persist → execute frozen plan" ordering; resolution
and execution are the same step. This is a real, honest gap this wave's
`implementation_registry.py` does not itself close (that is
`implementation_executor.py`'s job, scoped to the next wave below) — it
only makes a concrete identity resolvable to bind against.

## 5. Required metadata that was missing (closed by this pass)

Confirmed absent before this migration, all now real columns/tables on
`implementations`/`implementation_tasks` (`db/33_implementation_registry.sql`):
durable identity (`id`, unique `(name, provider, version)`), lifecycle
`status` (candidate/active/deprecated/disabled/quarantined), a SEPARATE
`verification_status` axis (unverified/verified — directive Sec 61's
explicit "available-but-unverified must not equal verified" rule),
`locator`/`invocation` (protocol-specific JSONB), `input_schema`/
`output_schema`, `requirements`/`auth_requirements`/
`resource_requirements` (JSONB, auth stores only a `credential_ref`
shape, never a secret — directive Sec 13/50), `source_ref`/`author`/
`license` (first-class columns, `NULL` = honestly unknown, never
guessed), `derived_from` (real self-FK for forking, directive Sec 69),
`content_hash` (directive Sec 60), the multi-task relationship
(`implementation_tasks` link table, directive Sec 18).

## 6. Deliberately NOT built this pass (real gaps, named not hidden)

- `app/execution/implementation_executor.py` — resolve→bind→freeze→
  execute wiring through `providers.py`, directive Sec 20-23. Scoped to
  a parallel workstream this same wave (see build-board note).
- `app/services/capabilities.py` — directive Sec 35-37 explicitly asks
  to REUSE, not duplicate, the existing empirical capability machinery
  (`app/services/procedure_extraction/capability.py`'s Wilson-interval
  `compute_capability`/`wilson_interval`/`band_for_p`) — this needs a
  `target_type='implementation'` extension, which `evidence_target_type_
  chk` (migration 24) already permits at the schema level with zero
  ALTER needed. Scoped to a parallel workstream this same wave.
- REST API (`GET /v1/implementations/{id}`, etc.), MCP tools, Graphify/
  Monid E2E scenarios (the latter explicitly out of scope per the prior
  wave's own "do not build unverifiable adapters" ruling — this
  directive's own Sec 86-87 ask for them, but this environment cannot
  verify a real Graphify/Monid MCP server end-to-end without real
  external credentials, same reasoning as `providers.py`'s own
  documented refusal to build `MCPProvider`/`MonidProvider`).

## 7. Migration applied, verified live

`db/33_implementation_registry.sql` applied cleanly to the local dev
Postgres (`python scripts/migrate.py`), confirmed idempotent (a second
run made no further changes to it), and round-tripped live
(register → get → list → activate → verify → deprecate →
get_for_task/resolve with a real task_node link, visibility filtering
against a private row) — all in `tests/test_implementation_registry_
e2e.py`, 4/4 passed against the real database, not just asserted offline.

**Pre-existing, unrelated finding, flagged not fixed**: `python scripts/
migrate.py --status` reports `32_ingestion_provenance.sql` as `MISMATCH`
(edited after being applied, by an earlier, different session/lane) —
this predates this pass, is not caused by migration 33, and migrate.py
correctly refuses to silently re-run it. Out of this wave's scope to
resolve; flagged here so it isn't mistaken for a new regression.
