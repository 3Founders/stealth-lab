# Ideal V1 — Final Acceptance Matrix

Branch: `core-a/ideal-v1-closure` · baseline before this pass: `04d0aa2` ·
closure commit: `e954058`.

For each requirement: the normal user entrypoint (no internal-script
dependency), the real production call path, the private/public storage
boundary it must respect, the persisted result, and the live proof.

---

## P0-1 — normal user work → PRIVATE personal learning

| Field | Value |
|---|---|
| Requirement | Automatic ongoing learning reaches the PRIVATE `LocalProcedureStore`, not the shared Postgres substrate. Raw local traces are never uploaded to global just because auto-learning is on. |
| Normal user entrypoint | App runs (`uvicorn app.main:app`). `INGESTION_AUTO_MODE=local` (the default) → the lifespan starts the loop. No script, no curl, no manual episode/procedure insert. |
| Production call path | `main.lifespan` → `ingestion_scheduler.start` → `_loop` → `_run_local_tick` → `local_learning_sweep.run_local_learning_sweep(store, <workspace>/.claude/traces)` → `parse_claude_code_trace_file` → `converge_episode` → `LocalProcedureStore.capture_local_procedure`. |
| Storage boundary | Local SQLite (`<workspace>/.stealthlab/local_procedures.db`). DB-free: `test_local_learning_sweep_offline.py::test_sweep_makes_no_db_connection` monkeypatches `asyncpg.connect`/`create_pool` to raise and the sweep still captures. Zero write to Postgres `procedures`. |
| Persistent result | A `local_procedures` row, `verification_state='candidate'`, `staleness='fresh'`, `evidence_refs[].privacy='local'`, `evidence_refs[].source_type='claude_code_trace'`, `evidence_status='executed'`. |
| Live proof | `test_local_learning_sweep_offline.py` (6): captures from a real transcript, idempotent across ticks, bounded by `max_sessions`, discussion-only marked-not-recaptured, missing dir clean no-op, no DB connection. `test_ingestion_scheduler_offline.py::test_local_mode_tick_writes_private_candidates_from_real_traces` proves the DEFAULT loop path and that `process_ingestion` is NOT called. Canonical E2E Stage 3. |
| Status | **CLOSED** (offline + scheduler-loop proof). Canonical Stage 3 pending live-gate confirmation. |

## P0-2 — git history in the ONE bootstrap command

| Field | Value |
|---|---|
| Requirement | `--repo` processes repo procedural docs **and** git/code history in one command; all sources converge into one `LocalProcedureStore` with provenance preserved; conservative git rules; candidates begin as candidates. |
| Normal user entrypoint | `python scripts/bootstrap.py --repo-root <repo> [--claude-export …] [--chatgpt-export …] [--traces-dir …] --workspace <dir>`. One command. `scripts/bootstrap_local_memory.py` removed (was a second CLI). |
| Production call path | `scripts/bootstrap.py::main_async` → (`run_repo_docs_private` → `repo_docs_bootstrap.bootstrap_repo_docs`) **and** (`run_git_history` → `git_history_bootstrap.bootstrap_git_history`) → `LocalProcedureStore`. Library path: `historical_bootstrap.run_bootstrap(repo_root=…)` now also calls `bootstrap_git_history` and folds counts into `summary["git"]`. |
| Storage boundary | Local SQLite. `git_history_bootstrap` imports no asyncpg/app.db. |
| Persistent result | `local_procedures` row, `scope.git_history = {repo, pattern, files}`, `evidence_refs[].source_type='git_history'`, `evidence_status='recommended'` (a commit proves a diff landed, never a passing test), `verification_state='candidate'`. Bare commits → `skipped_bare_commits`, counted in the report. |
| Live proof | `test_historical_bootstrap_offline.py::test_p0_2_git_history_flows_through_the_single_run_bootstrap` — real temp git repo (fix commit → co-located test commit), one `run_bootstrap` call, candidate lands with `scope.git_history`, `source_type={'git_history'}`, `candidate`, retrievable. `commits_scanned == 3`. Canonical E2E Stage 1. |
| Status | **CLOSED**. |

## P0-3 — historical chat evidence semantics

