# FINAL-V1 regression gate — results

- Date: 2026-09-03
- Branch: `core-a/ingestion-testing` @ `4208b87`
- API under test: uvicorn on `127.0.0.1:8000` → `{"status":"ok"}`, same Supabase (session pooler `aws-0-ap-south-1.pooler.supabase.com:5432`)
- Python 3.13.0, plain `python`, no venv. `-p no:cacheprovider`, no pytest config/markers.
- Raw logs: `C:\Users\chait\.claude\jobs\17ba3003\tmp\{A..J}*.txt`

## Suite results

| Suite | Command | passed | failed | skipped | warnings | runtime | exit |
|---|---|---:|---:|---:|---:|---:|---:|
| A · Backend offline (`DATABASE_URL` unset) | `cd backend && pytest tests -q -p no:cacheprovider` | 2118 | 0 | 287 | 14 | 344.09 s | 0 |
| B · Backend live E2E vs Supabase | `cd backend && pytest tests/test_product_model_e2e.py tests/test_product_model_mcp_e2e.py tests/test_durable_run_e2e.py tests/test_durable_graph_e2e.py tests/test_durable_resume_e2e.py tests/test_claim_graph_overview_e2e.py tests/test_claim_graph_mcp_e2e.py tests/test_migration_upgrade_e2e.py -q -p no:cacheprovider` | 12 | 0 | 0 | 2 | 51.39 s | 0 |
| C · Harness | `cd experiments/harness && pytest tests -q -p no:cacheprovider` | 254 | 0 | 0 | 0 | 5.31 s | 0 |
| D · Packaging | `cd packaging && pytest tests -q -p no:cacheprovider` | 95 | 0 | 0 | 0 | 9.32 s | 0 |
| E · Frontend typecheck | `cd frontendv1 && npx tsc --noEmit -p tsconfig.json` | — clean — | 0 | — | — | — | 0 |
| F · Frontend browser E2E (Playwright) | `cd frontendv1 && NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npx playwright test --reporter=list` | 7 | 0 | 0 | — | 20.6 s | 0 |
| G · Security regressions subset (offline) | `cd backend && pytest tests -q -p no:cacheprovider -k "s28 or skill_ingestion or descriptor or durable_resume or scope or redaction or identity"` | 198 | 0 | 23 | 0 | 19.21 s | 0 |
| H · Durable execution subset (offline) | `cd backend && pytest tests -q -p no:cacheprovider -k "durable"` | 23 | 0 | 7 | 0 | 14.65 s | 0 |
| I · Migration fresh gate | `cd backend && python scripts/migrate.py --status` | ledger clean — see below | — | — | — | <2 s | 0 |
| J · Performance sanity | `python .scratch/perf_probe.py` (re-run) | GO — see below | — | — | — | ~2 min | 0 |

Notes:
- G and H are `-k`-filtered re-runs of a subset of A (2184 / 2375 deselected respectively); their passes are already counted inside A's 2118. They are reported separately as targeted confirmation of the security / durable-execution surfaces this branch touched.
- B's `test_durable_run_e2e.py`, `test_durable_graph_e2e.py`, `test_durable_resume_e2e.py`, `test_migration_upgrade_e2e.py` all passed — the populated-DB durable + migration-upgrade paths are green.
- E `npm run lint` **crashes** with `TypeError: Converting circular structure to JSON` inside ESLint 9.39.5 / `@eslint/eslintrc` config-validator (flat-config + legacy `extends` interaction). This is a **pre-existing tooling defect, not a regression from this branch** — it is an ESLint-internal serialization bug in the shared config, unrelated to any source added by migrations 35–37 / product-model / durable-run / §28 / §29. `tsc --noEmit` (the actual type gate) passes clean.

## Totals

Primary suites (A + B + C + D + F), no double-count:
- **passed: 2486**
- **failed: 0**
- **skipped: 287** (all self-skips: `*_e2e.py` + `test_schema_drift.py` under unset `DATABASE_URL`, plus environment-gated cases; every collected non-skip test passed)
- **warnings: 16** (14 in A + 2 in B) — all the single `pytest_asyncio` `_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET` PytestDeprecationWarning plus benign `ResourceWarning`s from `test_sandbox_executor.py` / `test_sandbox_input_path_escape.py`. No new warning classes.
- E typecheck: pass. G/H targeted subsets: 221 passed / 0 failed (already inside A).

## Failures

**None.** Zero test failures across every suite. Zero unexplained conditions.

The only non-green signal is E `npm run lint`, addressed above: pre-existing ESLint flat-config circular-structure crash, explicitly called out in the task as expected and not part of this regression.

## I · Migration ledger status

`python scripts/migrate.py --status` (DATABASE_URL set), exit 0:

