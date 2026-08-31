# Phase 0 — Final Architecture Audit

Written 2026-08-31, against `main` @ `93ff532` (post Ideal-V1 P1 wave). Real
inspection of the current repository — schema files, service modules, and
running tests — not derived from prior conversation summaries. Every claim
below is either a direct file read (path:line noted) or a live test run
this session already produced. Where I could not verify something within
this pass's budget, it is marked **UNVERIFIED** rather than asserted.

This document exists to gate CONSOLIDATED-DIRECTIVE Phase 1 onward. No
architectural change should start until this is read.

---

## 1. Procedure substrate

**Schema**: `db/18_procedures.sql`, extended by `19_procedures_embedding.sql`
(embedding + `embedding_model_id`/`embedding_dim`), `20_procedure_extraction.sql`
(`extractor_version`, `capability_statement`), `21_band1_contracts.sql`
(`scope_type`/`scope_entity_id`, uniform across 7 tables), `22_band1_review_fixes.sql`
(CHECK constraints against `v0_gate.SCOPE_TYPES`), `23_plan_persistence.sql`
(no procedure columns, but the plan/graph tables reference `procedures.id`),
`24_evidence.sql` (procedure verification now derivable from `evidence` rows,
not just the JSONB counter — see §6), `30_verified_requires_evidence.sql`.

One table, `procedures`, versioned by **invalidate-and-append**
(`t_valid`/`t_invalid`/`t_created`/`t_expired`, `version INTEGER`,
`procedure_id` stable across the version chain, `family_id` self-FK). Three
**orthogonal** lifecycle axes, each a real Postgres ENUM, deliberately not
collapsed into one status column:

- `verification_state`: `candidate → verified → retired`
- `staleness`: `fresh → stale → revalidating`
- `availability`: `active → quarantined → disabled`

`verification_stats` is one JSONB counter blob (attempts/successes/
distinct_contexts/context_keys_seen/circuit-breaker counters/utility
totals) — this is the ORIGINAL (ticket-05-era) mechanism and is still what
`record_execution_outcome` writes to and what promotion/quarantine/
retirement read. Migration 24 added a real `evidence` table (§6) that CAN
answer richer questions (independent-supporting-count, evidence kind,
failure_class) but **the two are not yet unified** — `verification_stats`
is still the sole input to lifecycle transitions in `procedures.py`; the
`evidence` table today is written by `outcome_to_evidence()` alongside it,
not read back into the promotion decision. This is a real, precise
duplication the directive's Phase 6/25 (capability-as-evidence) work should
resolve, not invent a third counter.

**Writers**: `app/services/procedures.py` — `capture_procedure` (candidate,
V0-gated on provenance+scope), `supersede_procedure` (new version, carries
forward everything not in `changed_fields`), `record_execution_outcome`
(the single source of truth for every ticket-13 transition — promotion,
5-failure circuit breaker, 14-day quarantine auto-disable, Minton utility
retirement — under one row-locked transaction), `approve_procedure`/
`reject_procedure` (a **separate** gate from verification — confirmed live
this session: `verification_state='verified'` alone does not make a
procedure retrievable; `approval_status='approved'` is a second,
independent hard constraint in `applicability.py`), `mark_procedure_stale`,
`merge_duplicate_procedures` / `run_procedure_dedup_sweep`.

**Readers**: `get_procedure` (by version-row id), `find_applicable_procedures`
(§7), `compute_utility`.

**Composition**: `app/execution/procedure_graph.py` — a step's JSONB may
carry `subprocedure_ref: {procedure_id, version}`, recursively spliced at
compile time (`expand_procedure_steps`) with real cycle detection and a
depth cap (default 8), version-pinned (never resolves to a newer version
silently). A step may also carry `implementation_hint` (§27 below).

