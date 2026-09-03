# Final V1 — what shipped

_Version lineage — three points, all explicit:_

| Point | Commit | Tag |
|---|---|---|
| Historical baseline | `a5dace6` | `v1-baseline-2026-09-02` |
| Historical frozen Final V1 | `d0b173c` | `v1-final-2026-09-03` |
| Post-freeze security hardening | the commit tagged `v1-final-2026-09-03.1` | `v1-final-2026-09-03.1` |
| Evaluation-suite findings closed (current launch candidate) | the commit tagged `v1-final-2026-09-03.2` | `v1-final-2026-09-03.2` |

`v1-final-2026-09-03` (`d0b173c`) is the **historical frozen Final V1**, not
the current launch commit. The **launch candidate** is the `.2` patch tag
(`v1-final-2026-09-03.2`), which closes the two product defects the
independent Final-V1 evaluation suite discovered against the hardened
product — staleness not propagating into the leaderboard (Bug #7) and
Benchmark/Evaluation reads not inheriting the owning Problem's scope
(Bug #8). See § "POST-FREEZE EVALUATION FINDINGS" at the end. The `.1` tag
removed the public `apply_change_set` MCP tool (see § "POST-FREEZE SECURITY
HARDENING" below); it stays immutable, as does `.1`'s and `.2`'s
predecessors.

_Written 2026-09-03 for FINAL-V1 §6, extended for the freeze pass (§8/§10)
and the post-freeze security hardening._

This is the single place a reader learns what changed in the Final-V1
hardening wave. It does not restate the whole system — `README.md` (V1
frozen section), `proj_status.md`, and `schema.md` still hold. It records
**what is new, where it lives, and what proves it**, plus the quality
limits V1 knowingly ships with, plus the measurement work that is
explicitly *not* in this wave.

The interim record this supersedes: `.scratch/current-state-audit.md`
(§0 audit) and `.scratch/final-v1-hardening-acceptance.md` (§61 acceptance
matrix). Every closed unit's design rationale is in its commit message
(`git log a5dace6..v1-final-2026-09-03.1`).

Terminology used throughout (one set, no synonyms):

- **Problem / Benchmark / Solution / Evaluation** — the product model
  (migration 35). Capitalised when the domain object is meant.
- **durable execution run** — one `execution_runs` row + its
  `execution_run_nodes`, the mutable resumable unit beside the
  append-only `executions` table.
- **execution descriptor** — the one deterministic, secret-free
  projection of an Implementation Registry row to the execution ABI
  (`descriptor_version = "impl-descriptor/1"`).

---

## SHIPPED IN FINAL V1

### 1 · Problem / Benchmark / Solution / Evaluation product model

**Migration:** `backend/db/35_product_model.sql`
**Service:** `backend/app/services/product_model.py` (the one service REST
and MCP both call — no ranking or lineage logic anywhere else)
**REST:** `backend/app/api/problems.py`, prefix `/v1`, registered in
`app/main.py`
**MCP:** 6 read-only tools in `backend/app/mcp_server/server.py`

This reverses the earlier "no `solutions` table — read-composition only"
decision (documented as a board note in commit `8aecc04` and in
`app/api/solutions.py`). It is an **association + read-model layer over the
existing substrate** — `procedures` / `task_graphs` / `task_nodes` /
`execution_plans` / `executions` / `evidence` are unchanged, no target
object is ever copied, there is no second execution engine.

| Object | Rule |
|---|---|
| **Problem** | Durable. `status` ∈ `open` / `active` / `solved` / `archived`. Standard scope pair (`owner_id` / `visibility` / `scope_type`); private Problems stay private through `scope_predicates()`. |
| **Benchmark** | Versioned. Once a benchmark has been used for a published (completed) result it is **immutable** — `trg_benchmark_frozen_immutable` (BEFORE UPDATE) rejects any change to its measured meaning (protocol / criteria / environment / comparison policy / version / name). New meaning = a new version row. `freeze_benchmark()` in the service. No independent visibility: a Benchmark **inherits the owning Problem's scope** — `get_benchmark` / `list_problem_benchmarks` gate on `get_problem(scope)` before returning anything (as of `.2`; see § "POST-FREEZE EVALUATION FINDINGS", Bug #8). |
| **Benchmark case** | The smallest existing executable unit: a case **is** a `task_nodes` row + `expected_outcome` + `verification_criteria` overlay (`benchmark_cases`, FK to `task_nodes`). Not a new executable abstraction. |
| **Solution** | Association only: `(problem_id, solution_type, target_id, target_table, version, status, proposer, provenance)`. `solution_type` ∈ `procedure` / `task_graph` / `task`, with a type↔table CHECK. `associate_solution` validates the target row exists via the right id column (`procedures.procedure_id` for a procedure, `.id` for task / task_graph). Nothing is copied. Solutions **inherit their Problem's visibility** (`list_problem_solutions` gates on `get_problem(scope)` — same precedent as task_graphs inheriting plan scope). |
| **Evaluation** | Aggregates over **real** executions + evidence. Version-pins `procedure_id`+`procedure_version` and `implementation_id`+`implementation_version` (UUIDs, no FK, so a tombstoned version stays interpretable). `evaluation_executions` is the only Evaluation↔Execution link; no raw payload is duplicated. No independent visibility: an Evaluation **inherits the owning Problem's scope** — `get_evaluation` / `list_problem_evaluations` gate on `get_problem(scope)` (as of `.2`; Bug #8). A completed Evaluation is **historical evidence** and is never rewritten or deleted; whether its Solution is still a *current* leader is recomputed on read from the target's present validity (Bug #7). |

**Evaluation lifecycle — anti-fabrication.** An untrusted caller cannot
fabricate a `completed` Evaluation or a `verified_success_rate`:

- `complete_evaluation` **requires** real `execution_ids`, links them via
  `evaluation_executions`, and **recomputes** `run_count` + success /
  verified counts + latency percentiles from those executions and their
  evidence. Caller-supplied success / verified numbers are ignored;
  `extra_metrics` carries only values the substrate cannot derive.
- `trg_evaluation_completed_has_lineage` is the DB backstop — `status =
  'completed'` with zero `evaluation_executions` rows raises. Verified
  live: a bare `UPDATE evaluations SET status='completed'` fails.

**Lineage rule (§57).** A completed Evaluation points through
**Benchmark → Solution → pinned procedure/implementation version →
Execution(s) → Verification → Evidence**. There is no hand-written "final
best" row anywhere on that chain.

**Comparability.** `product_model.evaluations_comparable(a, b) → (bool,
reason)`. Two Evaluations are comparable iff: same benchmark id, same
`methodology.verification` semantics, same **material** environment keys
(non-material env keys are ignored), and both are `completed`. An
incomplete Evaluation is never comparable.

**Ranking / current-best (§17–§19, §38).** `problem_leaderboard` computes
**on read** from completed-Evaluation lineage:

- rank by the **Wilson interval lower bound** of verified success
  (`wilson_interval`) — never a raw rate;
- `MIN_RUNS_FOR_RANKING` gates small samples, so `100%, n=2` never
  reaches `BEST_VERIFIED` (its Wilson lower bound is below the floor →
  `INSUFFICIENT_EVIDENCE`);
- derived states: `BEST_VERIFIED` / `HIGH_PERFORMING` / `PROMISING` /
  `INSUFFICIENT_EVIDENCE`, plus `STALE` for a Solution whose underlying
  target is no longer valid;
- **current-validity gate (as of `.2`, Bug #7):** a Solution is only an
  *eligible* leader if the thing it points at is valid **right now**.
  `_ineligible_solution_reasons` reuses the existing staleness truth — a
  `procedure` Solution is ineligible iff its live `procedures` row is
  `staleness = 'stale'` (or has no live version), the same disqualifier
  `find_applicable_procedures` applies; a `task` Solution is ineligible iff
  its `task_nodes` row is tombstoned. An ineligible Solution stays in
  `leaderboard` with its historical numbers but is `state = STALE`,
  `eligible = false`, dropped from `current_best` and every
  `conditional_leaders` slot, and always sorted last (it can never
  *outrank* a valid Solution). `task_graph` Solutions carry no
  validity/staleness signal today — a documented gap, not a stronger
  guarantee than exists. New `ineligible_solutions: [{solution_id,
  reason}]` on the response;
- `TIE_EPSILON` → `current_best` is a **list**;
- `conditional_leaders` for reliability / cost / latency / first-pass;
- **`current_best` is derived, never stored.** It is `[]` when nothing is
  verified yet ("no verified solution yet"). The `problems` row carries no
  `winner` / `best_solution_id` column.

**`find_best_way` (product-model sense, §38).** `product_model.find_best_way`
takes an NL goal, matches it to a **Problem** (words OR'd, `ts_rank`), and
answers with **that Problem's evidence-derived leaderboard** — it never
picks "best" from text similarity. Distinct from the HTN coding agent tool
of the same short name (see §3 below); this one executes nothing.

**REST routes** (all under `/v1`):

```
POST /v1/problems                       GET  /v1/problems
GET  /v1/problems/find                  GET  /v1/best-way
GET  /v1/problems/{id}
GET  /v1/problems/{id}/solutions        GET  /v1/problems/{id}/benchmarks
GET  /v1/problems/{id}/evaluations      GET  /v1/problems/{id}/leaderboard
POST /v1/benchmarks                     GET  /v1/benchmarks/{id}
POST /v1/benchmarks/{id}/freeze
POST /v1/solutions/associate
POST /v1/evaluations                    GET  /v1/evaluations/{id}
POST /v1/evaluations/{id}/complete      POST /v1/evaluations/{id}/invalidate
```

**MCP tools** (read-only, converge on `product_model`):
`find_problem`, `inspect_problem`, `list_problem_solutions`,
`compare_solutions` (comparable completed evals only, plus an `excluded`
list), `inspect_evaluation` (version-pinned eval + linked execution ids),
`find_best_solution` (NL goal → matched Problem → current best VERIFIED
solution). As of `.2` these derive the caller's `AccessScope` the same way
the REST layer does — a real resolved OIDC subject → that user's scope,
nothing resolvable → `AccessScope.anonymous()` (public only) — via
`server._caller_access_scope()`, instead of the former hardcoded
`AccessScope.unrestricted()` that bypassed visibility entirely (Bug #8).

**Proof:** `backend/tests/test_product_model_offline.py` (12 — comparability
matrix, derived-state bands, small-n),
`backend/tests/test_product_model_e2e.py` (§57 lineage E2E vs Supabase),
`backend/tests/test_product_model_mcp_e2e.py` (all 6 tools reachable).

### 2 · Execution descriptor

**Migration:** none — projects the existing `implementations` columns
(`backend/db/33_implementation_registry.sql`).
**Code:** `backend/app/execution/implementation_registry.py` —
`descriptor(row)` (pure) and `get_descriptor(pool, id, *, scope)`.
**REST:** `GET /v1/implementations/{id}/descriptor`
(`backend/app/api/implementations.py`).
**MCP:** `inspect_implementation` now returns the full row **plus**
`descriptor`; `resolve_implementation` now emits **only** the descriptor +
a `reason`.

One canonical projection of a registry row to the execution ABI that a
harness / replay consumer binds against, instead of each consumer
re-deriving the shape from raw columns. Guarantees:

- **deterministic** — fixed field set and order (`_DESCRIPTOR_FIELDS`), so
  two calls on the same row byte-serialize identically;
- **exact identity** — `implementation_id` + `version`; a newer version is
  a different row → a different descriptor, and an already-bound descriptor
  never changes because the bound row never changes;
- **secret-free** — `auth_requirements` is sanitized to references only;
  an inline secret value becomes `{"redacted": true}`;
- `protocol` is derived from an explicit `locator` / `invocation` field if
  present, else mapped from `kind`; it never guesses a value the row
  cannot support;
- absent optional fields serialize as `{}` so a consumer can rely on the
  key existing;
- reuses `REGISTRABLE_KINDS` — no duplicate vocabulary, no second
  registry.

**Plan pinning.** Migration 23 already binds every execution to an exact
frozen plan version. The durable run (§3) pins `implementation_id` /
`version` on `execution_run_nodes` **at first resolve and never
re-resolves on resume**.

**Proof:** `backend/tests/test_implementation_descriptor_offline.py` (7).

### 3 · Durable retry / resume through the real tier-2 path

**Migrations:** `backend/db/36_durable_execution_runs.sql` (tables),
`backend/db/37_execution_runs_terminal_chk_fix.sql` (one-directional
terminal check so the layer is usable without a `CompiledPlan`).
**Code:** `backend/app/execution/durable_run.py` (the state layer),
`backend/app/execution/durable_graph.py` (`run_graph_durably` — the one
bridge from a compiled `TaskGraph` to a durable run).
**Wired into:** MCP `find_best_way` **tier-2** and `reproduce_procedure`
in `backend/app/mcp_server/server.py` — both now call `run_graph_durably`
instead of the in-memory `graph_executor.execute_task_graph`.

`executions` is append-only / frozen (migration 23), so the **resumable**
unit is a new mutable object beside it:

- `execution_runs` — `status` ∈ `pending` / `running` / `succeeded` /
  `failed` / `paused` / `cancelled`; `resume_count`; `final_execution_id`.
- `execution_run_nodes` — one row per `PlanNode.order`; `status` ∈
  `pending` / `running` / `succeeded` / `failed` / `blocked` / `cancelled`
  / `resumable`; `attempt_count` / `max_attempts`; `implementation_id`
  pinned at first resolve; `side_effecting`; `error_class`;
  `verification_state`; `worker_id` + `lease_expires_at`.

**Not a second scheduler / queue.** No polling loop, no timer, no backoff
scheduler. Each node transition is its own short transaction; the
`run_node` callback runs **outside** any transaction (a slow node never
holds a lock, a hosted statement timeout can't abort a run). Retry
happens on the next `resume_run` / `retry_node` call.

**Resume invariants (all proven):**

| Invariant | Mechanism |
|---|---|
| Crash between nodes resumes; completed nodes are **not re-run** | `resume_run` re-arms only non-terminal nodes |
| Crash **mid-node** is conservative | a `running` node with an expired lease: re-armed if pure, **parked** (`run.status = 'paused'`) if `side_effecting` — `retry_node()` then needs an explicit decision |
| A `succeeded` node can never un-succeed or be re-run | `trg_ern_terminal_fence` rejects a stale worker's / double-resume's direct `UPDATE` of a terminal node's status / attempt_count / result_ref / impl binding |
| Retry count is durable | `attempt_count` on the node row |
| Retry is explicit + bounded | `RETRYABLE_ERROR_CLASSES` vs `NON_RETRYABLE`; `max_attempts` (default 3); unknown class → **not** retried |
| Non-retryable / exhausted failure **stays visible** | node stays `failed`, run terminates `failed` |
| Concurrent resume is refused, not duplicated | `_claim_run` (worker_id + lease); a second worker under a live lease gets `ResumeInProgress` |
| A 2nd resume of a terminal run is an idempotent no-op | no node re-run |
| Exactly one immutable `executions` row on terminal | `durable_run._finalize` appends it via `record_plan_execution`, `implementation_id` pinned; callers must **not** also call `record_plan_execution` |

**Resuming an interrupted run through the product path.** `find_best_way`
gained a `resume_run_id: Optional[str]` parameter — passing it resumes the
existing durable run through the same tool. `_respond_tier1_hit` stays
in-memory (a non-stateful reasoning pass, not a long-running execution).

**Durable-run MCP surface.** Three read/act tools in `server.py` sit on
top of the durable-run service (creator-scoped — `REFUSED: not your run`
otherwise):

- `inspect_run(run_id)` — overall status, per-node status / attempt_count
  / max_attempts / error_class, the pinned implementation binding,
  worker/lease, and the full per-node attempt history. Read-only.
- `resume_execution_run(run_id)` — resume an eligible run. An
  already-terminal run is an idempotent no-op; a concurrent resume is
  `REFUSED`.
- `retry_run_node(run_id, node_order, force=False)` — explicit bounded
  retry of one `failed` / `resumable` / `blocked` node; `force=True`
  bumps that node's `max_attempts` by 1 (operator override). A
  `succeeded` node is never retried.

A **coding-agent / sandbox run** (its plan compiled by
`find_best_way`/`reproduce_procedure`, or a pending node with no real
provider) is **not** resumed headless by these tools — they return
`{"status": "needs_product_context", …}` pointing the caller at
`find_best_way(resume_run_id=…)` / `reproduce_procedure` instead. See
limitation #2.

**Proof:** `backend/tests/test_durable_run_offline.py` (12 —
`classify_error` matrix, retryable/non-retryable disjoint),
`backend/tests/test_durable_run_e2e.py` (§58, 3 vs Supabase),
`backend/tests/test_durable_graph_e2e.py` (1 vs Supabase: crash mid node B
→ zero `executions` rows → `run_graph_durably(resume_run_id=…)` → A not
re-run, B retries, C runs → exactly one `executions` row pinned to
procedure v1 → 2nd resume is a no-op).

### 4 · §28 — ChatGPT export branch reconstruction

**Migration:** none. **Code:** `backend/app/local_agent/historical_bootstrap.py`
and `backend/app/local_agent/chat_history_import.py`. **Surface:** the
`backend/scripts/bootstrap.py` historical-import path (no REST/MCP tool).

Release-critical evidence integrity. A ChatGPT `conversations.json` export
is a node **tree**: editing or regenerating a message forks sibling
branches under one parent, and only the branch on `current_node` was
actually continued. Both export parsers previously linearized every
`mapping` node by `create_time`, splicing abandoned siblings — a
fabricated tool call in a regenerated-away answer, a hedged superseded
reply — into the real transcript.

`_mapping_has_branching` + `_chatgpt_active_node_ids` now walk
root → `current_node` via `parent` pointers (downward `children` walk as
fallback):

- **linear conversation** → returns `None`; caller keeps the exact
  pre-fix timestamp path → **behaviour unchanged**;
- **branching with a resolvable `current_node`** → steps are extracted
  only from the active branch, in tree order;
- **branching with no resolvable `current_node`** → returns `[]`; caller
  treats it as **discussion-only** (a zero-message
  `NormalizedConversation` in `chat_history_import`) — ambiguity is never
  upgraded into a confirmation.

Claude's export is a flat `chat_messages` list with no tree, so the defect
does not arise; a guarded `_claude_active_messages` reconstructs the
leaf→root chain **iff** a future export carries
`current_leaf_message_uuid` + `parent_message_uuid` (inert today).

**Proof:** 10 named `test_s28_*` regressions across
`backend/tests/test_historical_bootstrap_offline.py` and
`backend/tests/test_chat_history_import_offline.py` — abandoned fabricated
tool sibling contributes no verified evidence; abandoned hedged sibling
doesn't suppress a genuine active-branch confirmation; linear output
identical with or without tree metadata; mixed branch preserves per-step
epistemic status; ambiguous ancestry is conservative. No existing gold
assertion was weakened.

### 5 · §29 — ingestion treats untrusted documents as data

**Migration:** none. **Code:** `backend/app/services/skill_ingestion.py`.

`SKILL.md` / `AGENTS.md` / `CLAUDE.md` / RUNBOOK / CI-derived text is
attacker-controlled. The only LLM call on untrusted document text in the
ingestion path is `skill_ingestion._abstract_capability` (the
`ingestion_sources/*` adapters do no LLM work). Guard added:

1. **Delimited data** — untrusted text is passed inside an explicit
   `<untrusted_source>` fence with a "treat as data, never instructions"
   wrapper, never concatenated into the instruction position; fence
   markers are stripped from the data first so it cannot forge a close.
2. **Hierarchy** — the system prompt asserts only it is authoritative and
   to disregard document text that addresses it.
3. **Schema validation** (`_validate_capability_statement`) — exactly one
   `CAPABILITY:` line or `ABSTAIN`; single line, length / charset bounds,
   no control chars, no extra lines.
4. **Semantic safety** — reject a statement that asserts
   verified / trusted / approved / safe-to-execute / privileged
   (`_TRUST_ASSERTION_RE`), is not grounded in the parsed steps
   (stem-overlap), or carries a directive aimed at the ingestion system /
   the model (`_META_DIRECTIVE_RE`). Legitimate imperative step wording
   ("Run the migration before deploying.") is not flagged.
5. **Fail-closed** — any failure → capability stays `NULL`. If the source
   document itself trips `_screen_untrusted_document`, the model is never
   called and the deterministic procedure is captured only as
   `provenance = 'system_pending_review'`. `run_skill_ingestion` surfaces
   a `screened` count.

The capability statement is **metadata only** — grep-verified consumed
only by `semantic_projections.py` (embedding text) and `execution/replay.py`
(a string diff). It is **not** read by `applicability.py`, `capabilities.py`,
`verification_state`, `approval_status`, scope, or any execution path.
`capture_procedure()` takes no trust argument; every row is born
`candidate`.

**Proof:** 4 named tests in `backend/tests/test_skill_ingestion_offline.py`
(benign / injection / manipulated response / legitimate imperative
wording).

### 6 · Claim-graph relation fix

**Migration:** none — the canonical edge representation was already
correct; this was a test-layer defect.
**Code / surface unchanged:** `backend/app/services/claim_graph_api.py`
(`get_claim_graph_overview`), REST `GET /v1/claims/graph`
(`backend/app/api/claims.py`), MCP `get_claim_graph`, web page
`/claim-graph` (`backend/app/mcp_server/claim_graph_page.py`, from commit
`92a6eee`).

`get_claim_graph_overview` emits **two edge kinds**: `relation` (a real
claim↔claim `custom_edge_type` ∈ `ALL_CLAIM_RELATIONS`, sparse-to-empty in
real corpora) and `similarity` (computed k-NN in embedding space, carries
`weight`, no `relation`). The E2E comprehension read `e['relation']` on
**every** edge and raised `KeyError: 'relation'` on a similarity edge on
real Postgres. Fixed by selecting `kind == 'relation'` first and asserting
similarity edges are well-formed. The claim graph stays **distinct** from
the procedural graph (`problems` / `solutions` / `procedures` /
`executions`) — no merge.

**Proof:** `backend/tests/test_claim_graph_overview_e2e.py` +
`backend/tests/test_claim_graph_mcp_e2e.py` — 37 green vs Supabase (commit
`bbf6ff0`).

### 7 · V1 frontend surface + WebMCP

**Where:** `frontendv1/` — a Next.js 16 App Router app, now tracked
(commit `39e2892`). It is a **separate deliverable owned by the frontend
session**; this doc records its state, it is not core-a's to change.

The old `frontend/` (Next.js 15 debate/approval UI) is **historical** and
is **not** the V1 product surface.

`frontendv1` renders the benchmark-first surface over the `/v1` REST API:
Problem page (current-best / tie / open hero + backend-ranked leaderboard +
evaluations), Search, Procedure / Task / Solution / Evidence /
Implementation / Repository / Project / Personal / Claim pages, auth,
submit. All ranking comes from the backend (`problem_leaderboard`) — the
frontend computes no leader.

**WebMCP:** `frontendv1/src/webmcp/` + the `/webmcp` status page.
`registerWebMcpTools()` feature-detects `document.modelContext` and
registers **13 semantic tools** over the domain API (`search_stealth`,
`find_best_way`, `inspect_procedure`, `inspect_task`, `inspect_solution`,
`inspect_evidence`, `inspect_repository_knowledge`, `compare_implementations`,
`find_problem`, `inspect_problem`, `list_problem_solutions`,
`compare_solutions`, `inspect_evaluation`). Where `document.modelContext`
is unavailable the wrapper is inert. Backend contract:
`.scratch/frontend_backend_contract.md`.

---

## KNOWN V1 QUALITY LIMITATIONS

Accepted, not blockers. Real, not invented.

1. **`find_best_way` tier-2 resume rebuilds the sandbox per invocation.**
   The durable run is called with `side_effecting_orders = set()` — a
   step is treated as replayable on resume, and prior steps' file edits
   ride forward as *context* to the agent, they are not mechanically
   re-applied to a fresh checkout. Resuming is safe (no double side
   effect at the graph layer) but a resumed run's earlier edits are only
   as durable as the agent choosing to reproduce them. Fine for the V1
   posture where `find_best_way` is a retrieval-grounded assist, not a
   long-lived stateful job.

2. **A coding-agent run does not resume headless.** The durable-run MCP
   tools (`inspect_run`, `resume_execution_run`, `retry_run_node`) resume
   a run that has a real provider per node. For a run whose plan was
   compiled by `find_best_way` / `reproduce_procedure` (a sandboxed
   coding-agent graph), `resume_execution_run` / `retry_run_node`
   deliberately **do not fake a resume** — they return
   `{"status": "needs_product_context"}`. Resuming such a run means
   re-invoking `find_best_way(resume_run_id=…)` (or `reproduce_procedure`)
   with the original `task_description` / `repo_path` supplied again by the
   caller. There is no "resume run X with no other input" path for a
   coding-agent run, by design.

3. **Execution-run reads are unauthenticated.** `GET /v1/runs/{id}` and
   `/nodes` return `created_by` / `scope_type` / `scope_entity_id` with no
   identity check. Mutations (resume / retry) *are* creator-gated. Tighten
   if private Problems ship. Scope: this item is now specifically about
   `execution_runs` — the product-model **downstream reads**
   (Benchmark / Evaluation / leaderboard) *are* scope-gated as of `.2`
   (Bug #8); `execution_runs` was left unchanged as out of this wave's
   surgical scope.

4. **`test_ingestion_admin_endpoint_e2e`** (trace → observation → claim
   drain) is a pre-existing red on a live DB — a narrow bug where
   `handle_promote_observation_to_claim` returns cleanly with no claim
   when the observation has no resolvable task/episode anchor. It is on
   the trace-ingestion path, **not** the corpus→procedure or product-model
   path, and is out of this wave's scope.

5. **`apply_change_set` — CLOSED post-freeze.** Was an ungated raw write
   primitive present in the public MCP registry; **removed as a public
   tool** in `v1-final-2026-09-03.1` (see § "POST-FREEZE SECURITY
   HARDENING"). Graph mutation from MCP is now gated via `submit_approval`
   / `decide_decomposition` only. Kept in this list as a pointer; the live
   entry is under FREEZE PASS · A · CLOSED.

6. **`find_best_way`'s Tasks-extension backing store is in-memory** —
   `--workers 1` is load-bearing; task state does not survive a server
   restart or span replicas. The durable execution run (§3) *is*
   crash-durable in Postgres; the MCP Tasks *envelope* around it is not.

---

## POST-V1 EXPERIMENT / MEASUREMENT

Explicitly **not** part of this wave. Listed so the boundary is clear.

- **Evaluation suite refresh** — re-run the full offline + Supabase E2E
  gate on a clean checkout, refreshed skip counts, and cut a new frozen
  **V1 baseline tag** to replace `v1-baseline-2026-09-02`.
- **Baseline-vs-Stealth experiments** — the three-arm sweeps
  (solo / ordinary memory / verified substrate) run against the shipped
  product model, not the harness prototype: does a Problem's
  evidence-derived `current_best` actually route an agent to a better
  Solution?
- **Ablations** — comparability gate on/off, Wilson lower bound vs raw
  rate, `MIN_RUNS_FOR_RANKING` sensitivity, durable-resume vs
  restart-from-scratch cost.
- **ROI** — token / wall-clock cost of the verified-substrate arm vs the
  win rate it buys.
- **Embedding-level retrieval eval** — `problems` / `procedures` have no
  embedding column today (`find_problem` is `ts_rank` lexical); measure
  what a vector index buys for Problem match and precedent retrieval.
- **Capacity / performance testing** — p95 tool-call latency, leaderboard
  compute-on-read cost at 10²–10³ Evaluations, durable-run throughput.
  Not exercised this wave.
- **External-corpus admission** — Corpus Phase 10 landed the retrieval /
  claim-graph proof and `final_report.md` for the 29 admitted sources;
  admitting an internet-scale corpus is post-V1 and gated on public-launch
  signals.

---

## FREEZE PASS (2026-09-03) — LIMITATIONS REGISTER

Added for FINAL-V1 §10. The full detail is in the sections above; this is
the four-bucket index the freeze gate requires.

### A · CLOSED BY FINAL V1

Everything under **SHIPPED IN FINAL V1** (§1–§7 above), plus, closed in the
freeze pass:

- **Frontend scripted browser E2E (§56)** — `2fae92c`,
  `frontendv1/e2e/v1-flow.spec.ts` (Playwright, 7 tests) against the real
  Next frontend + real FastAPI backend + real Supabase, no mock server.
  Seed via `frontendv1/e2e/seed_v1_flow.py` (real `product_model` write
  paths). The stale Problems list-page stub was wired to `GET /v1/problems`
  in the same commit.
- **Migration populated-upgrade test** — `885d83a`,
  `test_migration_upgrade_e2e.py`: throwaway PG17 cluster, apply 01–34,
  write a pre-hardening dataset through the real write paths, apply
  35/36/37 via the real `migrate.py`, assert every pre-existing row
  byte-for-byte unchanged + scope still enforced + the new product-model
  and durable-run tables work against the pre-existing rows + no
  destructive DDL. (This retires the old KNOWN-limitation "#3: migration
  upgrade is discipline, not a test".)
- **Full regression** — 2026-09-03: **2486 passed / 0 failed / 287
  skipped** across backend offline (2118), live E2E vs Supabase (12),
  harness (254), packaging (95), browser E2E (7); frontend `tsc` clean.
  `.scratch/final-v1-regression-results.md`.
- **Public ungated `apply_change_set` MCP exposure** — removed as a public
  tool (`v1-final-2026-09-03.1`, post-freeze security hardening); graph
  mutation is gated via `submit_approval` / `decide_decomposition` only.
  The internal `KnowledgeUpdater` is reachable from
  `app/api/approval.py::decide` and `app/api/decompose.py::decide` alone,
  each requiring a persisted proposal, a state gate, actor resolution, and
  an audit write. Public MCP tool count 30 → 29. Detail in
  § "POST-FREEZE SECURITY HARDENING" below and
  `.scratch/final-v1-postfreeze-hardening.md`.

### B · KNOWN / ACCEPTED V1 QUALITY LIMITATIONS

The six items under **KNOWN V1 QUALITY LIMITATIONS** above, i.e.:

1. `find_best_way` tier-2 resume rebuilds the sandbox per invocation
   (durable at the graph layer; a resumed run's earlier file edits ride
   forward as agent *context*, not mechanically re-applied).
2. A coding-agent run does not resume headless — the durable-run tools
   return `needs_product_context`; resume via
   `find_best_way(resume_run_id=…)`.
3. Execution-run **reads** are unauthenticated (mutations are
   creator-gated; consistent with every other V1 read surface).
4. `test_ingestion_admin_endpoint_e2e` pre-existing red — trace →
   observation → claim drain, not the corpus/product path.
5. ~~`apply_change_set` ungated + present in the public MCP registry.~~
   **MOVED to A · CLOSED** — removed as a public MCP tool in the
   post-freeze security hardening (`v1-final-2026-09-03.1`). No longer a
   known/accepted limitation.
6. MCP Tasks-extension backing store is in-memory (`--workers 1`
   load-bearing) — the durable execution *run* is Postgres-durable, the
   MCP Tasks *envelope* around it is not.
7. `get_claim_graph_overview(with_status=True)` issues ~4.5 queries/node,
   bounded by `limit ≤ 600` + a semaphore of 8; a flat fast-path
   (`with_status=False`) exists.
8. `frontendv1` `npm run lint` (bare `eslint`) crashes inside ESLint 9 /
   `@eslint/eslintrc` on the flat-config + legacy `extends` mix —
   pre-existing tooling defect. `npx tsc --noEmit` (the real type gate) is
   clean.

### C · UNMEASURED POST-FREEZE EVALUATION QUESTIONS

Not answerable from the code; require running the frozen product against
workloads. (Was mixed into "POST-V1 EXPERIMENT / MEASUREMENT" above.)

- Real **baseline-vs-Stealth** live results — do a Problem's
  evidence-derived `current_best` actually route an agent to a better
  Solution (three-arm: solo / ordinary memory / verified substrate)?
- Real **ROI / break-even** — token + wall-clock cost of the
  verified-substrate arm vs the win rate it buys.
- **Full embedding-level retrieval quality** — `problems` / `procedures`
  have no vector column today (`find_problem` is lexical `ts_rank`);
  measure what an index buys for Problem match + precedent retrieval.
- **Large-scale capacity** — p95 tool-call latency, leaderboard
  compute-on-read cost at 10²–10³ Evaluations, durable-run throughput.
- **Live LLM extraction quality** — procedure / claim / capability
  extraction accuracy on a held-out corpus.
- **Ablations** — comparability gate on/off, Wilson lower bound vs raw
  rate, `MIN_RUNS_FOR_RANKING` sensitivity, durable-resume vs
  restart-from-scratch cost.

### D · POST-V1 FEATURES

Deliberately not built; safe to add later.

- Richer generalization / transfer semantics where provably safe.
- Execution capabilities beyond what Final V1 requires (more providers,
  headless coding-agent resume, a persistent MCP Tasks store).
- Owner column + authenticated reads on `execution_runs` if private
  Problems ship.
- Full convergence of `find_best_way` (HTN coding agent) and
  `find_best_solution` (evidence leaderboard) into one entry point.
- Set-based claim-lifecycle query for large graphs.
- `schema.md` refresh to list the migration 35/36 tables (frozen — needs a
  formal unfreeze decision; today recorded as a board note).
- Internet-scale external-corpus admission (gated on public-launch
  signals).

---

## POST-FREEZE SECURITY HARDENING (v1-final-2026-09-03.1)

Landed **after** the `d0b173c` / `v1-final-2026-09-03` freeze. It changes
the public MCP surface, so it is a patch tag (`v1-final-2026-09-03.1` →
the commit tagged `v1-final-2026-09-03.1`) on top of the frozen Final V1, not a rewrite of it.
Full draft account: `.scratch/final-v1-postfreeze-hardening.md`.

**Issue.** The public MCP tool `apply_change_set` was an ungated,
arbitrary knowledge-graph write: any caller holding a valid token could
apply a hand-constructed `change_set` directly, with **no persisted
approval and no audit row**.

**Root cause.** It shipped as a bare `@server.tool()` alongside the gated
`submit_approval` / `decide_decomposition`. `CLAUDE.md`'s "behind an
opt-in flag, does not ship public" posture was never enforced by an
actual flag, so the tool was live in `tools/list` on every deployment.

**Fix.** Removed the public tool and its now-dead imports from
`app/mcp_server/server.py`. The internal mutation implementation
(`KnowledgeUpdater`) stays, now reachable **only** from
`app/api/approval.py::decide` (debate scorecards → persisted `scorecards`
row + `approvals` audit row) and `app/api/decompose.py::decide`
(decomposition proposals → persisted `decompositions` row, `status =
'proposed'` gate). Both require a persisted proposal, a state gate, actor
resolution, and an audit write, and the change_set applied is the
**stored** one — never caller-supplied.

**Approval model.** Unchanged. Same `approvals` table, same scorecard
state machine, same `decompositions.status` gate. No new approval system
was introduced.

**Public MCP change.** 30 → 29 tools. `tools/list` no longer exposes
`apply_change_set`.

**Tests.** `backend/tests/test_apply_change_set_removed_security.py` (21
offline) + `backend/tests/test_apply_change_set_removed_e2e.py` (2 vs
Supabase). Prove: `apply_change_set` absent from the registry and from
`tools/list` (total 29); no module-level `server.apply_change_set`; an
AST scan asserts `KnowledgeUpdater` is imported by **exactly**
`{app/api/approval.py, app/api/decompose.py}` and `server.py` imports
neither it nor `apply_debate_result` (guards against a re-added hidden
route); `approval.decide` on a missing / non-`PENDING_APPROVAL` scorecard
mutates nothing (404 / 409); `decompose.decide` on `status != 'proposed'`
is 409 and applies nothing, and `DecideRequest` / `ApprovalRequest` carry
no `ops` / `change_set` field so a caller cannot smuggle ops; both gated
paths apply the **stored** `row["change_set"]` verbatim; a resolved OIDC
actor overrides a spoofed `approver_id` in both paths; a failed apply
surfaces a plain 409 that leaks no `token` / `password` / `api_key` / DSN
material; re-deciding a decided proposal is 409, not a double-apply. The
two e2e cases drive a real `PENDING_APPROVAL` scorecard through
`submit_approval` and a real `status='proposed'` decomposition through
`decide_decomposition`, and confirm the write still lands **with** its
audit row (`approvals` / `decompositions.status`+`approver_id`+`decided_at`).
Full offline backend suite after the change: **2139 passed, 289 skipped,
0 failed**; packaging **95 passed**; `test_live_scripts_not_collected`
still green.

**Lineage.** Old tag `v1-final-2026-09-03` (`d0b173c`) unchanged and still
the historical frozen Final V1. Patch tag `v1-final-2026-09-03.1` was the
launch candidate until `v1-final-2026-09-03.2` superseded it (see
§ "POST-FREEZE EVALUATION FINDINGS"); all three tags stay immutable.

---

## POST-FREEZE EVALUATION FINDINGS (v1-final-2026-09-03.2)

Landed **after** `v1-final-2026-09-03.1`. The independent Final-V1
evaluation suite, re-run against the hardened product, found two real,
previously undocumented product defects. Both are closed here; the
evaluation suite stays the independent proving layer and no evaluation
code was imported into production. This is a **surgical** closure — the
product model, staleness, and authorization designs are unchanged; there
is no second ranking system and no second authorization system. No schema
migration (see below). Full account:
`.scratch/final-v1-evaluation-findings-fixed.md`. Discovery history is
preserved: these bugs existed in `.1` and earlier and were found by the
evaluation suite, not the test suite.

### Bug #7 — CLOSED — staleness must affect current-best / leaderboard

**Root cause.** The lower staleness chain already worked (claim change →
`propagate_claim_change` → `mark_procedure_stale` → `procedures.staleness =
'stale'` → `find_applicable_procedures` stops selecting it). But
`product_model.problem_leaderboard` never consulted that truth: it banded
each Solution purely on the Wilson lower bound of its **completed**
Evaluations, which are historical. A Solution backed by a now-stale
Procedure kept its `BEST_VERIFIED` band and stayed in `current_best`,
outranking currently valid Solutions.

**Fix.** `backend/app/services/product_model.py`. New
`_ineligible_solution_reasons(pool, solutions)` reuses the **existing**
disqualifier — a `procedure` Solution (whose `target_id` is the stable
`procedures.procedure_id`) is ineligible iff its live row is `staleness =
'stale'` or has no live version; a `task` Solution is ineligible iff its
`task_nodes` row is tombstoned; `task_graph` Solutions carry no such
signal and are documented as a gap, not given a stronger guarantee.
`problem_leaderboard` now marks an ineligible Solution `state = "STALE"`,
`eligible = false`, keeps it in `leaderboard` **with its historical
numbers**, drops it from `current_best` and every `conditional_leaders`
slot, sorts it last (it can never outrank a valid Solution), and returns a
new `ineligible_solutions: [{solution_id, reason}]`. If the only
`BEST_VERIFIED` Solution becomes stale, `current_best` becomes `[]` ("no
verified solution yet"). No stored winner; still computed on read.

**Regression.** `backend/tests/test_product_model_staleness_leaderboard_e2e.py`
— full lineage → confirm `current_best`; force staleness through the real
`relate_claims(SUPERSEDES)` production path; confirm `procedures.staleness`
really flips; re-read the leaderboard through the real service; confirm the
stale Solution leaves `current_best`, a fresh verified Solution is promoted,
the stale one is still listed, and **the historical Evaluation row is
unchanged** (`status='completed'`, lineage still pinned, no new Evaluation
fabricated). Second test: stale-only Solution → `current_best == []`.

**Historical Evaluation records are preserved; current-best eligibility is
derived from current validity.**

### Bug #8 — CLOSED — private Benchmark / Evaluation scope-gating

**Root cause.** Problem visibility was enforced (`scope_predicates()`), and
`list_problem_solutions` already inherited it. But `get_benchmark`,
`list_problem_benchmarks`, `get_evaluation`, and `list_problem_evaluations`
in `product_model.py` ran raw unscoped `SELECT`s, their REST routes never
threaded the viewer scope, and the six product-model MCP tools hardcoded
`AccessScope.unrestricted()` (which bypasses visibility entirely). Another
user — or an anonymous caller — could read or list a private Problem's
Benchmark/Evaluation data directly, or infer it from the leaderboard's
`benchmark_id`.

**Fix.** One shared guard, reused everywhere; no per-router auth logic.
- `backend/app/services/product_model.py`: the four accessors take a
  keyword-only `scope: AccessScope` (+ optional `tenant_scope`) and gate on
  `get_problem(pool, <problem_id>, scope=...)` being visible before
  returning anything — the exact idiom `list_problem_solutions` already
  uses. `problem_leaderboard` gained a top-level `get_problem` gate so a
  private board can't even leak its `benchmark_id`.
- `backend/app/api/problems.py`: the benchmark/evaluation routes pass
  `scope=scope` (already resolved by the `get_scope` dependency).
- `backend/app/mcp_server/server.py`: new `_caller_access_scope()` — the
  MCP analogue of REST `get_scope`: a real resolved OIDC subject →
  `AccessScope.for_user`, nothing resolvable → `AccessScope.anonymous()`
  (public only, matching an unauthenticated REST caller). The six
  product-model tools use it instead of `AccessScope.unrestricted()`.

**Regression.** `backend/tests/test_product_model_privacy_e2e.py` — proves
denial at three layers (service with `AccessScope.for_user`/`anonymous`,
REST with real `X-Viewer-Id` identity, MCP with the real caller-identity
contextvar), that a public Problem's downstream graph is still readable by
anyone, and the negative inference-leak checks (no benchmark id/name, no
`current_best`, no eval count in a stranger's leaderboard / `compare_solutions`
/ `find_best_solution` responses or bodies).

**Benchmark/Evaluation visibility inherits the owning Problem's scope.**

### Migration

**None.** `procedures.staleness` already carries the Bug #7 truth;
`benchmarks` and `evaluations` have no scope columns by design (they were
always meant to inherit the Problem's), so Bug #8 is a service-layer gate,
not a schema change. Fresh-DB and populated-upgrade migration tests are
unaffected and were re-run.

### Accepted limitations after `.2`

- `task_graph`-backed Solutions have no staleness or bi-temporal validity
  signal (`task_graphs` has neither axis by design), so a `task_graph`
  Solution is never marked `STALE`. Documented gap, surfaced honestly, not
  a silent assumption of a stronger guarantee.
- Product-model MCP reads are **public-only unless OIDC is configured**
  (`_caller_access_scope()` → `anonymous()` in the shared-token / loopback
  posture) — the same denial semantics an unauthenticated REST caller
  gets. A deployment that needs per-caller MCP visibility configures
  `OIDC_ISSUER` / `OIDC_AUDIENCE`, exactly as the write-path attribution
  already does.
- `execution_runs` reads remain unauthenticated (known limitation #3,
  above) — deliberately out of this wave's surgical scope.

### No ingestion

No external-corpus ingestion or admission was performed in this fix wave.
The next phase (evaluation-suite re-run pinned to `v1-final-2026-09-03.2`,
clean baseline, then admission and the A/B/C + ablation + ROI experiments)
begins only after this patch lands.
