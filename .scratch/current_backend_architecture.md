# Current Backend Architecture — audit delta for MASTER BACKEND IMPLEMENTATION directive

Written 2026-09-01, against `main` @ (post decomposition-unbounded commit).
This is a delta on top of two prior audits already in this directory —
read those first, this document only adds what changed or was missed:

- `.scratch/final_architecture_audit.md` (2026-08-31) — full substrate map
  (procedures, tasks, execution, trace, claims-as-of-that-date, retrieval,
  local/global, provenance, capability).
- `.scratch/backend_architecture_audit.md` (2026-09-01) — REST API layer
  audit + what Wave 1 built (claim/procedure/repository/project/task/me/
  search domain services and routers).

## What this directive asks for that is ALREADY BUILT (grepped and
confirmed, not assumed) — do not rebuild these under new names

- **Phase 2 claim graph** (relations, lifecycle, scope, traversal): done.
  `claims.py` (11 relations via `custom_edge_type`, 7-state computed
  lifecycle, `GLOBAL`/`PROJECT`/`REPOSITORY` via `scope_type`/
  `scope_entity_id`), `claim_traversal.py` (EXPLAIN/RESEARCH/DECIDE),
  `claim_temporal.py` (version-chain + commit history).
- **Phase 3 repository/project knowledge**: done.
  `app/services/repository_knowledge.py::get_repository_knowledge`/
  `get_project_knowledge`, `/v1/repositories/{id}`, `/v1/projects/{id}`.
- **Phase 5 claim→procedure/task propagation**: done, under the name
  `app/services/claim_impact.py`, not `claim_propagation.py` — the
  directive names `claim_propagation.py` with functions
  `find_claim_dependents`/`classify_claim_impact`/`propagate_claim_change`/
  `mark_affected_objects`/`invalidate_retrieval_state`.
  `claim_impact.py` already provides the equivalent real, wired,
  end-to-end-tested mechanism: `find_procedures_referencing_claim()` +
  `propagate_claim_change()`, called from `claims.py::relate_claims()`
  and ending in a real `mark_procedure_stale()` call (proven live,
  `test_claim_relate_impact_wiring_e2e.py`). **Do not create a second,
  parallel `claim_propagation.py`** — CLAUDE.md Rule 2 forbids this.
  If the directive's exact function names matter for a future caller,
  the honest move is a thin re-export/alias in a new
  `claim_propagation.py`, not a reimplementation — flagged here as a
  future micro-task, not done in this pass (no real caller has asked
  for the alternate names yet).
- **Phase 6 embedding projections**: done.
  `app/services/semantic_projections.py` — `claim_embedding_text()`,
  `task_embedding_text()`, `procedure_embedding_text()`, `content_hash()`,
  `needs_reembedding()`. Not yet wired into a real writer (documented
  as deliberate in that module already) — the projection FUNCTIONS exist
  and are tested; the async-refresh QUEUE consuming them does not yet.
- **Phase 17 procedure/task/solution contracts**: done (Wave 1).
  `/v1/procedures/{id}`, `/v1/tasks/{id}`, `/v1/solutions/{id}`,
  `/v1/claims/*`, `/v1/repositories/{id}`, `/v1/projects/{id}`, `/v1/me`.
- **Phase 20 REST+MCP sharing domain services**: partially true by
  construction — the new Wave 1 services (`claim_graph_api.py`,
  `procedure_graph_api.py`, etc.) are plain async functions with no
  REST-specific code, so an MCP tool CAN call them directly. No MCP tool
  actually does yet (not built this pass, real gap, see below).

## What this directive asks for that does NOT exist yet — real gaps

- **Phase 1 unbounded decomposition** — real gap, FIXED THIS PASS. See
  `.scratch/decomposition_unbounded_design.md` for the full change record.
- **Phase 7 solution search** (`search_solutions()`, cross-type ranked
  results, direct-task-can-win) — `domain_search.py::search_global()`
  (Wave 1) deliberately does NOT cross-rank (grouped by object_type, each
  ranked by its own mechanism, per CLAUDE.md's RRF/applicability
  separation rule). This directive explicitly wants a blended ranked
  list where a task CAN outrank a procedure. Genuine gap, scoped for
  this wave's parallel work.
- **Phase 14 implementation providers** (`ImplementationProvider`
  discover/inspect/execute abstraction, MCP/Monid/Composio/REST/WASM
  adapters) — confirmed absent (grepped, zero matches for
  `ImplementationProvider`/`MCPProvider`/`ComposioProvider`/
  `MonidProvider` anywhere in `app/`). `execution/implementations.py`
  is a real but much narrower KIND vocabulary + registry (5 kinds
  named, only `frontier` has a real executor) — this is the axis a
  provider abstraction would sit BEHIND, not a replacement for it.
  Genuine gap, scoped for this wave's parallel work.
- **Phase 15/16 SLM/WASM real execution** — confirmed absent, unchanged
  from the prior audit. Not attempted this wave (large, needs its own
  gated phase per the directive's own §78 completion-gate rule).
- **Phase 18 UGC/contribution system** — confirmed absent, unchanged.
- **Phase 19 Problems marketplace** — confirmed absent, unchanged; needs
  a real migration, deliberately deferred (directive's own §17/§52 say
  not to fabricate this through `procedures.id`).
- **Phase 22 hosted Postgres migration** — `.scratch/postgres_
  portability.md` (audit only) exists; no real hosted instance has ever
  been provisioned or tested against (confirmed: local `DATABASE_URL`
  only, `postgresql://...@localhost:5432/...`). Needs real credentials
  a human must provision — not agent-executable.

## This wave's scope (given the above)

Two genuinely new, independently-parallelizable, real gaps, both scoped
conservatively rather than attempting the full 23-phase directive at
once (per its own §76/§78 phase-gate discipline):

1. **Solution search cross-ranking** (Phase 7) — a product-facing
   blended search over the existing, unmodified `domain_search.py` legs.
2. **Implementation provider abstraction** (Phase 14) — the
   `discover`/`inspect`/`execute` ABC + a `FrontierProvider` (wraps the
   one real existing executor, proving the abstraction doesn't regress
   current behavior) + one additional, independently-verifiable adapter.
   Deliberately NOT building MCPProvider/MonidProvider/ComposioProvider
   in this pass — those need real external credentials/servers this
   environment cannot verify, and the directive's own house rule
   ("only actual implementations should be marked runnable... do not
   claim a runtime exists merely because it is represented") argues
   against building unverifiable adapters.

Everything else named in the directive (claim graph deepening beyond
what exists, SLM, WASM, UGC, Problems, hosted Postgres, full evaluation
suite) is real, acknowledged, NOT done, and deliberately not attempted
in this wave.