**Real, confirmed gap for CONSOLIDATED §13/§23**: no writer derives a
procedure's `preconditions` from the claims graph, and no extractor queries
`knowledge_nodes` at all. `preconditions` today are derived purely from an
episode's own `state_before` projection (`applicability.py`'s docstring,
`derive.py`). This is the exact joint CONSOLIDATED Phase 3 names as
"critical missing" — confirmed, not assumed.

---

## 2. Task substrate

**Schema**: `task_nodes` in `db/01_ontology.sql` (the ORIGINAL table, predates
`procedures`). Columns: `io_schema`, `skill_ref`, `success_criteria`,
`cost_estimate`, PERT three-point estimates, `embedding`. Gained
`scope_type`/`scope_entity_id` in migration 21 like everything else.

**Real, load-bearing distinction already enforced**: `procedures` (added in
migration 18, per that file's own header comment) exists BECAUSE
`task_nodes` was being used as a tagging convention for procedures
(`created_by='htn_method_library'`, decomposition stuffed into
`io_schema`) — exactly the "task-node/procedure collapse" CONSOLIDATED §0
correctly assumes is already resolved. **Do not re-collapse them.**
`task_nodes` today is used for: (a) retrieval fusion alongside claims/
procedures, (b) the legacy debate/decomposition flow
(`app/services/decomposition.py`), (c) write-only SKILL.md-ingestion
provenance (`skill_ingestion.py::_write_task_nodes`, which now inherits
real `scope_type`/`scope_entity_id` from the parent procedure — landed
this session). It is **not** where the execution DAG is built from (§3).

**CONSOLIDATED §26/§39 "TASKS = reusable capabilities to accomplish"**: no
single first-class object today cleanly matches this definition —
`task_nodes` is closer to "capability advertisement + retrieval anchor"
than "a reusable unit of work a procedure step names." A procedure step's
real unit of work is a `PlanNode` (§3), generated fresh per compile, not a
persistent, reusable `task_nodes` row. **This is a real conceptual gap**
worth a deliberate Phase-2/3 decision (reuse `task_nodes` more, or accept
the split) rather than a new "Task" table — CONSOLIDATED Rule 2 forbids a
parallel structure here.

---

## 3. Execution: procedure → plan → task graph → plan node → execution

Four real, distinct, versioned/bitemporal objects, migration 23
(`db/23_plan_persistence.sql`) + `app/execution/plans.py` +
`app/execution/plan_persistence.py` + `app/models/plan.py`:

```
procedures (steps JSONB)
   │ steps_to_linear_nodes() / expand_procedure_steps()   [procedure_graph.py]
   ▼
PlanNode[]  (order, goal, step_ref, implementation_hint, deps, node_class)
   │ compile_plan()                                        [plans.py]
   ▼
CompiledPlan { ExecutionPlan, TaskGraph }   -- content-hashed, deterministic
   │ persist_compiled_plan()                                [plan_persistence.py]
   ▼
execution_plans row + task_graphs row  -- BOTH append-only, engine-enforced
   │ record_plan_execution()                                [plan_persistence.py]
   ▼
executions row  -- born complete, no UPDATE possible (trigger-enforced)
```

`compile_plan` is **deterministic**: identical inputs → identical
`content_hash`/`graph_hash` regardless of generated ids, so a replay can
rebind to the original rows (`find_rebindable_plan` /
`_find_existing_plan`) rather than forking a duplicate. `execution_plans`
and `task_graphs` reject `DELETE`/`UPDATE` via a real Postgres trigger
(migration 23's own append-only enforcement — confirmed live this session:
test cleanup has to explicitly skip FK-referenced rows because the
database, not just application code, refuses the delete).

**Real node-level execution**: `app/execution/graph_executor.py::execute_task_graph`
walks a `TaskGraph` via caller-supplied `run_node(node) -> NodeResult`
closures — the graph module itself has zero knowledge of what a node
"does" (frontier call, WASM, deterministic script); that is entirely the
closure's job. Two real closures exist today:
1. `mcp_server/server.py`'s two `run_node` closures (tier-1 lightweight
   completion; tier-2/`reproduce_procedure` sandboxed Agent+tool loop).
