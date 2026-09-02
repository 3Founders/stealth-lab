# Final Integration + V1 Hardening — Acceptance Matrix (§61)

Branch `core-a/ingestion-testing`. Baseline `a5dace6` (`v1-baseline-2026-09-02`).
Hardening head `d5f… (see git log)`. Every row is **CLOSED / PARTIALLY CLOSED / OPEN** —
no "mostly", no "probably". Evidence = a commit SHA + a test that runs.

Commits this wave: `bbf6ff0` `53c93f9` `209564a` `47f4ffd` `8aecc04` `ed958b2`
`3229641` `9040dfa` `002e6da` (+ `<isolation fix>`).

DB target for all E2E: Supabase `wckeklqxmiglivfolujn` (ap-south-1 session pooler),
Postgres 17.6, migrations 01–37 applied.

---

## PRODUCT MODEL

| Requirement | State | Evidence |
|---|---|---|
| Problem — durable, statuses OPEN/ACTIVE/SOLVED/ARCHIVED, scope, private stays private | **CLOSED** | mig 35 (`8aecc04`); `problems` table + CHECKs; `product_model.create_problem/get_problem/list_problems`; scope via `scope_predicates()`. `test_product_model_offline.py`, `test_product_model_e2e.py`. |
| Benchmark — versioned, IMMUTABLE once used for a published result | **CLOSED** | mig 35 `benchmarks` + `trg_benchmark_frozen_immutable` (BEFORE UPDATE raises if a frozen benchmark's protocol/criteria/env/version/name change). Verified live in `8aecc04` commit log; `freeze_benchmark()`. |
| Benchmark cases — smallest existing executable unit (Task), preserve input/expected/verification | **CLOSED** | mig 35 `benchmark_cases` = FK to `task_nodes` + `expected_outcome` + `verification_criteria` overlay. Not a new executable abstraction. |
| Solution — association only, types procedure/task_graph/task, NO target copied | **CLOSED** | mig 35 `solutions` (polymorphic `target_id`+`target_table`, type↔table CHECK); `associate_solution` validates the target row exists via the right id column (`procedures.procedure_id` for a procedure). `test_product_model_e2e.py`. Board note recorded re: reversing the prior "no solutions table" decision. |
| Evaluation — aggregate over real executions+evidence, version-pinned, no raw payload duplication | **CLOSED** | mig 35 `evaluations` + `evaluation_executions` (the only Execution link). `complete_evaluation` RECOMPUTES run_count + success/verified from linked executions & evidence — caller numbers for success/verified are ignored. |
| Evaluation lifecycle — untrusted caller cannot fabricate COMPLETED / verified_success_rate | **CLOSED** | `trg_evaluation_completed_has_lineage` (DB backstop) + `complete_evaluation` raises on `execution_ids=[]`. Verified live: `UPDATE evaluations SET status='completed'` with no lineage → raises (`8aecc04`); `test_product_model_e2e.py` asserts the ValueError. |
| Lineage — a completed Evaluation points through Benchmark→Solution→pinned proc/impl→Execution(s)→Verification→Evidence | **CLOSED** | **§57 E2E** `test_product_model_e2e.py::test_product_model_lineage_and_current_best_are_derived_not_stored` — builds the real `execution_plans`/`task_graphs`/`executions`/`evidence` chain, no hand-written "final best" row. |
| Version pinning — evaluation records benchmark/solution/procedure/implementation versions | **CLOSED** | `evaluations.procedure_id`+`procedure_version`, `implementation_id`+`implementation_version`; `benchmarks.version`; `solutions.version`. UUID (no FK) so a tombstoned procedure version stays interpretable. |
| Comparability — `evaluations_comparable(a,b)` on benchmark version / verification semantics / material environment | **CLOSED** | `product_model.evaluations_comparable` → `(bool, reason)`. `test_product_model_offline.py` (6 cases incl. non-material env keys ignored, incomplete → not comparable). |
| Ranking — BEST_VERIFIED / HIGH_PERFORMING / PROMISING / INSUFFICIENT_EVIDENCE + conditional leaders + ties + never a fabricated/stored winner | **CLOSED** | `problem_leaderboard` computes on read from completed-evaluation lineage; `wilson_interval` lower bound; `TIE_EPSILON` → `current_best` is a LIST; `conditional_leaders` (reliability/cost/latency/first-pass). `test_product_model_e2e.py` asserts `current_best==[A]`, `state==BEST_VERIFIED`, and that the problem row carries no `winner`/`best_solution_id`. |
| Sample size — run_count retained, `100%, n=2` never dominates | **CLOSED** | `MIN_RUNS_FOR_RANKING` gate + `test_product_model_offline.py::test_small_n_perfect_rate_does_not_reach_best_verified` (`wilson_interval(2,2)` lower < BEST_VERIFIED_FLOOR → INSUFFICIENT_EVIDENCE). |
| find_best_way — NL goal → matched Problem → current best VERIFIED solution; "no verified solution yet" when none | **CLOSED** | `product_model.find_best_way` (words OR'd, ts_rank; match is a Problem, answer is that Problem's evidence leaderboard — never "best" from text similarity). REST `/v1/best-way`, MCP `find_best_solution`. `test_product_model_e2e.py` + `test_product_model_mcp_e2e.py`. |

## CLAIM GRAPH

| Requirement | State | Evidence |
|---|---|---|
| `relation` KeyError on real Postgres — investigated + fixed at the correct layer, not weakened, no fake relation | **CLOSED** | `bbf6ff0`. Root cause: the e2e read `e['relation']` on every edge, but `get_claim_graph_overview` emits two edge KINDS — `relation` (has it) and `similarity` (computed proximity, `weight`, no `relation`). Fix selects `kind=='relation'` first + adds asserts that similarity edges are well-formed. Canonical edge representation unchanged; page JS already branched on `l.kind`. |
| Claim-graph Postgres E2E | **CLOSED** | `test_claim_graph_overview_e2e.py` — 37 tests green vs Supabase (`002e6da` regression run + `bbf6ff0`). |
| Claim-graph MCP E2E | **CLOSED** | `test_claim_graph_mcp_e2e.py` passes vs Supabase (part of the same 37). |
| Claim graph kept DISTINCT from the procedural graph | **CLOSED** | No merge. `claim_graph_api` reads `knowledge_nodes`/`edges`; procedural graph is `problems`/`solutions`/`procedures`/`executions`. Audit §7 map. Existing viewer (`92a6eee`) preserved, not replaced. |

## SECURITY (release-critical)

| Requirement | State | Evidence |
|---|---|---|
| ChatGPT branch reconstruction — parent/child tree from `mapping`+`current_node`, active branch only | **CLOSED** | `209564a`. `_chatgpt_active_node_ids`: `None`→linear (behaviour unchanged), `[]`→branching+ambiguous→discussion-only (never a fabricated confirmation), non-empty→active branch. `historical_bootstrap.py` + `chat_history_import.py`. |
| — abandoned fabricated-tool sibling → no verified evidence | **CLOSED** | `test_s28_abandoned_fabricated_tool_sibling_contributes_no_verified_evidence` (×2 files). |
| — abandoned hedged sibling doesn't suppress a genuine active-branch confirmation | **CLOSED** | `test_s28_abandoned_hedged_sibling_does_not_suppress_active_branch_confirmation` (×2). |
| — normal linear conversation unchanged | **CLOSED** | `test_s28_linear_conversation_output_is_identical_with_or_without_tree_metadata` (×2). |
| — mixed conversation → per-step epistemic status preserved | **CLOSED** | `test_s28_mixed_active_branch_preserves_per_step_epistemic_status` (×2). |
| — ambiguous ancestry → conservative | **CLOSED** | `test_s28_ambiguous_ancestry_no_current_node_is_conservative` (×2). 51 passed (was 41), no gold test weakened. |
| Ingestion injection — untrusted docs as DATA (delimited + hierarchy + schema + semantic validation + conservative reject) | **CLOSED** | `47f4ffd`. `skill_ingestion.py`: `<untrusted_source>` fence (fence markers stripped from data), rewritten system prompt asserting only it is authoritative, `_validate_capability_statement` (exactly one CAPABILITY line or ABSTAIN, length/charset), semantic reject of trust-claims / ungrounded / meta-directives, fail-closed → capability stays NULL and the procedure lands `system_pending_review`. |
| — capability statement ≠ execution/trust authority | **CLOSED** | grep-verified: `capability_statement` consumed only by `semantic_projections.py` (embedding text) + `replay.py` (string-diff). Not read by `applicability`/`capabilities`/`verification_state`/`approval`/scope/execution. `capture_procedure` takes no trust arg; every row born `candidate`. |
| — regression set (benign / injection / manipulated response / legitimate imperative wording) | **CLOSED** | 4 named tests in `test_skill_ingestion_offline.py`; 86 passed, no regressions. |
| Private scope enforcement (Problems/Solutions) | **CLOSED** | `problems` carry `owner_id`/`visibility`/`scope_type`; reads thread `scope_predicates()`. Solutions inherit Problem visibility (`list_problem_solutions` gates on `get_problem(scope)` — precedent: task_graphs inherit plan scope). |
| Credential safety — no secrets in Postgres | **CLOSED (carried)** | Product model stores no credentials. Implementation registry (mig 33) `auth_requirements` holds only a `credential_ref`. No new secret-storing column added this wave. |

## IMPLEMENTATION REGISTRY

| Requirement | State | Evidence |
|---|---|---|
| Durable, addressable Implementation identity | **CLOSED (pre-existing, mig 33)** | `implementations` + `implementation_tasks`: identity `(name,provider,version)` UNIQUE; `kind` TEXT+CHECK (deterministic/tool/slm/frontier/human + wasm/computer_use/api); `status` + separate `verification_status`; provenance columns; `locator`/`invocation`/`input_schema`/`output_schema`/`requirements`/`auth_requirements`/`resource_requirements`. Audit §"existing impl/provider state". |
| Provider resolution — discover/inspect/execute, provider-neutral | **CLOSED (pre-existing)** | `app/execution/providers.py`, `implementation_registry.py`, `implementation_executor.py`. Not touched this wave (correctly — no duplication). |
| Harness execution descriptor (`{implementation_id, kind, provider, protocol, locator, invocation, …}`) | **PARTIALLY CLOSED** | All the fields exist as columns on `implementations` (mig 33). A dedicated `descriptor()` serializer that shapes exactly the §22/§27 ABI JSON is **not written** this wave. OPEN item. |
| Plan pinning — no mid-execution re-resolve of a mutable "current best" | **CLOSED (pre-existing + reinforced)** | mig 23 binds every execution to an exact plan version; `execution_plans` frozen by trigger. New durable-run pins `implementation_id`/`version` on `execution_run_nodes` at first resolve, never re-resolved on resume (`002e6da`). |
| Capability metadata — evidence-based, no brand argument | **CLOSED (pre-existing)** | `capabilities.py` Wilson LB from `evidence` rows; `evidence.target_type` already allows `implementation`. Ranking (`problem_leaderboard`) reuses `wilson_interval` — no model/brand input anywhere. |

## EXECUTION — durable retry / resume

| Requirement | State | Evidence |
|---|---|---|
| Durable per-node state (pending/running/succeeded/failed/blocked/cancelled/resumable) over the existing substrate, not an in-memory loop | **CLOSED** | `002e6da`. mig 36 `execution_run_nodes` (status CHECK, attempt_count, impl binding, side_effecting, error_class, verification_state, worker_id+lease). `execution_runs` is the mutable resumable unit beside append-only `executions`. |
| Not a second scheduler / queue | **CLOSED** | `durable_run.py` has no queue and no polling loop. Each node transition is its own short transaction; the `run_node` callback runs outside any transaction. Retry happens on the next `resume_run`/`retry_node` call. |
| Resume invariants — completed node not rerun; retry count durable; crash between nodes resumes; crash during node conservative; implementation pinned; deps enforced; double resume no duplicate side effects; resume idempotent; stale workers cannot mutate terminal state; failure stays visible | **CLOSED** | **§58 E2E** `test_durable_run_e2e.py::test_durable_retry_resume_crash_and_continue`: A succeeds → B crashes MID-NODE (`WorkerLost`) → state persisted (A succeeded, B running+expired lease, C pending) → resume with a NEW worker → A NOT re-run (callback log), B retries once and succeeds, C runs → run `succeeded` → 2nd resume is a no-op (no node re-run) → a stale worker's direct `UPDATE` of the succeeded node is rejected by `trg_ern_terminal_fence`. |
| Side-effect safety — park conservatively rather than blind rerun | **CLOSED** | `_drive`: a `running`+expired-lease node that is `side_effecting` → `resumable` + run `status='paused'`, returns a note that `retry_node()` needs an explicit decision. Pure nodes re-arm and retry. |
| Retry policy — explicit + bounded (retryable classes, max attempts, non-retryable) | **CLOSED** | `RETRYABLE_ERROR_CLASSES` vs `NON_RETRYABLE`; `classify_error` (12-case `test_durable_run_offline.py`); `max_attempts` default 3; unknown class → not retried (conservative). |
| Concurrent resume protection | **CLOSED** | `_claim_run` (worker_id + lease); a second worker under a live lease → `ResumeInProgress`. `test_concurrent_resume_is_refused_not_duplicated`. |
| Retry/resume API ops (inspect / resume / retry node / attempts history) | **PARTIALLY CLOSED** | Service functions exist (`run_status`, `resume_run`, `retry_node`) and are proven. A REST/MCP surface for them is **not** exposed this wave. OPEN item. |
| Evidence integrity across retry — no duplicate evidence, no trust inflation | **CLOSED** | Terminal fence prevents a re-run of a succeeded node; `_node_finish` writes result once; `execution_runs` appends at most one `executions` row on terminal. |
| Wire durable-run into the MCP `find_best_way` tier-2 executor | **OPEN** | The service is standalone + proven; the tier-2 executor still uses the in-memory `graph_executor`. Follow-on. |

## API / MCP

| Requirement | State | Evidence |
|---|---|---|
| REST — POST/GET `/v1/problems`, `/v1/problems/{id}`, `/{id}/{solutions,benchmarks,evaluations,leaderboard}`, `/v1/benchmarks/{id}`, POST `/v1/evaluations`, `/v1/evaluations/{id}` (+ find, best-way, freeze, associate, complete, invalidate) | **CLOSED** | `3229641` — `app/api/problems.py`, 17 routes, registered in `main.py`. Thin; delegates to `product_model`. `test_product_model_e2e.py` exercises the leaderboard + best-way legs over `httpx.ASGITransport`. |
| MCP — `find_problem`, `inspect_problem`, `list_problem_solutions`, `compare_solutions`, `inspect_evaluation`, `find_best_solution` converging on the shared service | **CLOSED** | `9040dfa` — 6 `@server.tool()`s in `mcp_server/server.py`. `test_product_model_mcp_e2e.py` — all 6 reachable through the server, correct shapes, REFUSED paths, `find_best_solution` returns the verified `current_best`. |
| REST ranking logic == MCP ranking logic (one service) | **CLOSED** | Both call `product_model.problem_leaderboard` / `find_best_way`. No ranking code in either router or the tools. |
| Existing APIs not broken | **CLOSED** | `test_product_model_*` router/app slice → 96–97 passed / 11–15 skipped; full offline collection 2378 tests, no collection errors. |
| MCP `find_best_way` (the HTN coding agent) unchanged | **CLOSED** | Not modified. The new `find_best_solution` is a distinct tool (answers "which known solution is measurably best", executes nothing); full convergence of the two is noted as a follow-on. |

## FRONTEND

| Requirement | State |
|---|---|
| Home / Search / Problem (benchmark+leaderboard) / Solution / Procedure / Task / Implementation / Evidence / Repository / Project / Personal / Auth / Claim-graph pages | **OPEN** — not started this wave. The existing `frontend/` (Next.js) is untouched. `frontendv1/` is an untracked parallel experiment, not on this branch. |
| Frontend critical E2E (§56) | **OPEN** |
| Performance architecture (small bundle, lazy graph, cached reads, debounced search) | **OPEN** |

## WEBMCP

| Requirement | State |
|---|---|
| `document.modelContext.registerTool` wrapper + feature-detect; 13 semantic tools over the domain API; live browser test | **OPEN** — not started. The backend domain services the WebMCP tools would call (`product_model`, `claim_graph_api`, retrieval) exist and are proven, so this is a frontend-integration unit. |

## TESTS

| Category | State | Evidence |
|---|---|---|
| Unit (offline) | **CLOSED (this wave's additions)** | `test_product_model_offline.py` (12), `test_durable_run_offline.py` (12), `test_s28_*` (10), `test_skill_ingestion_offline.py` injection set (4). |
| Integration / E2E vs real Postgres (Supabase) | **CLOSED (this wave's additions)** | `test_claim_graph_overview_e2e` + `_mcp_e2e` (37), `test_product_model_e2e` (§57, 1), `test_product_model_mcp_e2e` (1), `test_durable_run_e2e` (§58, 3). Hardening-file run: **112 passed / 1 failed→fixed (isolation) → re-run 1 passed**. |
| Migration — fresh DB + existing-DB upgrade, no checksum drift | **PARTIALLY CLOSED** | Migrations 01–37 applied clean to a fresh Supabase project (checksum ledger, `002e6da` + earlier). An explicit "apply against a populated V1 DB then diff" test is **not** written; the checksum-immutability discipline (a mismatch is a hard error in `migrate.py`) is the standing guard. |
| Security regression | **CLOSED** | §28 (10) + §29 (4) named regression tests, above. |
| Full offline regression gate (§63) | **CLOSED** | `cd backend && python -m pytest tests -q` (DATABASE_URL unset) → **2099 passed, 283 skipped, 0 failed, 14 warnings** in 4m48s, exit 0 (2026-09-03). The 283 skips are the `*_e2e.py` / `test_schema_drift.py` files self-skipping without a DB, as designed. The pre-existing `test_ingestion_admin_endpoint_e2e` red (audit-noted, trace→claim path, not the corpus/product path) only manifests with a live DB and is out of this wave's scope. |
| Performance sanity | **OPEN** — not exercised this wave. |

## DOCUMENTATION

| Doc | State |
|---|---|
| README / architecture / API / MCP / WebMCP / security / execution / retry-resume / Problem-Benchmark-Solution-Evaluation / claim-graph / ingestion / V1 lifecycle | **OPEN** — no doc files updated this wave. Each closed unit's commit message carries the design rationale; `.scratch/current-state-audit.md` + this matrix are the interim record. |

---

## Summary

| Bucket | CLOSED | PARTIALLY CLOSED | OPEN |
|---|---|---|---|
| Product model | 12 | 0 | 0 |
| Claim graph | 4 | 0 | 0 |
| Security (release-critical) | 10 | 0 | 0 |
| Implementation registry | 3 | 1 (harness descriptor serializer) | 0 |
| Execution retry/resume | 7 | 2 (retry/resume API surface; wire into MCP tier-2) | 1 (— counted in PARTIAL) |
| API / MCP | 5 | 0 | 0 |
| Frontend | 0 | 0 | 3 |
| WebMCP | 0 | 0 | 1 |
| Tests | 4 | 1 (migration-upgrade test) | 1 (perf sanity) |
| Documentation | 0 | 0 | 1 |

**Both release-critical defects (§28, §29) are CLOSED and proven. Every mandatory E2E
that is backend-resolvable — §57 product lineage, §58 retry/resume, §59 claim-graph —
is CLOSED against Supabase.** The OPEN column is the frontend (§34–48), WebMCP
(§49–54), and the docs/perf/migration-upgrade finish.
