# Backend Master Build — Architecture Audit (delta)

Written 2026-09-01, against `main` @ `833c247`. This is a **delta** audit for
the BACKEND MASTER BUILD directive, not a re-derivation of Phase 0's work.
`.scratch/final_architecture_audit.md` (written 2026-08-31, CONSOLIDATED
directive Phase 0) already covers the procedure/task/execution/trace/
evidence/retrieval/local-global/provenance substrate in full and remains
accurate for everything it describes — **read that file first**. Since it
was written, the following sections of it are now STALE (resolved by work
this same session, all committed to `main`):

- §5 "claim graph, real but coarser" → **superseded**. Real work landed:
  11 relation types (`GENERAL_RELATIONS` in `app/services/claims.py`), the
  `edge_type='SUPERSEDES'`-always bug fixed, a 7-state computed lifecycle
  (`get_claim_lifecycle_state`), a real version chain (`supersede_claim`/
  `get_claim_version_chain`, `claim_family_id`/`claim_version` in
  `properties`), commit-linked temporal reasoning (`claim_temporal.py`).
- §5/§9 "no claim→procedure link, no provenance at author time" →
  **superseded**. `precondition_with_claim`, auto-populated `claim_id` in
  `derive_preconditions()`, `applicability.py`'s hard-constraint cascade
  checking a specific claim, and `relate_claims()` → `claim_impact.
  propagate_claim_change()` → `mark_procedure_stale()` wired end-to-end
  (proven live, `test_claim_relate_impact_wiring_e2e.py`).
- §6 "no per-claim evidence reader" → **superseded**.
  `app/services/claim_evidence.py`, wired into `claim_traversal.py::explain()`.
- "Context-compiler existence — UNVERIFIED" → **resolved, exists**:
  `app/services/context_compiler.py`, pure, priority-ordered, budget-bounded.
  **Confirmed still true**: not wired into any real call site.
- Ideal-V1 P1 wave (separately) landed procedure composition
  (`app/execution/procedure_graph.py`), transfer-tier reproduction
  (`mcp_server/server.py::reproduce_procedure`), personal-learning-loop
  richness, and the Phase-2 SKILL.md ingestion compiler
  (`app/services/skill_ingestion.py`, `ingestion_sources/`,
  `procedures.py::supersede_procedure`, migration 32).

Everything else in the 2026-08-31 audit (procedure substrate, task_nodes
vs. PlanNode split, execution DAG, trace pipeline, retrieval split,
local/global, capability scoring already wired, failure-routing
half-wired) is **still accurate as written** — not re-verified line by
line in this pass, only spot-checked (capability.py, applicability.py
still present and unchanged in shape).

This document's own job is the genuinely NEW surface the BACKEND MASTER
BUILD directive asks for and the earlier directive did not: a **REST API
domain layer** for future frontend surfaces (§32-§47), plus the identity/
authorization boundary those APIs need.

---

## 1. REST API — what exists today

`app/api/`: `admin.py`, `agent_store.py`, `agents.py`, `approval.py`,
`chat.py`, `decompose.py`, `graph.py`, `ingest.py`. Wired in `main.py` via
`app.include_router(...)`, all under real prefixes (`/v1/...`).

`graph.py` (`/v1/graph/{node_id}`) is the one existing endpoint that
resembles what this directive asks for: a bounded subgraph read
(`GraphStore.traverse_from`), scope-filtered via `scope_predicates()`,
rows a viewer can't see silently omitted (not labelled "?" — a real,
deliberate anti-enumeration choice worth keeping as the house style for
every new endpoint this wave adds).

**Confirmed absent** (grepped, not guessed): no `/v1/procedures/{id}`, no
`/v1/tasks/{id}`, no `/v1/claims/*`, no `/v1/search`, no `/v1/repositories/
{id}`, no `/v1/projects/{id}`, no `/v1/me`, no `/v1/solutions/*`, no
`/v1/problems*`. `find_best_way`, `search_global`, `get_repository_
knowledge` do not exist under those names or any recognizable equivalent
— the MCP server (`mcp_server/server.py`) has a `plan_only`-mode
`find_best_way` (§0 map above, "plan_only mode" note in the prior audit)
that compiles a plan; it is not this directive's read-oriented "best known
way to do X" search contract, and does not need to be confused with it.