2. `local_agent/runner.py`'s `LocalAgentRunner.run_node`.

**§27 implementation abstraction — landed this session**
(`app/execution/implementations.py`): a closed vocabulary
(`deterministic`/`tool`/`slm`/`frontier`/`human`) validated at the
`PlanNode.implementation_hint` boundary, with a real registry
(`resolve_implementation`) reporting `supported=True/False`. **Only
`frontier` has a real registered strategy today** — confirmed by reading
every real `run_node` closure in the codebase before writing that module.
`local_agent/runner.py` is the one caller that actually dispatches on this
today (refuses honestly, never silently substitutes, for an unsupported
kind). `mcp_server/server.py`'s two closures do **not** yet consult the
registry — this is the concrete, scoped Phase-6/7/8 starting point
(CONSOLIDATED §26–§30): wiring the registry into the MCP server's own
`run_node` closures is a small, well-bounded next step, not new
architecture.

**"plan_only" mode** (`find_best_way(mode="plan_only")`, built this
session): compiles + persists a real plan with **zero server-side LLM
calls**, returning it as structured JSON for an MCP-embedded host agent
(Claude Code/Cursor) to execute with its own LLM and report back via
`report_execution`. This is the real mechanism CONSOLIDATED §26's
"implementation selection" should route through for a `frontier`-hosted
caller — not a new endpoint.

---

## 4. Trace: event → trace → episode → observation

**Schema**: `db/12_trace_ingestion_pipeline.sql` (`agent_traces`,
`trace_events` — NOT NULL on `dedup_key`/`schema_version`, confirmed live
this session building the canonical demo), `db/01_ontology.sql`
(`episodes`, `episode_links` — no cascade from `episodes`, confirmed live),
`db/17_episode_project_columns.sql` (`project_id`, `session_id`,
`start_ts`/`end_ts`, `parent_episode_id` — added specifically for episode
assembly grouping).

**Real pipeline**: `app/services/trace_worker.py` —
`process_collector_file` (this repo's own dogfooded collector,
`.claude/settings.local.json` → `hook_wrapper.py` → redacted JSONL) →
`assemble_episodes` (groups trace events into an `EpisodeAssembly`,
parent/child via subagent transcript discovery) → `write_session_episodes`
(idempotent via a metadata fingerprint check — a real, once-live bug is
documented inline: double-JSON-encoding silently broke the fingerprint
dedup and duplicated every episode on re-run; fixed and now the canonical
"pass Python objects, not `json.dumps(...)` strings" warning in the file).

`app/services/ingestion_jobs.py::handle_normalize_trace_event` is the
event→episode entry a job-queue caller uses; `resolve_justification_episode`
and `handle_promote_observation_to_claim`/`handle_extract_procedure_from_episode`
are the queued continuation into §5/§6.

**Observation**: `app/services/observations.py::persist_observation` —
writes one `observations` row (migration 14) + `observation_events` link
rows per real `event_id`. Deliberately **not deduplicating**
(re-extracting from the same event twice makes two observation rows —
observations are immutable/re-derived, not superseded, ticket-04's
reasoning). `extractor_kind` is `'deterministic'` or model-derived, which
determines the claim's `epistemic_status` on promotion (§5) — this is the
one real, explicit "don't let an LLM turn an assumption into a fact"
mechanism CONSOLIDATED §8 asks for, and it already exists.

---

## 5. Claims (observation → claim)

**Schema**: claims are `knowledge_nodes` rows with `node_type='claim'`
(migration 01) — **not** a separate table, by original design (ticket 03's
explicit rejection of a tag-based representation is about task_nodes/
procedures, not about claims needing their own table; claims share
`knowledge_nodes`' shape deliberately: subject/predicate/object triple +
embedding + provenance + bitemporal). `scope_type`/`scope_entity_id`
(migration 21, CHECK-constrained migration 22) landed on `knowledge_nodes`
alongside every other core table, but — confirmed this session — were
**never actually populated by any writer** until this wave's fix
(`promote_observation_to_claim` now derives `scope_type='project'` from
the justifying episode's own `project_id`; proven live in the canonical
demo test).

