# Final Integration + V1 Hardening — Acceptance Matrix (§61)

Branch `core-a/ingestion-testing`. Baseline `a5dace6` (`v1-baseline-2026-09-02`).
Hardening head `a404593`. Every row is **CLOSED / PARTIALLY CLOSED / OPEN** —
no "mostly", no "probably". Evidence = a commit SHA + a test that runs.

**Rewritten 2026-09-03 (freeze pass)** — the last PARTIALLY CLOSED row, the
frontend scripted browser E2E (§56), is now CLOSED via `2fae92c`
(`frontendv1/e2e/v1-flow.spec.ts`, 7 Playwright tests against the real
backend + real Supabase). **Every Final-V1 acceptance row is now CLOSED.**
`a404593` is freeze-prep hygiene only (dead imports, doc tool-count,
untrack build logs — no behaviour change).

Prior pass closed the non-frontend PARTIAL/OPEN items: `b966cd7` descriptor
+ tier-2 wiring, `a8bd940` retry/resume surface, `885d83a` migration
upgrade, `dfb793e` perf, `44c942c` docs; frontend lane landed `39e2892`.

Commits this wave: `bbf6ff0` `53c93f9` `209564a` `47f4ffd` `8aecc04` `ed958b2`
`3229641` `9040dfa` `002e6da` `0f17055` `320918a` `892f60c` `b966cd7` `39e2892`
`44c942c` `a8bd940` `885d83a` `dfb793e` `4208b87` `2fae92c` `a404593`.

DB target for all E2E: Supabase `wckeklqxmiglivfolujn` (ap-south-1 session pooler),
Postgres 17.6, migrations 01–37 applied.

---

## PRODUCT MODEL

| Requirement | State | Evidence |
|---|---|---|
| Problem — durable, statuses OPEN/ACTIVE/SOLVED/ARCHIVED, scope, private stays private | **CLOSED** | mig 35 (`8aecc04`); `problems` table + CHECKs; `product_model.create_problem/get_problem/list_problems`; scope via `scope_predicates()`. `test_product_model_offline.py`, `test_product_model_e2e.py`. |
| Benchmark — versioned, IMMUTABLE once used for a published result | **CLOSED** | mig 35 `benchmarks` + `trg_benchmark_frozen_immutable`; `freeze_benchmark()`. |
| Benchmark cases — smallest existing executable unit (Task), preserve input/expected/verification | **CLOSED** | mig 35 `benchmark_cases` = FK to `task_nodes` + `expected_outcome` + `verification_criteria` overlay. Not a new executable abstraction. |
| Solution — association only, types procedure/task_graph/task, NO target copied | **CLOSED** | mig 35 `solutions` (polymorphic `target_id`+`target_table`, type↔table CHECK); `associate_solution` validates the target via the right id column. `test_product_model_e2e.py`. Board note re: reversing the prior "no solutions table" decision. |
| Evaluation — aggregate over real executions+evidence, version-pinned, no raw payload duplication | **CLOSED** | mig 35 `evaluations` + `evaluation_executions`. `complete_evaluation` RECOMPUTES run_count + success/verified from linked executions & evidence — caller numbers ignored. |
| Evaluation lifecycle — untrusted caller cannot fabricate COMPLETED / verified_success_rate | **CLOSED** | `trg_evaluation_completed_has_lineage` + `complete_evaluation` raises on `execution_ids=[]`. `test_product_model_e2e.py` asserts the ValueError. |
| Lineage — a completed Evaluation points through Benchmark→Solution→pinned proc/impl→Execution(s)→Verification→Evidence | **CLOSED** | **§57 E2E** `test_product_model_e2e.py::test_product_model_lineage_and_current_best_are_derived_not_stored`. |
| Version pinning — evaluation records benchmark/solution/procedure/implementation versions | **CLOSED** | `evaluations.procedure_id`+`procedure_version`, `implementation_id`+`implementation_version`; `benchmarks.version`; `solutions.version`. |
| Comparability — `evaluations_comparable(a,b)` on benchmark version / verification semantics / material environment | **CLOSED** | `product_model.evaluations_comparable` → `(bool, reason)`. `test_product_model_offline.py` (6 cases). |
| Ranking — BEST_VERIFIED / HIGH_PERFORMING / PROMISING / INSUFFICIENT_EVIDENCE + conditional leaders + ties + never a fabricated/stored winner | **CLOSED** | `problem_leaderboard` computes on read; `wilson_interval` lower bound; `TIE_EPSILON` → `current_best` is a LIST; `conditional_leaders`. `test_product_model_e2e.py` asserts `current_best==[A]`, no `winner`/`best_solution_id` column. |
| Sample size — run_count retained, `100%, n=2` never dominates | **CLOSED** | `MIN_RUNS_FOR_RANKING` gate + `test_product_model_offline.py::test_small_n_perfect_rate_does_not_reach_best_verified`. |
| find_best_way — NL goal → matched Problem → current best VERIFIED solution; "no verified solution yet" when none | **CLOSED** | `product_model.find_best_way`; REST `/v1/best-way`, MCP `find_best_solution`. `test_product_model_e2e.py` + `test_product_model_mcp_e2e.py`. |