| Field | Value |
|---|---|
| Requirement | A recommendation ("you could run pytest") is never upgraded to EXECUTED by an unrelated later success sentence. Mixed conversations keep per-step epistemic status. Historical executed → candidate, never verified. |
| Normal user entrypoint | Same `scripts/bootstrap.py` (chat exports) — routes through `chat_history_import.import_chat_history`. Library path: `historical_bootstrap.parse_claude_export` / `parse_chatgpt_export`. Both fixed. |
| Production call path | `chat_history_import`: `classify_message_evidence` (per message) → `_attributed_levels` (clamps an outcome to "discussion" unless a real command in this conversation can be attributed to it, and never past a hedge) → `extract_candidates_from_conversation`. `historical_bootstrap`: `_steps_from_message_texts` (same discipline — same-message or one-step-lookback from a non-hedged block). |
| Storage boundary | Local SQLite. Per-step status in `steps[].properties.evidence_status` / `.evidence_level`; `evidence_ref.step_statuses` breakdown. |
| Persistent result | Candidate only when real attributable work exists; `verification_state='candidate'` regardless of evidence level (`capture_local_procedure` is always-candidate). |
| Live proof | `test_chat_history_import_offline.py` P0-3 cases 1–4 (recommendation+unrelated success → no candidate; command+matching outcome → candidate; discussion → none; mixed → each step keeps status). `test_historical_bootstrap_offline.py` same 4 cases ×2 parsers + a unit test that a late "pytest passed" cannot reach past "you could run `pytest`". |
| Status | **CLOSED**. |

## P1-1 — documentation freeze