## 2. Request-scope primitives to reuse (do not reinvent)

- `app/api/deps.py::get_scope` — resolves an `AccessScope` per-request:
  a validated OIDC actor (Band 2.9 `authn.current_actor()`) wins; falls
  back to the trusted-only-because-nothing-is-private-yet `X-Viewer-Id`
  header; anonymous otherwise. **Every new read endpoint takes
  `scope: AccessScope = Depends(get_scope)`.**
- `app/services/access.py::scope_predicates(scope, tenant, param_index)`
  — the ONLY legal source of tenant/visibility SQL (CLAUDE.md hard rule).
  `TenantScope.unrestricted()` renders literal `TRUE` — today's honest
  permissive-in-effect posture, per `graph.py`'s own precedent.
  `require_trustworthy_identity()` is the boot guard that refuses to run
  private-visibility mode on header-only identity — new endpoints do not
  need to touch this, it already fires at startup.
- `enforce_limits` / `make_cost_recorder` (`deps.py`) — only for
  endpoints that spend LLM money. Pure reads (everything this wave is
  scoped to build) do not need either.
- `app/db/graph_store.py::GraphStore` — real bounded traversal
  (`traverse_from`, `node_exists`), already tenant/scope-aware. Reuse for
  any new "neighbors"/"graph" endpoint rather than hand-rolling a new
  traversal.

## 3. What's genuinely new schema vs. pure composition

**Pure composition, zero new schema** (compose existing services, wrap in
a new router, add scope filtering): claim graph API (§40 of directive —
`get_claim`, `get_claim_neighbors` [reuse `claim_traversal.research`],
`get_claim_evidence` [reuse `claim_evidence.get_claim_evidence`],
`get_claim_dependents` [reuse `claim_impact.find_procedures_referencing_
claim`]); procedure graph API (§41 — `get_procedure`, `get_procedure_
versions` [reuse existing version-chain walk], `get_procedure_evidence`);
repository/project knowledge API (§39, §18 — new, but composes
`find_applicable_procedures` + claim reads filtered by `scope_entity_id`,
no new table); search/recommend API (§37-38 — composes `retrieval.py` /
`unified_retrieval.py` + `applicability.py` + capability scoring, no new
table).

**Genuinely new schema, deliberately deferred out of this wave** (per the
directive's own Rule 4 phase-gate discipline — flagged, not silently
skipped): §35-36 "Problems" (an active-problems marketplace entity) has no
existing analogue anywhere in the schema and needs a real migration
(title/description/task-or-procedure relation/status/evidence/proposer) —
this is the one piece of this directive that is actual net-new
architecture, not composition, and the directive's own Rule 2 ("do not
create competing abstractions... only replace when you can demonstrate
the existing one fundamentally prevents the intended behavior") argues for
scoping it as its own small, reviewed migration rather than folding it
into the read-API wave. §32.5 "Solutions" the directive explicitly says
NOT to build as a disconnected new table — modeled instead as a read
composition over existing `procedures` + `execution/implementations.py`
+ `evidence` rows, so it belongs with the pure-composition group above,
not the new-schema group.

## 4. Plan for this wave

Given the above, Wave 1 is scoped to the **pure composition, zero new
schema** read-API surface — the highest-value, lowest-risk slice, fully
reusing `get_scope`/`scope_predicates`/existing services, no migration,
independently parallelizable by file:

- Claim Graph API (service + `/v1/claims/*` router)
- Procedure Graph API + `/v1/procedures/{id}` (service + router)
- Repository/Project Knowledge API (`/v1/repositories/{id}`, `/v1/projects/{id}`)
- Search + `find_best_way` recommendation API (`/v1/search`, `/v1/recommend`)
- Task API (`/v1/tasks/{id}`) + `/v1/me` (personal contributions)

Deferred to a later wave, explicitly: Problems/Solutions marketplace
schema (§35-36, needs a migration decision), write-side Contribution API
(§42, needs an approval-flow design decision), SLM/WASM (§27-28, separate
directive already scoped these as net-new), capability-object unification
(§25, verification_stats vs. evidence table still not unified per the
prior audit's §1 finding).