## CLAIM GRAPH

| Requirement | State | Evidence |
|---|---|---|
| `relation` KeyError on real Postgres — investigated + fixed at the correct layer, not weakened, no fake relation | **CLOSED** | `bbf6ff0`. Root cause: the e2e read `e['relation']` on every edge, but `get_claim_graph_overview` emits two edge KINDS — `relation` and `similarity` (computed proximity, `weight`, no `relation`). Fix selects `kind=='relation'` first + asserts similarity edges well-formed. Canonical edge representation unchanged. |
| Claim-graph Postgres E2E | **CLOSED** | `test_claim_graph_overview_e2e.py` green vs Supabase. |
| Claim-graph MCP E2E | **CLOSED** | `test_claim_graph_mcp_e2e.py` vs Supabase. |
| Claim graph kept DISTINCT from the procedural graph | **CLOSED** | No merge. `claim_graph_api` reads `knowledge_nodes`/`edges`; procedural graph is `problems`/`solutions`/`procedures`/`executions`. Existing viewer (`92a6eee`) preserved. |

## SECURITY (release-critical)

| Requirement | State | Evidence |
|---|---|---|
| ChatGPT branch reconstruction — parent/child tree from `mapping`+`current_node`, active branch only | **CLOSED** | `209564a`. `_chatgpt_active_node_ids`: `None`→linear, `[]`→branching+ambiguous→discussion-only, non-empty→active branch. |
| — abandoned fabricated-tool sibling → no verified evidence | **CLOSED** | `test_s28_abandoned_fabricated_tool_sibling_contributes_no_verified_evidence` (×2 files). |
| — abandoned hedged sibling doesn't suppress a genuine active-branch confirmation | **CLOSED** | `test_s28_abandoned_hedged_sibling_does_not_suppress_active_branch_confirmation` (×2). |
| — normal linear conversation unchanged | **CLOSED** | `test_s28_linear_conversation_output_is_identical_with_or_without_tree_metadata` (×2). |
| — mixed conversation → per-step epistemic status preserved | **CLOSED** | `test_s28_mixed_active_branch_preserves_per_step_epistemic_status` (×2). |
| — ambiguous ancestry → conservative | **CLOSED** | `test_s28_ambiguous_ancestry_no_current_node_is_conservative` (×2). 51 passed (was 41). |
| Ingestion injection — untrusted docs as DATA | **CLOSED** | `47f4ffd`. `<untrusted_source>` fence, rewritten system prompt, `_validate_capability_statement`, semantic reject of trust-claims / ungrounded / meta-directives, fail-closed → capability stays NULL, procedure lands `system_pending_review`. |
| — capability statement ≠ execution/trust authority | **CLOSED** | grep-verified: `capability_statement` consumed only by `semantic_projections.py` + `replay.py`. Not read by `applicability`/`capabilities`/`verification_state`/`approval`/scope/execution. Every row born `candidate`. |
| — regression set (benign / injection / manipulated response / legitimate imperative wording) | **CLOSED** | 4 named tests in `test_skill_ingestion_offline.py`; 86 passed. |
| Private scope enforcement (Problems/Solutions) | **CLOSED** | `problems` carry `owner_id`/`visibility`/`scope_type`; reads thread `scope_predicates()`. Solutions inherit Problem visibility. Re-verified post-upgrade by `test_migration_upgrade_e2e.py`. |
| Cross-user execution-run mutation (resume/retry) | **CLOSED** | `a8bd940`. `authorize_run_mutation` raises `NotYourRun` (403 / `REFUSED`) iff both the resolved caller identity and `execution_runs.created_by` are known and differ. `test_durable_resume_e2e.py::test_a_different_resolved_identity_cannot_resume_or_retry_another_users_run`; `test_durable_resume_offline.py` (auth gate). |
| Descriptor secret leakage | **CLOSED** | `b966cd7`. `_sanitize_auth` → inline secret string becomes `{"redacted": true}`, refs kept, recurses one level. `test_implementation_descriptor_offline.py` (token / client_secret / nested oauth; no-drift). |
| Credential safety — no secrets in Postgres | **CLOSED** | No new secret-storing column. Implementation registry `auth_requirements` holds only a `credential_ref`. `.env` / service-role key not committed; `frontendv1/.env.local.example` = placeholders only. |