| Field | Value |
|---|---|
| Requirement | Docs describe shipped V1; stale claims reconciled ("ingestion stops at observations", "manual", old tool/endpoint counts, HTN, method library); SHIPPED V1 vs POST-V1 distinguished; README answers the new-user questions with no hidden internal-script dependency. |
| Deliverable | `README.md` — new frozen "StealthLab V1 — what ships" section: install, connect an agent, the one bootstrap command (incl. git), where private memory lives, automatic local learning, candidate maturation, failure ≠ success, staleness, UNKNOWN fails closed, publish (no copied verification count), User B independent reuse, privacy isolation, plus "Not in V1". Corrected the stale "ingestion … does not go further yet / run a script" paragraph. |
| Status | **CLOSED for README** (the directive's "especially clear" target). `proj_status.md` / `commLLM.md` broader reconciliation: partial — the V1 section is the authoritative new-user statement; remaining historical docs are already flagged historical in CLAUDE.md's doc map. |

---

## Canonical lifecycle (10 stages) & release gate

Run against a **fresh disposable database** (`stealthlab_v1_gate`), migrations 01→34 applied clean (proves the fresh-migration gate).

| Stage | What | Backing proof |
|---|---|---|
| 1 Cold start | one bootstrap command over repo+git+claude+chatgpt+traces → one private library | `test_historical_bootstrap_offline.py` (full run summary + git-through-run_bootstrap); `scripts/bootstrap.py` orchestrator |
| 2 Personal reuse | local retrieval → local applicability → selection → local execution | `test_local_retrieval_e2e.py`, `test_local_applicability_*`, `test_unified_retrieval_*` |
| 3 New learning | real work → trace → candidate in private memory, no manual endpoint | `test_local_learning_sweep_offline.py`, `test_ingestion_scheduler_offline.py` local-mode tick |
| 4 Maturation | distinct-context successes → verified via genuine evidence | `test_capabilities_e2e.py`, `test_band1_9a_evidence.py` (invariant #3 gate) |
| 5 Generalization | multiple episodes → generalized procedure, provenance kept | `test_merge_cluster.py`, `test_procedure_dedup_e2e.py`, `merge_duplicate_procedures` |
| 6 Staleness | precondition/env change → stale/inapplicable → selection changes | `test_applicability_e2e.py`, `test_tms_readability_e2e.py`, `mark_procedure_stale` |
| 7 Publish | private → scrub → global candidate, no copied local verification count | `publish.py` + `test_*publish*`; scrub in `trace_redaction` / `publish` |
| 8 User B | global candidate → User B retrieval → applicability → execution → independent evidence | `test_second_user_global_reuse_e2e.py` |
| 9 Privacy | User B cannot see User A private git/repo/chat/trace/unshared material | `test_hardening_h2_rls_backstop.py`, `scope_predicates`, migration 29 |
| 10 Failure | failure → evidence → failure route → trust/capability does not increase | `test_band2_4_*`, migration 34 (recorded failure lowers P) |

### Gate run log

Branch `core-a/ideal-v1-closure` @ `f4683f7`.

| Gate | Result |
|---|---|
| Fresh migration (01→34, bare disposable DB `stealthlab_v1_gate`) | ✅ 34/34 applied clean, checksums OK. Repeated on a 2nd bare DB (`sl_baseline_gate`) — identical. |
| Upgrade migration (existing shared DB: pending 34 → applied) | ✅ `applying 34_evidence_stats_count_failures.sql … applied`, additive `CREATE OR REPLACE VIEW`. |
| Full offline suite (`DATABASE_URL` unset) | ✅ **2050 passed, 275 skipped, 0 failed**. |
| Full live suite (fresh disposable DB, deterministic order) | **2319 passed, 5 failed, 1 skipped.** Failure triage below. |
| Bootstrap / git / historical-evidence / automatic-learning / local-applicability / generalization / privacy / MCP-identity / lifecycle-stage e2e (run on a fresh isolated DB, in their own file groups) | ✅ all green in isolation. |

**Live-suite failure triage** (directive: reproduce + demonstrate unrelated):

1. `test_env_guard_offline.py::test_load_dotenv_never_leaves_database_url_behind`
   — the test's own assertion message: *"test assumes no ambient DATABASE_URL
   in this run"*. It is an offline test; it PASSES in the offline suite
   (counted in the 2050). Harness artifact of running the whole suite with
   `DATABASE_URL` exported, not a defect.

2. `test_task_api_e2e.py::test_task_detail_and_personal_contributions_against_real_postgres`
   — `assert 3 == 2`. **Real, on this branch**: migration 34 makes a recorded
   failure count in the capability stream. **FIXED** in `f4683f7` (2 → 3,
   stale comment rewritten). Verified: `test_task_api_e2e.py` 1 passed on the
   shared DB; the 4-file capability/retrieval/solution/task e2e group 20
   passed together on a fresh isolated DB.

3–5. `test_local_retrieval_e2e.py::test_call_graph_ranked_names_boosts_semantic_tier_ranking`,
   `test_solution_search_e2e.py::test_search_solutions_blends_a_real_procedure_and_a_real_task`,
   `test_solution_search_e2e.py::test_rest_solutions_search_route_end_to_end_against_real_db`
   — **pre-existing full-suite test-isolation pollution, demonstrated unrelated:**
   • `git diff --name-only main...HEAD` — this branch touches **none** of these
     test files nor `local_retrieval.py` / solution-search code / `implementations.py`.
   • They **PASS in isolation** and **PASS as their own e2e-file group** on a
     fresh migrated DB — on this branch AND on baseline `04d0aa2` (checked via
     `git worktree` + a 3rd disposable DB).
   • They fail only at the tail of the full 2319-test deterministic run: an
     earlier test leaves shared mutable Postgres state (an implementation-
     registry row / call-graph rows) these non-self-seeding e2e tests then
     read. Same on baseline. This is a repo-wide test-isolation gap, not a
     V1-closure regression.

**Net after the `f4683f7` fix:** offline 100% green; migration chain clean
fresh + upgrade; every canonical-lifecycle stage green on a fresh DB in
isolation; the only remaining live-suite reds are 1 harness artifact + 3
pre-existing, file-diff-disjoint, isolation-order failures reproducible on
`04d0aa2`.

### What stands between here and an unqualified "READY"

1. Repo-wide live-suite test isolation: find the earlier test that leaves
   implementation-registry / call-graph rows and add teardown (or make the
   3 e2e tests self-seed). Pre-existing; out of the 4 named items' scope.
2. A single consolidated `test_ideal_v1_lifecycle_e2e.py` running all 10
   stages in one flow. Stages are each proven by existing e2e tests run on
   a fresh DB here; the one-file version was not authored this pass.