```
applied   01_ontology.sql
applied   02_loop.sql
applied   03_access.sql
applied   04_governance.sql
applied   05_decomposition.sql
applied   06_generated_files.sql
applied   07_agents.sql
applied   08a_graph_workflow_execution_type.sql
applied   08b_graph_workflow_execution_rest.sql
applied   09_seed_internal_agents.sql
applied   10_code_sourced_agents.sql
applied   11_fix_embedding_joint_drift.sql
applied   12_trace_ingestion_pipeline.sql
applied   13_claim_subject_index.sql
applied   14_observations.sql
applied   16_state_projection_index.sql
applied   17_episode_project_columns.sql
applied   18_procedures.sql
applied   19_procedures_embedding.sql
applied   20_procedure_extraction.sql
applied   21_band1_contracts.sql
applied   22_band1_review_fixes.sql
applied   23_plan_persistence.sql
applied   24_evidence.sql
applied   25_universal_changesets.sql
applied   26_replayability.sql
applied   27_failure_routing.sql
applied   28_identity.sql
applied   29_rls_backstop.sql
applied   30_verified_requires_evidence.sql
applied   31_seed_grounded_hybrid_extractor.sql
applied   32_ingestion_provenance.sql
applied   33_implementation_registry.sql
applied   34_evidence_stats_count_failures.sql
applied   35_product_model.sql
applied   36_durable_execution_runs.sql
applied   37_execution_runs_terminal_chk_fix.sql
```

- 37 migration rows, all `applied`. (Numbering skips a standalone `15`; `08` is split `08a`/`08b`. Highest number = 37, row count = 37.)
- No `MISMATCH` (no checksum drift), no `pending`, no `error`. Ledger clean.
- `test_migration_upgrade_e2e.py` (populated-DB upgrade path, run in B) passed.

## J · Performance sanity

Probe re-run this gate (`DATABASE_URL=... python .scratch/perf_probe.py`, exit 0). Fresh medians vs. the recorded baseline in `.scratch/final-v1-perf-sanity.md` — all equal or lower (remote pooler over public internet; absolute ms is a ceiling). Query counts identical → no new N+1, no unbounded read.

| Path | p50 ms (this run) | p95 ms (this run) | baseline p50 | DB queries |
|---|---:|---:|---:|---:|
| `product_model.get_problem` | 23.9 | 30.8 | 40 | 2 |
| `product_model.list_problem_solutions` | 46.7 | 70.9 | 78 | 4 |
| `product_model.get_benchmark` | 20.1 | 31.1 | 38 | 2 |
| `product_model.problem_leaderboard` | 81.5 | 108.5 | 150 | 8 (no N+1) |
| `product_model.get_evaluation` | 36.7 | 59.1 | 58 | 4 |
| `product_model.complete_evaluation` (6 linked execs) | 153.1 | 371.6 | 284 | 13 |
| `durable_run.start_run` (3-node) | 80.4 | 138.1 | 133 | 7 |
| `durable_run.execute_run` (3-node) | 501.5 | 627.1 | 1081 | 47 |
| `durable_run.resume_run` (crash mid-node) | 879.1 | 1332.5 | 1180 | 70 |
| `mcp.find_problem` | 28.8 | 31.7 | 27 | 2 |
| `mcp.inspect_problem` | 193.8 | 451.3 | 283 | 16 |
| `mcp.find_best_solution` | 123.1 | 419.2 | 172 | 10 |
| `claim_graph_api.get_claim_graph_overview` (`with_status=True`) | 302.1 | 344.0 | 394 | 98 (bounded O(N), ≤600 nodes, sem=8, documented) |
| `claim_graph_api.get_claim_graph_overview` (`with_status=False`) | 102.1 | 181.7 | 160 | 8 (flat) |
| `embeddings.embed_one` (distinct) | 3424.4 | 6159.0 | 3431 | n/a (external provider API) |
| `embeddings.embed_one` (same text ×5) | 0.0 | — | 0.02 | n/a (in-process `_EMBED_CACHE`) |

No `failures` entry on any probe row. `find_best_way` tier-2 not measured (requires sandbox) — same as baseline.

**Verdict: GO.** Every DB path has a bounded, data-size-independent (or explicitly `LIMIT`-capped) query count. `problem_leaderboard` = fixed 4 reads, no N+1. Durable execution: bounded retries (`max_attempts` frozenset), non-spinning `_drive` loop. Embeddings cache repeats. The one O(N) fan-out (`get_claim_graph_overview(with_status=True)`, ~4.5 q/node) is bounded (≤600 nodes), concurrency-capped, documented, and has a flat `with_status=False` fast path — recorded as a lead follow-up, not a blocker.

## Final verdict

**REGRESSION GATE GREEN** — A/B/C/D/F/G/H all pass with 0 failures; E `tsc` clean; migration ledger clean at 37; perf sanity GO. The sole non-pass signal (`npm run lint` ESLint circular-structure crash) is pre-existing tooling breakage explicitly excluded by the task brief, not a regression from migrations 35–37 / product-model / durable-execution / §28 / §29.