## IMPLEMENTATION REGISTRY

| Requirement | State | Evidence |
|---|---|---|
| Durable, addressable Implementation identity | **CLOSED (pre-existing, mig 33)** | `implementations` + `implementation_tasks`: identity `(name,provider,version)` UNIQUE; `kind` TEXT+CHECK; `status` + separate `verification_status`; provenance; `locator`/`invocation`/`input_schema`/`output_schema`/`requirements`/`auth_requirements`/`resource_requirements`. |
| Provider resolution — discover/inspect/execute, provider-neutral | **CLOSED (pre-existing)** | `app/execution/providers.py`, `implementation_registry.py`, `implementation_executor.py`. Not duplicated this wave. |
| Harness execution descriptor (`{implementation_id, kind, provider, protocol, locator, invocation, …}`) | **CLOSED** | `b966cd7`. `implementation_registry.descriptor(row)` — pure fn, `DESCRIPTOR_VERSION="impl-descriptor/1"`, stable field order, deterministic, missing optionals → `{}`, validates `kind ∈ REGISTRABLE_KINDS` (the existing DB CHECK vocab — no duplicate vocabulary). `_protocol_for` derivation. MCP `inspect_implementation` / `resolve_implementation` return it; REST `GET /v1/implementations/{id}/descriptor` (404 for missing/invisible). `test_implementation_descriptor_offline.py` — 7 passed. |
| Plan pinning — no mid-execution re-resolve of a mutable "current best" | **CLOSED (pre-existing + reinforced)** | mig 23 binds every execution to an exact plan version; `execution_plans` frozen by trigger. Durable-run pins `implementation_id`/`version` on `execution_run_nodes` at first resolve, never re-resolved on resume (`002e6da`); `trg_ern_terminal_fence` blocks rewriting a succeeded node's binding. |
| Capability metadata — evidence-based, no brand argument | **CLOSED (pre-existing)** | `capabilities.py` Wilson LB from `evidence` rows; ranking (`problem_leaderboard`) reuses `wilson_interval` — no model/brand input anywhere. |

## EXECUTION — durable retry / resume

