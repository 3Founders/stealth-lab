# Global Procedural Memory — Architecture Audit & V1 Plan

**Date**: 2026-08-30. **Method**: direct code reads this session (file:line cited
throughout) plus this session's own prior extensive verification (the
applicability cascade, precondition normalization, execution-layer wiring,
HTN removal). Where something is a proposal rather than existing code, it is
labeled **PROPOSED**. Nothing below is asserted from the spec docs alone —
every "implemented" claim traces to a real file.

---

## A. Current State Audit

| Component | Status | Evidence |
|---|---|---|
| Graph (nodes/edges) | **Implemented** | `db/01_ontology.sql`; `app/db/graph_store.py` (`GraphStore.traverse_from`, `blast_radius`) — real, tenant/scope-aware, recursive-CTE, explicitly "NOT LOAD TESTED" (`graph_store.py:140`) |
| Claims | **Implemented, partial lifecycle** | `app/services/claims.py::capture_claim()` — always lands `truth_state="IN"`, `claim_status` default `'candidate'`; no caller ever passes an override (confirmed by grep this session) |
| Evidence | **Implemented, well-designed, narrow scope** | `app/execution/evidence.py` — real invariant gates (#3, #12, #13, #19); `db/24_evidence.sql`. Only two evidence types (`execution_result`, `reproduction`) count toward `verified` (`REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED`) |
| Procedures | **Implemented** | `db/18_procedures.sql`; `app/services/procedures.py::capture_procedure/record_execution_outcome`. 698 live rows, all from a v1 seed script — one real domain populated |
| Implementations | **Missing as a first-class object** | `schema.md`'s `Implementation [V]` has no table; tool bindings live inline as `{"type":"tool","name":...}` JSON inside `steps` (confirmed in the document-ingestion design work this session) |
| HTN/DAG | **Deleted this session** | `app/execution/htn_agent.py` and its whole call graph removed 2026-08-30 (never wired to the live MCP tool; research harness moved to `experiments/harness/`) |
| Execution (real) | **Implemented this session, unwired into the product's main loop** | `app/execution/plans.py` (pure compiler, pre-existing), `app/execution/plan_persistence.py` (the INSERT layer, new), `app/execution/graph_executor.py` (the scheduler, new). Wired into `find_best_way`'s tier-2 as single-node graphs only — no real multi-step decomposition feeds it yet |
| Traces | **Implemented** | `db/12_trace_ingestion_pipeline.sql`; dogfooded live via `.claude/settings.local.json` hooks → `hook_wrapper.py` |
| Method reuse | **Implemented, narrow** | `app/services/method_library.py` — full-match-only, no partial/adapt tier, scoped to `created_by='htn_method_library'` rows (now orphaned since HTN's removal — **this module's only real producer is gone**, a new finding this session) |
| Capability | **Implemented, real, NOT wired** | `app/services/procedure_extraction/capability.py` — Wilson-interval banding (0–5), independent-groups/environments/review gates, `RoutingDecision` enum. Docstring states outright: *"Not yet wired into retrieval/routing call sites"* (`capability.py:42`) |
| Provenance | **Implemented** | `ProvenanceSource` enum (`models/ontology.py:17`), enforced at write time by `v0_gate.py::validate_provenance` |
| Versioning | **Implemented for procedures only** | `procedure_id`+`version` composite key (`db/18_procedures.sql`, unique constraint added `db/23_plan_persistence.sql`); claims have no version column — `knowledge_nodes` is row-id-addressed only (confirmed by `evidence.py:97`'s own comment) |
| TMS | **Aspirational — two narrow real substitutes** | `schema.md`'s Dependency Index (typed `depends_on`/`derived_from`/`supported_by`/`requires` edges) has **zero occurrences** in `backend/app` (confirmed by repo-wide grep this session). Real substitutes: `failure_handlers.py::handle_dependency_queue()` (one failure type, one `context_key` scope) and `graph_store.py::blast_radius()` (feeds a human-approval risk count, not auto-recomputation) |
| APIs | **Implemented, product-shaped not library-shaped** | `app/api/*` (FastAPI routers) + 9 MCP tools (`packaging/tests/test_server_offline.py`'s own list: `apply_change_set`, `check_procedure`, `decide_decomposition`, `decompose_task`, `detect_conflict_trigger`, `find_best_way`, `propose_synthesis`, `retrieve_precedent`, `submit_approval`) |
| Persistence | **Implemented, Postgres-coupled** | `asyncpg` throughout; JSONB, recursive CTEs, enum types, trigger-enforced freezes — porting off Postgres is a rewrite, not a config change |
| Retrieval | **Implemented, one confirmed correctness bug** | `app/services/retrieval.py` (RRF fusion, real); `applicability.py`'s candidate SQL orders by `jsonb_array_length(preconditions) ASC` with **no embedding term at all** (`applicability.py:381-387`) — a real, present-day bug independent of scale, fixed-but-not-yet-shipped per this session's launch-hardening plan |
| Embeddings/search | **Implemented** | HNSW indexes on every embedding column (`01_ontology.sql`, `19_procedures_embedding.sql`); `Embedder` class, Voyage-backed |
| Tests | **Implemented, large** | 1393 passed / 114 skipped (post-HTN-removal, this session, verified) |
| MCP | **Implemented, product-tool-shaped, not library-primitive-shaped** | Current tools bundle several concerns per call (`find_best_way` = search + applicability + report + extract in one); no standalone `search_procedures`/`get_procedure`/`report_execution`/`submit_procedure` |
| Ingestion | **Implemented for one domain (trace-derived), aspirational for documents** | `procedure_extraction/` is domain-agnostic and real (`extract_procedure()`); document-to-procedure ingestion is three ad hoc, uncommitted banking-only scripts in `vendor/tau2-bench/`, never generalized (this session's earlier design work) |
| UGC | **Missing entirely** | No submit/challenge/fork/attribute/reputation mechanism anywhere in `backend/app` |
| Privacy boundary (local/global) | **Missing entirely** | No local-backend concept, no admission controller, no "generalize this trace" pipeline. Everything today writes directly to one shared store |
| Local/global architecture | **Missing entirely** | Single backend, single Postgres instance, no two-tier design exists in code |

**Headline finding**: the substrate has a genuinely sophisticated *trust and
evidence* layer (capability.py, evidence.py, the applicability cascade) that
is mostly **built and correct but not fully wired together** — capability.py
computes nothing anyone reads yet; method_library.py's only producer
(HTNAgent) was just deleted; the document-ingestion path that would populate
a second real domain doesn't exist as shipped code. The gap between "real
primitives exist" and "the loop closes end to end" is the central finding of
this audit, not a missing concept.

---

## B. Architectural Contradictions

1. **`method_library.py` is now an orphaned consumer.** It reads `task_nodes` rows tagged `created_by='htn_method_library'` (`method_library.py:97,106`), written only by `persist_plan()`, called only from the now-deleted `HTNAgent`/`ResearchHTNAgent` call graph. The module still imports cleanly and its tests still pass (nothing calls `persist_plan` in tests either, confirmed by this session's HTN-removal work), but it is dead-in-practice code as of this session — a real, freshly-created contradiction between "this module exists and is tested" and "nothing produces its input anymore."
2. **Two different, non-interoperating "task decomposition" concepts share vocabulary.** `task_nodes`/`decompositions` (public-submission → debate-panel `ChangeSet` proposal, `app/services/decomposition.py`) and the schema.md `TaskGraph`/`ExecutionPlan` concept are both called "decomposition" in different docstrings, but confirmed this session to be entirely separate systems with zero code overlap. A reader of `schema.md` alone would reasonably conflate them.
3. **`verification_stats`' "Outcome" is not `schema.md`'s `Outcome [H]`.** `record_execution_outcome()` bumps a JSONB counter on `procedures.verification_stats`; `schema.md` describes a structured `Outcome` object with criteria/metrics. Same English word, two different, non-convertible shapes — confirmed this session while building the execution-layer wiring.
4. **`capability.py`'s ordinal ladder (0–5, Wilson-banded) and `verification_state`'s ternary (`candidate`/`verified`/`retired`, ticket-13 threshold-based) are two independent, non-unified capability representations**, both real, both live in the codebase, computing different things from overlapping inputs. `capability.py`'s own docstring acknowledges this explicitly: *"ticket 13's SPRT promotion logic in procedures.py stays untouched and authoritative for lifecycle transitions — this module computes the ROUTING input, it does not own lifecycle"* (`capability.py:38-41`). This is a genuine, acknowledged-in-code split, not an oversight — but it means **there is no single number today that answers "how good is this procedure."**
5. **`applicability.py`'s candidate selection contradicts its own stated purpose.** The module exists to rank by relevance under a non-compensatory cascade; its candidate *pre-filter* (before the cascade even runs) orders by precondition count, never relevance (`applicability.py:381-387`). At the current 698-row scale this silently drops most of the corpus from ever being considered; documented as a known, unfixed bug in this session's launch-hardening plan.
6. **Claim/state confusion at the `TargetRef` boundary.** `evidence.py:97`'s comment notes claims are "row-id-addressed (`knowledge_nodes` has no version column)" while procedures require an exact `(id, version)` pair — evidence about a claim and evidence about a procedure are structurally different shapes wearing the same `TargetRef` type, distinguished only by a runtime `target_type` string check, not the type system.
7. **`task_nodes` is simultaneously a generic "any task-shaped thing" bucket and several unrelated domain-specific tenants of it** — SWE-bench issue rows (`created_by='swebench_ingest'`), HTN method-library rows (now orphaned), `/v1/decompose` reuse-check candidates, and hybrid-retrieval search results all live in one table with no discriminated-union enforcement beyond a `created_by` string convention. This is a real premature-generalization smell: one physical table serving at least four logically distinct concepts, distinguished only by an unenforced naming convention.

---

## C. Procedure Primitive — the minimal rigorous contract (PROPOSED)

The current `procedures` table conflates **Procedure** and **Implementation**
(tool bindings live inline in `steps`, no separate table) and has no
**Execution** row it can point to that's real for HTNAgent-originated runs
(fixed this session for `find_best_way`'s tier-2, but only there). Below is
the minimal split that lets each concept do one job — deliberately *not*
adding fields beyond what retrieval/applicability/execution/capability
actually need, per the brief's own instruction.

```
Procedure                              (mostly EXISTS — db/18_procedures.sql)
  id, procedure_id, version             — real
  goal                                  — real
  steps: [{order, goal, ...}]           — real, PLANNER-NEUTRAL (no deps/branches — real DDL comment)
  preconditions: [Predicate]            — real shape (subject/predicate/object), mostly unpopulated
  invariants: [{"kind":"numeric",...}]  — real, z3-backed, unpopulated
  scope, exclusions, failure_conditions — real columns, mostly empty
  domain, domain_payload                — real, free-form
  provenance                            — real, enum-gated
  verification_state, staleness,
    availability                        — real, ticket-13 lifecycle
  embedding                             — real, HNSW-indexed

Implementation                          (MISSING — proposed new table)
  id, procedure_id, procedure_version   — FK, exact version (invariant #2 discipline)
  kind: "tool" | "agent_harness" | "human" | "deterministic_script"
  binding: {name, args_schema} | {harness_name, model_hint} | ...
  cost_hint: {tokens_est, latency_est}  — for routing, not billing
  status: candidate | verified | retired  — SEPARATE from the procedure's own state

Execution                               (EXISTS, narrowly — plan_persistence.py, this session)
  id, execution_plan_id, task_graph_id  — real, frozen-by-trigger
  procedure_id, procedure_version       — denormalized snapshot
  implementation_id                     — column exists (db/23), NEVER POPULATED (no Implementation table to point at)
  outcome: success|failure|needs_rework — real
  trace_id                              — real, polymorphic, no FK (deliberate)

Evidence                                (EXISTS — evidence.py, db/24_evidence.sql)
  target: {target_type, target_id, target_version}  — real, addresses Procedure OR Claim OR (proposed) Implementation
  evidence_type: execution_result | reproduction | ... — real, 9-value enum
  direction: supports | contradicts     — real
  independence_group                    — real, load-bearing
  outcome_status, success_criteria      — real, "no bare model-asserted success" gate

Capability                              (EXISTS, unwired — capability.py)
  scope: {task, state_signature, environment, input_signature, evaluation_criterion}
  p_estimate (Wilson lower bound), level (0-5), routing decision
```

**Relationships, explicit**: one `Procedure` version has 0..N
`Implementation`s. One `Implementation` accrues N `Execution`s. Each
`Execution` produces exactly one `Evidence` row (via `outcome_to_evidence()`,
real today). `Capability` is *computed*, not stored per-row — it's a
read-time aggregate over an `Implementation`'s (or procedure's, until
Implementation exists) `Evidence` stream, scoped by `CapabilityScope`. This
is the one addition this audit recommends adding a real table for
(**Implementation**) — everything else needed for the contract already
exists; the gap is that `steps`' inline tool bindings currently do
`Implementation`'s job with no version, no independent capability score, and
no way to say "Cursor's binding for this procedure scores 0.91, Claude
Code's scores 0.74."

---

## D. Procedure → HTN/DAG Instantiation (PROPOSED, answering the brief's ten questions directly)

1. **What is stored in the canonical procedure?** Goal, flat ordered `steps`
   (each `{order, goal}`, planner-neutral), `preconditions` (Predicate
   triples), `invariants` (z3 numeric constraints) — a *template*, not a plan.
2. **What is generated at execution time?** A `TaskGraph` (real,
   `plans.py::compile_plan()`) — today one node per procedure (flat-agent
   granularity); **PROPOSED**: one node per `steps[i]`, `deps=[i-1]` (linear
   chain — matches the real DDL constraint that steps carry no branching
   metadata; a true DAG needs `steps` to gain a `depends_on` field first,
   which is a schema change, not an instantiation-layer one).
3. **What is parameterized?** `compile_plan()`'s `parameters` dict —
   real, already supports this (`plans.py:275`).
4. **How are preconditions checked?** `applicability.py::check_hard_constraints()`
   — real, non-compensatory cascade, BEFORE instantiation, not during.
5. **How are subprocedures selected?** **Not implemented.** `steps` has no
   `subprocedure_ref` field — a procedure cannot today declare "step 3 is
   itself `explore_repo`." **PROPOSED**: add `steps[i].subprocedure_ref:
   Optional[ProcedureRef]`; at compile time, recursively expand any node
   with a `subprocedure_ref` into the referenced procedure's own compiled
   subgraph, spliced in with the parent's deps rewired to the subgraph's
   entry/exit nodes. This is the single most important schema addition this
   audit recommends for the "canonical procedures compose" vision.
6. **How are implementation candidates bound?** **Not implemented** (no
   Implementation table — see §C). Today: whichever agent harness calls the
   MCP tool IS the implementation, unrecorded as a distinct entity.
7. **How does the procedure adapt to a new repository/environment?**
   `procedure_scope` narrowing in `find_best_way` (`server.py`,
   `environment_probe.py`'s `language` predicate) — real but thin (one
   predicate). Real repo-specific adaptation (the `explore_repo:postgres`
   idea from this session's earlier discussion) is unbuilt: would need a
   procedure row scoped `scope_type="repository"` that `applicability.py`
   already knows how to narrow on (the mechanism exists; the corpus doesn't).
8. **How is applicability distinguished from execution?** Cleanly, today —
   `check_hard_constraints()` runs and returns a filtered candidate list;
   nothing about running it touches execution state. This is one of the
   substrate's genuinely correct separations.
9. **How are failures attributed** (procedure vs. implementation vs. state
   vs. planner vs. tool)? **Not implemented as a taxonomy.**
   `record_execution_outcome()` takes a `failure_class` string
   (`evidence.py`'s `FAILURE_CLASSES` enum exists) but nothing populates it
   with this five-way distinction today — it's a free-form classification
   point with no producer wired to reason about *which* layer failed.
10. **How does a successful execution update the reusable procedure?**
    `record_execution_outcome()` → `verification_stats` counter →
    ticket-13 threshold → `verification_state` flips to `verified`. Real,
    wired, narrow (counts only, no capability.py involvement yet).

**Worked example — `explore_repo` → `implement_feature`:**

```
explore_repo (canonical procedure, v1)
  steps: [{0, "list top-level dirs"}, {1, "read package manifest"},
          {2, "grep for entrypoint patterns"}]
  preconditions: [] (applies to any repo)

implement_feature (canonical procedure, v1)
  steps: [
    {0, subprocedure_ref: explore_repo@1},              -- PROPOSED field
    {1, "identify the auth architecture", deps:[0]},     -- PROPOSED deps field
    {2, "locate the change surface",       deps:[1]},
    {3, "implement",                       deps:[2]},
    {4, "run tests",                       deps:[3]},
    {5, "verify",                          deps:[4]},
  ]
```

At compile time, node 0's `subprocedure_ref` expands into `explore_repo`'s
own 3-node subgraph; `implement_feature`'s node 1 gets its `deps` rewired
from `[0]` to `[explore_repo's exit node]`. `graph_executor.py` (real, built
this session) already executes exactly this shape correctly — the gap is
purely that `compile_plan()`/`steps` don't yet support `subprocedure_ref`
or `deps` on stored procedures (only on already-compiled `PlanNode`s).

---

## E. Global Ingestion Architecture (PROPOSED — extends this session's earlier document-ingestion design)

```
source ─▶ normalization ─▶ observations ─▶ {claims|procedures|evidence} ─▶ canonicalization ─▶ dedup ─▶ index ─▶ publish
```

Per-source mapping (explicitly NOT "everything becomes a skill" — this
session's own earlier design already established this discipline for
documents; extending it here to the full source list the brief names):

| Source | → primitive | Real precedent to reuse |
|---|---|---|
| SKILL.md / agent skills | Procedure (candidate) | `procedure_extraction/strategies.py`'s extractor pattern |
| GitHub repo / workflow | Procedure + Implementation (tool bindings) + Evidence (from its own tests, if run) | none yet — new adapter |
| GitHub issue | Claim (problem pattern) + candidate Procedure (fix) | `observations.py::persist_observation` + `promote_observation_to_claim` |
| GitHub PR | Evidence (diff = implementation; CI status = execution_result) | `evidence.py::outcome_to_evidence` — CI green/red maps directly to `outcome_status` |
| Postmortem/runbook | Claim (failure condition) + Procedure (prevention/recovery) | same as issue, plus `failure_class` |
| Research paper | Claim + Evidence (weak: `evidence_type='document'`) + *maybe* candidate Procedure | this session's Path 3 design (`claim_status='uncertain'` at creation — new precedent, small `capture_claim()` extension) |
| Documentation | Claim (constraint) + Procedure (official steps) | Path 2 design (deterministic-first Tier-1/2/3 precondition normalization) |
| Benchmark result / agent trajectory (SWE-bench, OpenHands) | Episode + Evidence (execution_result, real outcome) + candidate Procedure | `procedure_extraction/evidence.py::AgentRunEvidenceSource` — **already exactly this shape**, real, wired |
| Tests (as a source, not just verification) | Evidence (`success_criteria` = the test's own assertions) | `evidence.py::_check_success_criteria` already requires exactly this |
| Stack Overflow / Q&A | Claim (candidate) + candidate Procedure, LOW default trust | Path 3 discipline (uncertain, weak evidence type) |

**Load-bearing point, not obvious from the brief alone**: the
*extraction mechanism* per source splits along a real, already-discovered
axis from this session — **is the source's content already
machine-checkable-rule-shaped (banking eligibility bullets, CI pass/fail,
test assertions) or is it human-argument-shaped (paper prose, tutorial
narrative)?** The former gets deterministic-first extraction
(`TEMPLATE_REGISTRY` pattern, no LLM required for the majority case); the
latter structurally requires an LLM for the "is this a claim or a hedge"
judgment — this is not a implementation shortcut, it's a property of the
source material itself (established and defended in this session's earlier
design work).

---

## F. Execution/Evidence Loop (mostly EXISTS, gaps named precisely)

```
procedure ──match (applicability.py)──▶ execution (plan_persistence.py + graph_executor.py)
    ──outcome (NodeResult/GraphExecutionResult, real, this session)
        ──▶ evidence (outcome_to_evidence(), real)
            ──▶ capability update (capability.py, REAL BUT UNWIRED)
```

Exactly recorded today, per real code: `Execution.outcome` (success/failure/
needs_rework), `trace_id` (polymorphic pointer), `procedure`+`version`
snapshot. **What's real but not connected**: nothing today calls
`compute_capability()` after an execution. The wiring gap is small and
specific — `record_plan_execution()` (this session's new function) knows the
outcome the moment it's called; it could feed an `OutcomeRecord` into
`capability.py` and persist a `CapabilityRecord` immediately. **This is the
single highest-leverage, smallest wiring gap in the entire audit** — the
math already exists, tested, correct; it's writing zero rows today.

---

## G. Capability Level System

**Already correctly designed — `capability.py`, verbatim, not a proposal.**
Wilson-lower-bound P estimate, 0-5 ordinal band with real gates
(independent-groups ≥2 for L2, verification-plan-satisfied for L3,
≥2-environments for L4, +completed-review for L5), a `RoutingDecision`
enum (`auto_route`/`offer_as_candidate`/`refuse_reuse`) driven by P alone,
never the presentation label. Model-brand is explicitly, provably excluded
from every computation (`source_metadata` field exists precisely so callers
don't smuggle brand into `success`/`environment`).

**What's missing is not the model — it's the storage and the caller.**
`capability.py` is pure (no DB reads). **PROPOSED**: a thin `capability_records`
table (CORE-A territory, per the module's own ownership note) storing the
`CapabilityRecord` shape keyed by `(procedure_id, procedure_version,
implementation_id, scope_hash)`, recomputed incrementally on each new
`Evidence` row rather than from a full replay every time. Given no
Implementation table exists yet (§C), this table's `implementation_id`
column would be nullable until that lands — an honest degradation, not a
blocker to shipping the procedure-level version first.

**Routing consequence, once wired**: `find_best_way`'s tier-1 lookup could
consult `route_for_p()` instead of the current binary
`verification_state == 'verified'` check — this directly answers the
brief's "predictable subtask → SLM/tool, uncertain → frontier model"
ambition, but only once Implementation exists as something to route
*between*.

---

## H. Global Admission Controller (MISSING — proposed from scratch)

No local/global split exists in code today; everything writes to one shared
store. Proposed pipeline, reusing real primitives wherever one already fits:

```
raw private trace (local only — NEW local store, out of scope for V1)
  ──▶ local extract_procedure() (REAL, unchanged — runs identically local or global)
  ──▶ admission check:
        privacy:        does the candidate's steps/preconditions/domain_payload
                         contain a literal file path, repo name, credential-shaped
                         string? (REUSE: extract_procedure()'s own V4 check already
                         does exactly this class of scan against evidence tokens —
                         `_evidence_tokens()`, `procedure_extraction/__init__.py:39-53`
                         — extend it from "did this leak from THIS episode" to
                         "does this contain anything path/secret-shaped at all")
        generalizability: has this exact procedure (by content hash, same
                         technique as `plans.py::compile_plan`'s content_hash)
                         already been submitted by ≥1 other independent user?
                         (REUSE: `independence_group` on evidence — a second
                         submitter is a second independence group)
        novelty:         does an existing global procedure already cover this
                         goal at ≥X similarity? (REUSE: `find_applicable_procedures`
                         itself, run against the GLOBAL store as a dedup check)
  ──▶ global candidate (provenance stamped `prior_library` or a NEW
      `user_contributed` value — v0_gate.py's enum needs exactly one new value)
  ──▶ same verification path every other candidate takes (ticket-13 thresholds,
      now against GLOBAL independent executions across users)
```

**Concrete accept/reject examples:**
- **Accept**: a procedure whose steps are `["run pytest", "check exit code
  0"]` with `domain_payload={"language":"python"}` — no path, no secret,
  generalizable.
- **Reject (privacy)**: a procedure whose step text contains
  `/home/anuj/stealth-lab-internal/...` — a literal local path leaked into
  the goal text (exactly the V4 check `extract_procedure()` already performs
  for a narrower purpose — extending its scope, not building new machinery).
- **Reject (novelty)**: near-identical to an existing verified global
  `explore_repo@7` at 0.97 cosine similarity — offered as reuse-of-existing
  instead of a new submission.

**Honest scope note**: this is the single largest unbuilt piece relative to
the brief's ambition. It is also the piece where "the smallest architecture
that proves the thesis" argues hardest for deferral — the killer experiment
(§M) does not require a local/global split at all; it requires one shared
corpus and a baseline-vs-library comparison. Build this after the
experiment, not before.

---

## I. UGC / Peer Review (MISSING — minimal version proposed)

Real precedent for every piece exists narrowly, none composed into UGC:
- **Submit**: `capture_procedure()` (real) + a new `provenance='user_contributed'`
  value.
- **Verify**: ticket-13's real threshold math, unchanged, applied to
  cross-user independence groups.
- **Fork**: `supersede_procedure()` (referenced by `procedures.py`'s own
  docstring, real) creates a new version — a fork is a new `procedure_id`
  with a `SUPERSEDES` edge (real `EdgeType`, `graph_store.py`) pointing at
  the original.
- **Challenge**: **genuinely missing** — nearest real precedent is
  `knowledge_conflict.py::detect_and_create_conflict_trigger`, built for a
  different purpose (debate-loop conflict detection), not user-initiated
  dispute. **PROPOSED minimal version**: a `procedure_challenges` table
  (challenger_id, procedure_row_id, reason, status) — deliberately NOT a
  comment thread, just a structured "I ran this and it failed under X"
  record that becomes a new `Evidence` row with `direction='contradicts'`
  once reviewed, reusing the evidence pipeline rather than inventing a
  parallel one.
- **Reputation**: explicitly, per the brief's own instruction, **execution
  evidence IS the reputation signal** — no separate score needed. A
  contributor's "reputation" is queryable directly as "how many of their
  submitted procedures reached `verified`" — a view over existing tables,
  not new state.

**Not building**: likes, comments, follower graphs, badges — the brief
explicitly warns against a generic social network, and nothing in the
current architecture needs them to prove the thesis.

---

## J. MCP API — minimal interface (PROPOSED)

Current 9 tools are product-shaped (bundle concerns). Minimal
library-primitive interface, each mapping to a REAL underlying function:

```
search_procedures(task: str, state: dict) -> [ProcedureSummary]
    → find_applicable_procedures() (REAL, applicability.py) MINUS the
      candidate-selection bug (§A) — must be fixed as part of exposing this,
      not after

get_procedure(id, version) -> ProcedureDetail
    → get_procedure() (REAL, procedures.py, verbatim)

check_applicability(id, version, state) -> {applicable: bool, failed_on: [str]}
    → check_hard_constraints() (REAL, applicability.py) — already returns
      exactly this shape (ApplicabilityResult)

report_execution(id, version, outcome, evidence: dict) -> EvidenceId
    → record_execution_outcome() + outcome_to_evidence() (REAL, composed)

submit_procedure(procedure: dict) -> {id, verification_state}
    → capture_procedure() (REAL, verbatim) — lands 'candidate', never
      fabricates verified (already correct, ticket-13 discipline)
```

**Example request/response** (`search_procedures`):
```json
// request
{"task": "add a rate limiter to an existing FastAPI endpoint",
 "state": {"language": "python", "framework": "fastapi"}}
// response
[{"id": "...", "name": "add-rate-limiter-fastapi", "version": 2,
  "verification_state": "verified", "successes": 14,
  "similarity": 0.87}]
```

This is strictly a *subset* of `find_best_way`'s current logic exposed as
five thin calls instead of one thick one — no new backend logic, a
refactor of the MCP surface, which is exactly the "5-tool" direction
already agreed on earlier this session and independently reachable from
what exists today.

---

## K. Scalability

Deferring almost everything per the brief's own instruction — most of this
was already worked out in this session's separate launch-hardening pass
(3-day plan, `.claude/plans/imperative-twirling-plum.md` history):

**Must design now** (cheap, and wrong-by-default otherwise):
- `applicability.py`'s candidate selection must be embedding-first, not
  precondition-count-first — this is a *correctness* bug today, at 698
  rows; it does not get better with scale, only more silently wrong.
- Every new table this audit proposes (`Implementation`,
  `capability_records`, `procedure_challenges`) needs the same
  scope/provenance/bi-temporal discipline every existing table already has
  — cheap to do at table-creation time, expensive to retrofit (per this
  project's own hard rule 2).

**Can postpone**:
- Partitioning append-only tables (irrelevant below real sustained write
  volume — 698 rows today).
- A dedicated graph database (`graph_store.py`'s own docstring already
  names the exact trigger: "traversal depth or graph size outgrowing CTE
  performance" — not there yet).
- A real event stream / async fan-out for the Dependency Index — the two
  narrow real substitutes (`handle_dependency_queue`, `blast_radius`)
  suffice until cross-procedure capability propagation is actually needed,
  which requires Implementation + capability_records to exist first.

**Hot path vs. async path, already correctly separated in the existing
code**: `governance.py`'s H3 buffered-drain pattern (append→buffer→batched
INSERT, replay-or-block on failure) is the *right* shape for high-volume
evidence ingestion at scale — reuse it directly for `Evidence`/`Execution`
writes once volume warrants it, rather than inventing a new buffering
mechanism.

---

## L. First 4-Week Implementation Plan

**Phase 1 (Week 1) — canonical procedure corpus + the one real bug fix**
- Fix `applicability.py`'s candidate selection (embedding-first). Files:
  `applicability.py`. Test: extend `test_applicability_e2e.py`.
- Seed 20-30 real canonical coding procedures (`explore_repo`,
  `find_entrypoint`, `identify_change_surface`, `debug_failing_test`,
  `write_tests_for_existing_code`, `safe_refactor`, ...) via
  `capture_procedure()` directly — no new ingestion pipeline needed for a
  hand-curated seed set of this size.
- Exit criterion: 20+ `candidate` procedures in `procedures`, each with
  real, checkable `preconditions`/`steps`, zero fabricated
  `verification_state`.

**Phase 2 (Week 2) — the 5-tool MCP surface**
- New thin MCP tools per §J, each wrapping an existing real function.
- Files: `app/mcp_server/server.py` (new `@server.tool()` functions),
  `app/mcp_server/tasks_extension.py` (decide which need task-augmentation
  — likely none, all five are fast).
- Test: one offline test per tool, FakePool-style, matching this session's
  own convention.
- Exit criterion: all 5 tools independently callable, verified against a
  real MCP client (`mcp dev` inspector).

**Phase 3 (Week 3) — execution + outcome, for real multi-step procedures**
- Add `steps[i].deps: list[int]` to the procedure schema (migration, new
  file per "next free number" convention) — the minimum needed for §D's
  linear-chain instantiation.
- Wire `compile_plan()` to build one `PlanNode` per step (not one
  monolithic node) when `deps` is present.
- Reuse `graph_executor.py` (already built, tested, this session) — no new
  scheduler code needed.
- Exit criterion: one real canonical procedure (`explore_repo`, 3 real
  steps) executes as a real 3-node graph, each node a real tool-call,
  proven live (same discipline as this session's
  `test_graph_executor_coding_live.py`).

**Phase 4 (Week 4) — capability, wired**
- New `capability_records` table (migration).
- `record_plan_execution()` calls `compute_capability()` and persists the
  result (small addition to `plan_persistence.py`).
- `search_procedures` (§J) surfaces `p_estimate`/`level` alongside
  `verification_state`.
- Exit criterion: a procedure's capability record visibly changes after a
  real execution — proven live, not just unit-tested.

**Phase 5 (ongoing from week 4) — the benchmark (§M)**

**Deferred past week 4, explicitly**: ingestion at scale, global evidence
sharing across users, UGC, TMS, the local/global split, SLM routing (needs
Implementation objects to route *between*, which needs real usage data
first).

---

## M. The Killer Experiment

**A vs. B, unseen repos, same model/harness:**
- **A (baseline)**: `find_best_way` with tier-1 (procedure lookup) *disabled*
  — pure tier-2, matching today's plain flat-agent behavior.
- **B (treatment)**: full two-tier `find_best_way`, procedure library
  populated per Phase 1.

**Task set**: real GitHub issues from repositories NOT used to seed the
procedure library (avoid contamination) — reuse this project's own
`experiments/harness/` measurement rig (the "live" one per this repo's own
doc-map, not the deleted HTN harness), which already knows how to run
matched A/B sweeps (proven this session on task_002, 10 trials/arm,
p=0.0001 — same statistical discipline, applied to a different question).

**Metrics** (all mechanically measurable from `AgentRun`'s existing return
shape — `stop_reason`, `tool_calls`, `usage.prompt_tokens/completion_tokens`,
`wall_seconds`, `patch`):
- task success rate (real test pass, not model self-report — same
  `run_succeeded = stop_reason=="finished" and bool(patch)` discipline
  already used in `find_best_way`)
- total tokens per solved task
- wall-clock latency
- tool-call count (repeated-mistake proxy: same tool called on the same
  target >2x)
- **procedure retrieval precision**: of tier-1 hits returned, what fraction
  were actually used productively (didn't get discarded mid-run)?
- **applicability accuracy**: of procedures the cascade admitted, what
  fraction turned out relevant vs. a false positive discovered only during
  execution?

**Sample size / methodology**: this session's own `.scratch/research/task002-matched-ab-10trials.md`
already sets the real precedent — matched pairs (same task, same seed,
arms differ only in the treatment), McNemar's test for paired binary
outcomes (referenced in `experiments/harness/`'s own naming), not an
unpaired t-test. **Minimum n for a crude signal**: 10 tasks × 2 arms
(proven sufficient to detect a large effect in the task_002 case,
p=0.0001); a real publishable result needs the standard McNemar
power-calculation the harness already implements, likely 30-50 tasks
depending on effect size — do not commit to a smaller number without
running that calculation for real, per this project's own measurement
discipline (`experiments/harness/`'s named purpose).

**Do not assume it works**: name the two most likely null/negative
outcomes up front, so a negative result is legible rather than explained
away — (1) retrieval precision is high but procedures are too shallow to
help (the real, honestly-labeled `candidate` procedure this session found,
"Call Bash 16x," is exactly this failure mode already observed once); (2)
applicability's non-compensatory cascade is too strict at small corpus
size, so tier-1 rarely fires at all, making A and B statistically
indistinguishable for reasons unrelated to the thesis.

---

## N. What to Delete / Not Build

**Already deleted this session**: `HTNAgent` and its whole call graph — it
was never wired to the live product, its research role is superseded, its
existence created the two real contradictions in §B.

**Should be deleted or explicitly retired next**:
- `method_library.py` — orphaned (§B.1). Either delete it, or repurpose it
  immediately for §D's `subprocedure_ref` expansion cache (a legitimate
  future use for the same "have we solved a shape like this before" idea)
  — do not leave it as dead-but-tested code pretending to be live.
- The three ad hoc banking-only document-ingestion scripts in
  `vendor/tau2-bench/` — superseded by this session's (unbuilt) generic
  document-ingestion design; keep as historical reference only, per this
  project's own doc-map convention, not as a path forward.

**Should be deferred, not built now**: local/global split (§H), UGC beyond
the minimal challenge mechanism (§I), TMS/Dependency Index generalization
(real narrow substitutes suffice until Implementation+capability_records
create something worth propagating), SLM routing (no Implementation
objects exist yet to route between), a dedicated graph database, any
multi-region/distributed storage.

**Should be simplified**: `capability.py`'s ordinal ladder and
`verification_state`'s ternary should NOT both continue evolving
independently forever — §G's wiring work should force a real decision
about whether `verification_state` becomes a *derived presentation* of
`capability.py`'s level (recommended) or the two stay deliberately separate
forever (also acceptable, but should be a stated decision, not an accident
of two lanes never talking).

**Should be isolated behind an interface, not deleted**: the flat `Agent`
(`experiments/swebench_pro/agent.py`) — `graph_executor.py` was built this
session with exactly this in mind (`run_node` is dependency-injected), so
swapping in a smarter per-node implementation later costs nothing at the
scheduler layer.

---

## O. Research / Novelty Assessment

1. **What's novel**: the *combination* of (a) a non-compensatory
   applicability cascade that treats a violated precondition as a hard
   disqualifier rather than a scoring penalty, (b) evidence-typed,
   independence-group-aware capability estimation with an explicit
   model-brand-blindness invariant (`capability.py`'s `source_metadata`
   design), and (c) bi-temporal invalidate-and-append as the storage
   discipline underneath both. Individually, non-compensatory filtering
   (CSP/rule engines), Wilson-interval confidence estimation (standard
   stats), and bitemporal databases (established DB pattern) are each
   solved elsewhere. **Their specific composition — a procedure retrieval
   system where "how well does this generalize" is a first-class,
   auditable, independence-aware statistic rather than a vector-similarity
   score or a star rating — is the genuinely distinctive piece**, and it is
   real, tested code today, not a pitch.
2. **What's already solved elsewhere**: vector retrieval + RRF fusion
   (standard); HTN planning (classical AI, and this project correctly
   recognized it wasn't earning its keep and removed it); skill/tool
   registries (Skilldex, Agentic Registry, MCP registries — this session's
   own earlier research already found these); case-based reasoning and
   experience replay conceptually overlap with "extract a procedure from a
   trace" (`extract_procedure()`), though the evidence-typed verification
   layer on top is not standard CBR.
3. **Merely engineering integration**: the MCP surface itself (§J); the
   document-ingestion pipeline (§E) — real work, not research.
4. **Research hypothesis, unproven**: that procedure retrieval genuinely
   improves outcomes vs. relying on model priors alone (§M is the
   experiment that would settle this — it has NOT been run for this
   product's actual procedure corpus; the task_002 A/B result this session
   ran was for a *retrieval* comparison, substrate-vs-bm25, not
   library-vs-no-library on real coding tasks).
5. **Publishable result**: a rigorously-run version of §M, with negative
   results reported honestly (the two named failure modes above), would be
   publishable — the honesty about failure modes is itself part of what
   would make it credible rather than a vendor benchmark.
6. **Strong startup moat**: not "we have data" — per the brief's own
   framing, correctly — but "our procedures carry an auditable,
   cross-environment, brand-blind capability score that a raw vector-memory
   competitor's retrieved snippet cannot." That's a real differentiator
   *if* §M's experiment succeeds; it is not yet demonstrated.
7. **What proves the thesis**: §M, run for real, with a positive and
   statistically defensible result on unseen repos. Nothing less settles
   it — internal confidence in the architecture is not evidence.

---

## P. Final Architecture

```
public sources (GitHub, docs, papers, benchmarks, trajectories)
    │
    ▼
ingestion compiler  (§E — source-specific extractors, shared canonicalization/dedup)
    │
    ▼
global procedural memory  (procedures + Implementation[NEW] + evidence + capability_records[NEW])
    │
    ▼
retrieval/applicability  (find_applicable_procedures — REAL, fix candidate-selection bug)
    │
    ▼
MCP  (5 thin tools, §J — REAL underlying functions, new thin surface)
    │
    ▼
arbitrary harness  (Claude Code / Cursor / Codex / ... — MCP is the only coupling point)
    │
    ▼
local execution  (graph_executor.py — REAL, dependency-injected, harness-agnostic)
    │
    ▼
evidence  (outcome_to_evidence() — REAL)
    │
    ▼
capability  (compute_capability() — REAL, needs wiring)
    │
    ▼
global update  (capability_records write-back — NEW, small)

─────────────────────────────────────────────────────────────
LOCAL / PRIVATE BACKEND (§H — NOT BUILT, explicitly deferred past the
killer experiment):

  raw traces ──▶ local extract_procedure() ──▶ [PRIVACY BOUNDARY] ──▶ admission
                                                                        controller
                                                                            │
                                                            (only generalized,
                                                             privacy-checked,
                                                             novel candidates
                                                             cross the boundary)
                                                                            ▼
                                                              global procedural memory
                                                              (the box at the top)
```

The privacy boundary sits at exactly one point: after local extraction,
before global submission. Everything above the boundary line in this
diagram already exists as one shared system today (no local/global split
in code) — building the boundary is entirely additive to what's there, not
a rearchitecture of it.

---

## Q. Final Verdict

1. **Genuinely strong**: the evidence/capability layer (`evidence.py`,
   `capability.py`) is real, correctly designed, and more rigorous than
   this audit expected to find — Wilson intervals, independence-group
   discipline, brand-blindness as an enforced invariant, not a policy.
   The non-compensatory applicability cascade is a real, defensible design
   choice, not hand-waved.
2. **Single biggest architectural problem**: **the loop doesn't close.**
   Every real, correct piece (capability.py, evidence.py, the applicability
   cascade, the newly-built execution layer) exists in isolation —
   `capability.py` computes nothing anyone stores or reads; `method_library.py`
   just lost its only producer; document ingestion for a second domain
   doesn't exist as shipped code. This is not a design flaw so much as an
   integration debt — but it means the system today cannot yet demonstrate
   "experience → procedure → verification → capability → reuse → better
   outcome" end to end, on any single real example, without new wiring.
3. **Single most important missing primitive**: **Implementation.**
   Without it, there is no object to compose multiple harnesses' bindings
   onto, nothing for `capability.py`'s per-scope estimate to attach to
   below the procedure level, and no way to ever answer "which
   implementation of `explore_repo` should route here" — the whole
   model-substitution ambition in the brief depends on this one table
   existing.
4. **Build next**: Phase 1-2 of §L (fix the retrieval bug, seed a real
   canonical coding corpus, expose the 5-tool MCP surface) — cheap, uses
   only what's real today, and is the prerequisite for §M regardless of
   what else gets built.
5. **Explicitly do not build next**: the local/global split, UGC beyond a
   minimal challenge mechanism, TMS generalization, SLM routing — all real
   future work, none required to answer whether the core thesis holds.
6. **The experiment that determines whether this is worth pursuing**: §M,
   run for real, on unseen repositories, procedure-library-on vs.
   procedure-library-off, with the two named failure modes reported
   honestly if they occur. Nothing else in this document substitutes for
   actually running it.