**Writer**: `app/services/claims.py::capture_claim` — writes one
`knowledge_nodes` row + one `PRODUCES`/`CLAIM_OF` edge to EACH live
`task_node` in `task_ids` (a claim with no live task_node to attach to is
dropped, not written orphaned — "best-effort telemetry must never fail the
run"). `embedding` is computed at write time (a real, once-live bug is
documented: this used to be omitted entirely, making every claim
permanently invisible to `HybridRetriever`). Properties validated against
`ClaimProperties` (`NODE_TYPE_SCHEMAS` registry) before insert.

**Relations — CONSOLIDATED §4's exact ask, partially real today**:
`relate_claims(from_claim_id, to_claim_id, relation)` where
`RELATIONS = {"SUPERSEDES", "CONTRADICTS"}` (`claims.py:51`). **Real,
notable inconsistency found this pass**: the INSERT always writes
`edge_type='SUPERSEDES'` regardless of which of the two relations is
passed — the actual semantic distinction lives ONLY in `custom_edge_type`
(the polymorphic `edges` table's own free-text column, migration 01).
Querying by `edge_type='SUPERSEDES'` therefore does **not** distinguish
supersession from contradiction; a caller must also filter
`custom_edge_type`. This is the real, closed vocabulary CONSOLIDATED §4
asks to extend to `supports/refines/depends_on/conditional_on/generalizes/
specializes/derived_from/instantiates/applies_to` — and the RIGHT
extension mechanism is already established precedent: **add relation
names to `custom_edge_type`'s accepted vocabulary, do not ALTER the
`edge_type` ENUM** (the ENUM is a small, stable, structural category;
`custom_edge_type` is where semantic richness already lives, per this
exact file's own working pattern). Fix the `edge_type='SUPERSEDES'`
hardcoding bug as part of that work, not separately.

**Lifecycle — CONSOLIDATED §5's ask, real but coarser today**:
`properties->>'truth_state'` is a binary `IN`/`OUT` (`claims.py:12`), not
the 7-state `proposed/supported/current/disputed/contradicted/stale/
retired` CONSOLIDATED asks for. `relate_claims` flips a target to `OUT`
without ever setting `t_invalid` — "what we once believed" and "what we
believe now" stay separately queryable from the SAME row, which is a real
and correct bitemporal choice to preserve. **"Disputed" already exists as
a real, live-queryable status** — but it is **computed, not stored**:
`claims.py`'s `_DISPUTED_CLAIM_SQL` derives it from an open
`VALIDATED_BY`/`CONFLICTS_WITH` edge whose debate has not resolved
(`has_open_conflict_trigger`, `list_current_claims`). This is a genuinely
good pattern (no risk of a stored status drifting from the trigger state
that actually determines it) and CONSOLIDATED Phase-2 claim-lifecycle work
should extend this computed-status approach for `supported`/`stale` rather
than add a stored `status` column that can go stale itself.

**Real, confirmed gap**: no temporal-claim-version-chain exists analogous
to `procedures`' `family_id`/`version` chain — a claim superseding another
via `relate_claims` does NOT create a new versioned row the way
`supersede_procedure` does; it mutates `properties` on the OLD row in
place (`UPDATE knowledge_nodes SET properties = properties || ...`) and
inserts a fresh, unrelated claim row as "the new belief," with only the
`SUPERSEDES` edge linking them. CONSOLIDATED §11 ("commit A → claim
version 1 → commit B → claim version 2") is not yet a coherent version
chain the way procedures have one — this is real net-new work, not
extension of an existing mechanism that just needs wiring.

---

## 6. Evidence

**Schema**: `db/24_evidence.sql` — a real, first-class `evidence` table
(NOT the older `procedures.verification_stats` JSONB blob, though both
still coexist — see §1). `evidence_kind` ENUM: `execution_result`,
`observation`, `experiment`, `benchmark`, `document`, `human_review`,
`external_source`, `artifact`, `reproduction`. Carries `strength
{score, method}`, `independence_group`, `failure_class` (the spec's six
causes + `false_reuse`), a `target` (polymorphic, procedure-or-task),
`target_version` pinning for procedure-targeted evidence, append-only with
supersede-by-tombstone.

**Real finding, directly relevant to CONSOLIDATED §32 (Procedure
Experiments) and §43 (Evaluation)**: `evidence_kind` already includes
`'experiment'` and `'benchmark'` as first-class values — the schema for
CONSOLIDATED's requested "experiment abstraction"
(hypothesis/control/treatment/contexts/executions/metrics/result) is
**already representable** in the existing `evidence` table via
`evidence_kind='experiment'` + a structured `content`/properties payload.
**UNVERIFIED**: whether any real writer produces `experiment`- or
`benchmark`-kind rows today, or whether these values are declared but
dormant (grep found no writer using either kind in this pass's budget —
flagged for Phase-9/Phase-evaluation work to confirm before assuming a
writer exists).

**Capability computation — a major, real, already-built piece
CONSOLIDATED §25 explicitly asks for**: `app/services/procedure_extraction/capability.py`
implements exactly `P(success | task, implementation, context)` as the
**lower bound of a Wilson score interval** over a real outcome stream
(never the raw success percentage as certainty — CONSOLIDATED §25's exact
requirement), banded into an ordinal ladder (`unknown → observed →
reproduced → validated → generalized → trusted`) per a founder-ratified
spec (`BAND0_DECISIONS.md` D1 ruling). Routing thresholds apply to P
itself; the level label is presentation only, never a routing input.

This module's own docstring says "Not yet wired into retrieval/routing
call sites" — **that claim is now stale**: `failure_handlers.py::
capability_for_stream` (§9's real handler, see below) uses this module's
`OutcomeRecord`/`CapabilityScope`/`CapabilityRecord` directly, and
`applicability.py`'s `_capability_ranked_hits` (confirmed at
`applicability.py:905`, imported from `failure_handlers.py`) **is** part
of the real RRF fusion `find_applicable_procedures` uses for ranking
(§7). **Correcting a stale docstring is a one-line Phase-0 follow-up**,
separate from any architectural work.

**Failure-driven improvement — already real, CONSOLIDATED §31's four
routes exist, and is HALF-WIRED (confirmed, not flagged unverified)**:
`app/execution/failures.py` owns a durable, idempotent failure-route
QUEUE (`db/27_failure_routing.sql`, unique on `(evidence_id, route)`,
idempotent insert); `app/services/procedure_extraction/failure_handlers.py`
implements the four spec §36-mandated handlers (one is `capability_demotion`
— recompute from the cumulative evidence stream, demotion falls out of
recomputation, not a separate mutation) and consumes the queue via
`fetch_route_queue()`.

**The write side IS live**: `procedures.py::record_execution_outcome`
calls `classify_and_route()` on every real recorded failure
(`procedures.py:633`, inside the same row-locked transaction as the
outcome write) — every failure recorded through the one real,
already-tested outcome-recording path is already being classified and
queued today.

**The read side is NOT wired to anything**: `fetch_route_queue` has no
caller anywhere in `app/` or `scripts/` outside its own definition and
`failure_handlers.py`'s own use of it — no scheduled job, no MCP tool, no
script drains this queue in the real running system. The four handlers
are real and tested in isolation but are never invoked end-to-end. This
is a precise instance of the exact "half-gate" pattern CLAUDE.md's own
hard rule 6 warns against, just in the opposite direction (the trigger/
writer landed; the consumer never got wired to a runner) — **the single
cheapest, highest-leverage fix available for Phase 9**: write one real
periodic-or-triggered caller of `fetch_route_queue` + the four handlers,
no new architecture needed.

---

## 7. Retrieval: query → semantic/lexical → applicability → ranking

Two real, distinct modules, deliberately NOT merged (CLAUDE.md's own hard
rule: "Retrieval fuses by RRF; applicability is a non-compensatory
cascade. A violated precondition is a disqualification, not a low score.
Keep them apart."):

- **`app/services/applicability.py::find_applicable_procedures`** — cold-
  start gate (returns `[]` if too little verified evidence exists globally,
  UNLESS the caller explicitly opts into unverified candidates — a real,
  measured fix this substrate already made: the gate used to make the
  `allow_unverified_procedures` opt-in permanently unreachable, a
  chicken-and-egg that prevented the loop from ever bootstrapping) → a
  cost/embedding-fused candidate pre-filter (`_fetch_candidate_pool`) →
  `check_hard_constraints` per candidate, SHORT-CIRCUITING on the first
  failed constraint (temporal validity → staleness → availability →
  verification_state → **approval_status, a separate gate, confirmed
  live** → scope/exclusions → preconditions via `_project_state_cached`
  fail-closed under CWA (no claim found == unsatisfied, ticket 12's
  deliberate rejection of three-valued logic) → numeric invariants LAST,
  the only stage that can invoke a real evaluator) → similarity ranking of
  SURVIVORS ONLY via RRF-fused embedding-nearest + capability (§6), never
  ranking before filtering.
- **`app/services/retrieval.py`** — the general hybrid retriever
  (`HybridRetriever`, `fuse_rrf`) reused by applicability's own pre-filter
  and by `unified_retrieval.py` (§8).

**Real, confirmed gap for CONSOLIDATED §16**: retrieval combines
similarity + applicability + capability today. It does **not** yet
consult claim CONSISTENCY (a claim contradicting a candidate procedure's
own preconditions is invisible to ranking beyond the binary
precondition-satisfied check) — this is the direct payoff of landing
Phase 2 (claim graph) + Phase 3 (claim↔procedure integration) correctly;
retrieval itself needs no new architecture, only a new signal once claims
carry richer relations.

---

## 8. Local vs. global

**Global** (Postgres commons): everything above.

**Local** (`app/local_agent/`, SQLite, **DB-free by AST-enforced
structural contract** — `test_local_agent_runner_offline.py`'s own test
parses `runner.py`'s AST to forbid `asyncpg`/`app.db.session` imports,
confirmed this session):
- `local_store.py` — `LocalProcedureStore`, mirrors `procedures` schema
  in SQLite, gained durable publish-link columns this wave
  (`published_procedure_id`/`published_procedure_row_id`/`published_by`/
  `published_at`, idempotent `PRAGMA table_info`-checked migration).
- `local_applicability.py::check_local_hard_constraints` — DB-free
  cascade (temporal/staleness/availability/verification/scope/
  exclusions/numeric invariants). **Deliberately does not evaluate
  `preconditions`** — no local claims graph exists — confirmed by a
  dedicated live test this session proving preconditions are genuinely
  SKIPPED, never fabricated pass/fail.
- `local_episode_evidence.py` — DB-free analogue of the global
  `episode_evidence.py`, real per-step verification data, honest empty
  fields (never fabricated).
- `local_learning.py::maybe_capture_local_candidate` — ad-hoc capture
  from a successful local run; this wave added real embedding-at-capture
  (was lexical-only-findable before) and the run's own outcome now counts
  as the candidate's first evidence.
- `runner.py::LocalAgentRunner` — the real local execution loop; this
  wave wired implementation-hint dispatch (§3) and an upfront
  `repo_path` existence refusal.
- `unified_retrieval.py::orchestrate_unified_search` — merges local+
  global candidates, ranks by verification tier → capability (bucketed)
  → freshness → scope specificity.

**Publish (local → global)**: `app/services/publish.py::publish_local_procedure`
— real redaction, zero-inherited-evidence start (a locally-verified
procedure does NOT carry its local evidence count into the global row —
confirmed this session by a static source-scan test proving no
verification-promotion path auto-calls this), `AlreadyPublishedError`
refuses silent duplicate publish, `force=True` opt-in creates a genuine
new independent row rather than overwriting the earlier published one.

**CONSOLIDATED §7/§34's "local claim must not automatically become global
knowledge"**: enforced structurally today only in the sense that there is
**no automatic local→global claim promotion pathway to guard against** —
local claims (from a local episode) are not currently written to the
global `knowledge_nodes` table at all; local learning captures
PROCEDURES, not claims. This is honest by omission rather than by an
explicit privacy gate — worth a deliberate design decision in Phase 2
(does local claim capture get built at all in this release, per
CONSOLIDATED §50's "not required immediately" scoping guidance).

---

## 9. Provenance chain

Traceable TODAY, end-to-end, real code paths (proven live this session by
`test_canonical_personal_memory_e2e.py`):

```
trace_event (dedup_key, tool_name, success)
   → episode (content_ref locator, project_id, session_id)
   → observation (extractor_kind, event_ids link table)
   → claim (knowledge_nodes, scope inherited from episode's project_id,
            epistemic_status derived from extractor_kind)
```

```
procedure (provenance, scope, source_episode_ids)
   → evidence (execution_result rows, target_version-pinned)
   → verification_stats (counters) [+ evidence table, not yet unified, §1]
   → execution_plans / task_graphs (content-hashed, append-only)
   → executions (born-complete audit row)
```

**The joint CONSOLIDATED §3/§13 names as missing is exactly this**: no
edge/reference connects a procedure's `preconditions` entries back to the
`claim`/`observation`/`episode`/`source` chain above. A precondition today
is a bare structured predicate (`{subject, predicate, object}`) checked
against `_project_state_cached`'s live claim query — it is GROUNDED at
CHECK time (real claims are queried), but not PROVENANCED at AUTHOR time
(no stored pointer says "this precondition came from claim X"). This is
the single most consequential gap this audit found, and it is exactly
where CONSOLIDATED Phase 3 should start.

---

## 10. Directive phase → current-state map

| CONSOLIDATED phase | Real substrate today |
|---|---|
| Phase 1 (Postgres portability) | Not yet audited in full — separate pass. Already Postgres-only (no other DB). `pgvector` + JSONB throughout; exactly one `WITH RECURSIVE` site (`app/db/graph_store.py`, not yet read); real RLS backstop exists and is permissive-when-unset by design (§11) — needs a real test against a hosted provider, not just a design read. |
| Phase 2 (claim graph) | Relations exist but coarse (2 of ~11 requested, and one has a real labeling bug); lifecycle is binary + a computed "disputed" pattern worth extending, not replacing; no claim version chain. Real, bounded work — extend `edges`/`custom_edge_type`, do not add a parallel graph table. |
| Phase 3 (claim↔procedure integration) | Confirmed NOT wired, precisely as previously reported. Single biggest gap. |
| Phase 4 (context compiler) | **UNVERIFIED** — not located in this pass; likely does not exist as a named module (`find_best_way`'s `plan_only` mode returns a full compiled plan, not a token-budgeted context digest). Needs a dedicated search before Phase 4 starts. |
| Phase 5 (procedure induction) | Substantial real infrastructure already: `derive.py`, `synthesis.py` (multi-episode, this session), `strategies.py`/`registry.py` (mechanism/variant split). Decisions/branching (§19), cross-repo generalization depth — **UNVERIFIED**, needs targeted read. |
| Phase 6 (capability) | **Already real and already wired** (`capability.py` + `failure_handlers.py::capability_for_stream` + `applicability.py`'s RRF fusion). Needs: unify with `verification_stats` (§1), confirm routing actually uses it beyond ranking (§26 implementation selection). |
| Phase 7/8 (SLM/WASM) | Only `frontier` has a real registered strategy in `implementations.py` (§3). Net-new build, cleanly scoped by the existing registry's `supported=False` contract. |
| Phase 9 (failure-driven improvement) | Queue + 4 handlers exist and are TESTED but only half-wired: writer live (`record_execution_outcome` → `classify_and_route`), consumer (`fetch_route_queue` + handlers) has zero real callers. First task: wire one real caller of the consumer side — small, bounded, no new architecture. |
| Phase 10 (UGC) | Confirmed does not exist. Genuinely net-new. |

---

## 11. Follow-up verification (closed out same pass)

Six items were originally flagged unverified; five are now resolved by a
targeted grep/read pass rather than left as open questions for the next
phase to rediscover:

- **RLS backstop** (`db/29_rls_backstop.sql`): real, and NOT a second
  policy source — it re-states `services/access.py`'s existing tenancy
  predicate, keyed off `current_setting('app.tenant_id', true)`, bound
  transaction-locally via `set_config(..., TRUE)` (never session-scoped
  `SET`, which would leak onto the next pool borrower). **Permissive-when-
  unset by design**: unset `app.tenant_id` falls back to the row's own
  tenant, matching the current public-commons posture — this is a
  backstop against a future query that forgets the app-layer predicate,
  not an active multi-tenant enforcement today. `FORCE ROW LEVEL SECURITY`
  is set so it isn't decorative even for owner-role connections. Directly
  relevant to Phase 1: a hosted-provider migration does not need to
  change this file's *behavior*, only confirm the provider honors
  `FORCE RLS` + `set_config` semantics identically (a real thing to test
  against Supabase/Neon specifically, not assume).
- **Recursive CTEs**: exactly one file uses `WITH RECURSIVE` —
  `app/db/graph_store.py` (**not yet read in this pass** — Phase 1 should
  read it directly before assuming provider parity; recursive CTEs are
  standard SQL and portable, but worth confirming no Postgres-version-
  specific syntax is in play).
- **UGC contribution pathway** (Phase 10): confirmed **does not exist** —
  no `Contribution`-shaped class, no `user_submission` concept anywhere
  in `app/`. Genuinely net-new work, not extension.
- **`evidence_kind IN ('experiment','benchmark')`**: confirmed **no real
  writer produces either kind today** — the vocabulary is declared and
  representable (§6) but dormant. Phase 9/evaluation work building a real
  experiment abstraction should use this existing kind rather than adding
  a new one, and will be the FIRST real writer for it.
- **Failure-route queue feed status**: resolved and folded into §6 above
  (write side live, read side unwired).

**Still genuinely open** (not reached this pass — real work for whichever
phase needs them):
- Context-compiler existence (Phase 4) — no module found under a
  guessed name; a broader search (not just name-matching) is needed
  before concluding it doesn't exist at all in some other shape.
- Decisions/branching representation depth in extracted procedures (§19
  of the directive) — `derive.py`/`synthesis.py` were read for their
  compatibility-gate and generalization logic this session, but not
  specifically audited for conditional-branch representation.
- `app/db/graph_store.py`'s recursive CTE usage, noted above.

---

## Phase 0 verdict

**Audit complete for the areas CONSOLIDATED §2 (Phase 0) explicitly names**
(procedure substrate, task substrate, execution, trace, claims, learning,
retrieval, local/global, provenance). Six items above are explicitly
flagged unverified rather than guessed at. No code has been changed in
this phase, per Rule 1/Rule 4.

**Recommendation for Phase 1**: given the size of the six unverified
items and that they gate real design decisions (RLS posture in particular
gates how a hosted-Postgres migration script must be written), Phase 1
should start by closing those six items, not by assuming Phase 0's map is
complete.