| Requirement | State | Evidence |
|---|---|---|
| Durable per-node state over the existing substrate, not an in-memory loop | **CLOSED** | `002e6da`. mig 36 `execution_run_nodes` (status CHECK, attempt_count, impl binding, side_effecting, error_class, verification_state, worker_id+lease). `execution_runs` is the mutable resumable unit beside append-only `executions`. |
| Not a second scheduler / queue | **CLOSED** | `durable_run.py` has no queue and no polling loop. Each node transition is its own short transaction; the `run_node` callback runs outside any transaction. |
| Resume invariants — completed node not rerun; retry count durable; crash between/within nodes resumes; implementation pinned; deps enforced; double resume no duplicate side effects; resume idempotent; stale workers cannot mutate terminal; failure stays visible | **CLOSED** | **§58 E2E** `test_durable_run_e2e.py::test_durable_retry_resume_crash_and_continue` + **§3 E2E through the wired helper** `test_durable_graph_e2e.py::test_run_graph_durably_creates_durable_run_and_resumes_through_the_same_helper`: A succeeds → B crashes MID-NODE (`WorkerLost`) → state persisted, **0** executions rows → resume via `run_graph_durably(resume_run_id=…)` → A NOT re-run, B retries (attempt_count 2, first_pass False), C runs → `succeeded` → 2nd resume no-op → stale worker's `UPDATE` of the succeeded node rejected by `trg_ern_terminal_fence`. Exactly ONE immutable `executions` row pinned to procedure_id/v1. |
| Side-effect safety — park conservatively rather than blind rerun | **CLOSED** | `_drive`: a `running`+expired-lease node that is `side_effecting` → `resumable` + run `status='paused'` + a note that `retry_node()` needs an explicit decision. Pure nodes re-arm and retry. |
| Retry policy — explicit + bounded | **CLOSED** | `RETRYABLE_ERROR_CLASSES` vs `NON_RETRYABLE`; `classify_error` (12-case `test_durable_run_offline.py`); `max_attempts` default 3; unknown class → not retried. |
| Concurrent resume protection | **CLOSED** | `_claim_run` (worker_id + lease); a second worker under a live lease → `ResumeInProgress`. `test_concurrent_resume_is_refused_not_duplicated`. |
| Retry/resume API ops (inspect / resume / retry node / attempts history) | **CLOSED** | `a8bd940`. `app/execution/durable_resume.py` (context-free entrypoints, **zero** retry logic — rebuilds `CompiledPlan`+`deps` from persisted rows, dispatches nodes through the real `implementation_executor`) + REST `app/api/runs.py` (`GET /v1/runs/{id}`, `/nodes`, `POST …/resume`, `…/nodes/{order}/retry?force=`) + MCP (`inspect_run`, `resume_execution_run`, `retry_run_node`). A coding-agent / no-provider run returns `{"status":"needs_product_context",…}` — never a fake success. `test_durable_resume_offline.py` (11), `test_durable_resume_e2e.py` (3, vs Supabase). |
| Evidence integrity across retry — no duplicate evidence, no trust inflation | **CLOSED** | Terminal fence prevents a re-run of a succeeded node; `_node_finish` writes result once; `_finalize` appends at most one `executions` row on terminal (impl id pinned). The manual `record_plan_execution` calls in `server.py` tier-2 / `reproduce_procedure` were DELETED so a durable run records exactly once. |
| Wire durable-run into the MCP `find_best_way` tier-2 executor | **CLOSED** | `b966cd7`. `app/execution/durable_graph.run_graph_durably` is the single bridge; `find_best_way` tier-2 and `reproduce_procedure._run_tier` now call it instead of `execute_task_graph`; `find_best_way` gained `resume_run_id`. `_respond_tier1_hit` deliberately stays on the in-memory pass (non-stateful reasoning). `test_durable_graph_e2e.py` — 1 passed vs Supabase. |

## API / MCP

| Requirement | State | Evidence |
|---|---|---|
| REST — `/v1/problems` family (+ find, best-way, freeze, associate, complete, invalidate) | **CLOSED** | `3229641` — `app/api/problems.py`, 17 routes. `test_product_model_e2e.py`. |
| MCP — `find_problem`, `inspect_problem`, `list_problem_solutions`, `compare_solutions`, `inspect_evaluation`, `find_best_solution` on the shared service | **CLOSED** | `9040dfa`. `test_product_model_mcp_e2e.py` — all 6 reachable, correct shapes, REFUSED paths. |
| REST ranking logic == MCP ranking logic (one service) | **CLOSED** | Both call `product_model.problem_leaderboard` / `find_best_way`. |
| Retry/resume exposed on BOTH REST and MCP over one service | **CLOSED** | `a8bd940` — both surfaces delegate to `app.execution.durable_resume`; no retry logic in either. |
| Existing APIs not broken | **CLOSED** | Full offline regression gate (below). |
| MCP `find_best_way` (the HTN coding agent) semantics unchanged except durable execution | **CLOSED** | Only tier-2 execution swapped to `run_graph_durably` + a `resume_run_id` param added; retrieval / debate / plan-compile unchanged. `find_best_solution` remains a distinct tool. |

## FRONTEND

| Requirement | State | Evidence |
|---|---|---|
| Home / Search / Problem (benchmark+leaderboard) / Solution / Procedure / Task / Implementation / Claim / Evaluation / Repository / Project / Personal / Auth pages | **CLOSED** | `39e2892` (frontend lane). `frontendv1/` — Next 16 + React 19, 52 tracked files, `src/app/**/page.tsx`, single typed client `src/lib/api/{client,types}.ts`, OIDC + dev-viewer auth. |
| Problem benchmark hero — current-best / tie / open, backend-ranked leaderboard | **CLOSED** | `frontendv1/src/components/leaderboard.tsx`, `src/app/problems/[id]/page.tsx` — consumes `problem_leaderboard`; no client-side ranking. |
| Frontend critical E2E (§56) | **CLOSED** | `2fae92c` — `frontendv1/e2e/v1-flow.spec.ts` (Playwright, **7 passed**) drives the real Next frontend against the real FastAPI backend + real Supabase (no mock server). Asserts: app loads + `/health`; viewer identity (`X-Viewer-Id`) on `/v1` requests; Problems list from `GET /v1/problems`; Problem detail renders the benchmark name, "2 candidate solutions", the leaderboard `<table>` + backend Wilson-LB copy, and the backend-derived "Current best verified" hero (93.3% / n=30); Evaluation detail shows backend-recomputed n=30 + verified 93.3%; full home→problems→problem→evaluation click-path; every `/v1` request stays on the configured backend origin and matches no `mock\|fixture\|stub\|fake`. Seed: `frontendv1/e2e/seed_v1_flow.py` (real `product_model` write paths → `current_best == [Solution A]`). The stale Problems list-page stub was wired to `GET /v1/problems` in the same commit. |
| Performance architecture (small bundle, lazy graph, cached reads, debounced search) | **CLOSED** | `frontendv1/src/components/search-box.tsx` (debounced), skeleton loaders, per-route code splitting (Next app router). |

## WEBMCP

| Requirement | State | Evidence |
|---|---|---|
| `document.modelContext.registerTool` wrapper + feature-detect; semantic tools over the domain API; schema validation | **CLOSED** | `39e2892` — `frontendv1/src/webmcp/{registry,schemas,tools,validate}.ts` + `src/components/webmcp-provider.tsx`, `src/app/webmcp/page.tsx` (13 semantic tools). Feature-detects `document.modelContext`; tools call the same typed API client. Browser proof: `2fae92c` `v1-flow.spec.ts` asserts `/webmcp` feature-detects and renders all 13 expected tools. |

## TESTS

| Category | State | Evidence |
|---|---|---|
| Unit (offline) | **CLOSED** | `test_product_model_offline.py` (12), `test_durable_run_offline.py` (12), `test_durable_resume_offline.py` (11), `test_implementation_descriptor_offline.py` (7), `test_s28_*` (10), `test_skill_ingestion_offline.py` injection set (4). |
| Integration / E2E vs real Postgres (Supabase) | **CLOSED** | `test_claim_graph_overview_e2e` + `_mcp_e2e`, `test_product_model_e2e` (§57), `test_product_model_mcp_e2e`, `test_durable_run_e2e` (§58, 3), `test_durable_graph_e2e` (§3, 1), `test_durable_resume_e2e` (3), `test_migration_upgrade_e2e` (1). Live run: see §11 in `final-v1-production-readiness.md`. |
| Migration — fresh DB + existing-DB upgrade, no checksum drift | **CLOSED** | `885d83a`. `test_migration_upgrade_e2e.py` — throwaway PG17 cluster: apply 01..34, write a pre-hardening dataset via the real write paths, apply 35/36/37 via the real `migrate.py`, assert no checksum drift + every pre-existing row byte-for-byte unchanged + scope still enforced + new product-model & durable-run tables work against the pre-existing rows + no destructive DDL. 1 passed. |
| Security regression | **CLOSED** | §28 (10) + §29 (4) + descriptor secret redaction (in the 7) + cross-user run auth (offline + e2e) named tests. |
| Full offline regression gate (§63) | **CLOSED** | 2026-09-03 freeze run, exit 0: backend offline **2118 passed / 287 skipped / 0 failed**; live E2E vs Supabase **12 passed**; harness **254**; packaging **95**; frontend `tsc` clean; browser E2E **7 passed**. Total **2486 passed / 0 failed / 287 skipped**. Detail: `.scratch/final-v1-regression-results.md`. |
| Performance sanity | **CLOSED** | `dfb793e`. `.scratch/final-v1-perf-sanity.md` — probe vs live Supabase, N=20/path, 0 failures, **GO**; leaderboard N+1 check = none (8 fixed queries); no unbounded retry / repeated embed. `.scratch/perf_probe.py` + `perf_results.json`. |

## DOCUMENTATION

| Doc | State | Evidence |
|---|---|---|
| README / architecture / MCP / retry-resume / Problem-Benchmark-Solution-Evaluation / claim-graph / ingestion / V1 lifecycle | **CLOSED** | `44c942c`. New `docs/final-v1.md` (SHIPPED / KNOWN LIMITATIONS / POST-V1 — three separated sections). "Final-V1 update" sections added to `README.md`, `ARCHITECTURE.md`, `commLLM.md`, `backend/README_MCP_SERVER.md`; stale statements fixed ("execution is in-memory", "evaluation is a testing artifact", old `frontend/` as V1 surface, MCP tool count). Frozen-doc drift (`schema.md` missing mig 35/36 tables; the reversed "no solutions table" decision) recorded in the commit message, NOT edited (hard rule 3). |

---

## Summary

| Bucket | CLOSED | PARTIALLY CLOSED | OPEN |
|---|---|---|---|
| Product model | 12 | 0 | 0 |
| Claim graph | 4 | 0 | 0 |
| Security (release-critical) | 12 | 0 | 0 |
| Implementation registry | 5 | 0 | 0 |
| Execution retry/resume | 9 | 0 | 0 |
| API / MCP | 6 | 0 | 0 |
| Frontend | 4 | 0 | 0 |
| WebMCP | 1 | 0 | 0 |
| Tests | 6 | 0 | 0 |
| Documentation | 1 | 0 | 0 |

**Every Final-V1 acceptance item is CLOSED — no PARTIALLY CLOSED, no OPEN.**
Both release-critical defects (§28, §29) are CLOSED and proven. Every
mandatory E2E — §3 durable tier-2, §56 frontend browser flow, §57 product
lineage, §58 retry/resume, §59 claim-graph, migration upgrade — is CLOSED
against real Postgres / Supabase.

Carried (unchanged from the frozen baseline, not introduced or regressed
this wave; recorded for founder review, not a Final-V1 blocker):
`apply_change_set` is an ungated raw write primitive that is present in the
public MCP registry — CLAUDE.md's "opt-in flag, not public" posture is not
enforced by a flag today. Approval + audit still come from
`submit_approval` / `decide_decomposition`, never from `apply_change_set`.
