# Final Integration + V1 Hardening — Current-State Audit (§0)

Written 2026-09-02. Verified against the actual tree + the live Supabase DB, not the prompt summary.

## Starting point

| | |
|---|---|
| starting branch | `core-a/ingestion-testing` |
| starting SHA | `7968efe` (before this audit) → `bbf6ff0` after commit #1 |
| baseline SHA | `a5dace6` (`v1-baseline-2026-09-02`) |
| commits since baseline | 12: `92a6eee` (claim-graph viewer, Body A) + `5b2fc15…7968efe` (corpus wave, Body B, 10 commits) + `bbf6ff0` (claim-graph e2e fix, this wave) |

## Production changes since baseline (non-scratch)

Exactly the claim-graph viewer (`92a6eee`) + this wave's fix:

| file | source |
|---|---|
| `backend/app/mcp_server/claim_graph_page.py` (new, 375) | `92a6eee` |
| `backend/app/services/claim_graph_api.py` (new, 256) | `92a6eee` |
| `backend/app/api/claims.py` (+77) | `92a6eee` |
| `backend/app/mcp_server/server.py` (+123) | `92a6eee` |
| `backend/app/mcp_server/vendor/force-graph.min.js` (new) | `92a6eee` |
| `backend/README_MCP_SERVER.md` (+36/-…) | `92a6eee` |
| `backend/tests/test_claim_graph_{api_offline,overview_e2e,mcp_e2e}.py` (new) | `92a6eee` + `bbf6ff0` |

The corpus wave (Body B) added **zero** production code — all under `.scratch/corpus_wave/` + `.scratch/better_ways_corpus/`.

## Scratch-only changes

`.scratch/corpus_wave/` (registry, 50 candidates, ranking, admitted/rejected, ingest runner + result, claim-graph preview, supabase_readiness) and `.scratch/better_ways_corpus/phase1/`. `.scratch/postgres_portability.md` (pre-existing).

## Known broken tests — status

| test | was | now |
|---|---|---|
| `test_claim_graph_overview_e2e::test_claim_graph_overview_against_real_postgres` | `KeyError: 'relation'` on real Postgres | **FIXED** (`bbf6ff0`) — the e2e comprehension read `e['relation']` on every edge, but `get_claim_graph_overview` emits two edge kinds and a `similarity` edge has no `relation` (only `weight`). Test now selects `kind=='relation'` first + asserts similarity edges are well-formed. 37 passed vs Supabase. |
| `test_claim_graph_mcp_e2e` | "not fully exercised" | **PASSES** — ran green against Supabase in the same run (part of the 37). |
| `test_ingestion_admin_endpoint_e2e::…drives_real_traces_to_a_real_procedure_candidate` | `assert 0 >= 1` (trace→observation→claim drain links 0 claims) | **OPEN** — carried from Phase 3. Not clock, not embeddings, not schema. Real narrow bug on the trace-ingestion path (`handle_promote_observation_to_claim` returns cleanly with no claim when the observation has no resolvable task/episode anchor). Not the corpus artifact→procedure path. Belongs to §29/§42 hardening or a dedicated fix. |

## Current Supabase state (verified live)

Project `wckeklqxmiglivfolujn`, `ap-south-1` **session pooler** (`aws-0-ap-south-1.pooler.supabase.com:5432`), Postgres 17.6. The direct host `db.<ref>.supabase.co` is IPv6-only and does not resolve from this machine — `backend/.env` (gitignored) uses the session-pooler URL; the direct string is kept as `DATABASE_URL_DIRECT`.

| | |
|---|---|
| migrations applied | **34 / 34**, checksums clean |
| extensions | `vector 0.8.2`, `pgcrypto 1.3`, `btree_gist 1.7` |
| procedures | 49 distinct (`prior_library` **28** from corpus Phase 9 + `system_pending_review` 21 from earlier e2e runs) — all `verification_state = candidate`, **0 verified** |
| task_nodes | 170 `created_by='corpus_wave'` (+ pre-existing test rows) |
| claims (`knowledge_nodes`) | 15 `created_by='corpus_wave'`, all embedded, all anchored to 1 `document` episode via `episode_links` |
| episodes | 5 |
| `get_claim_graph_overview(link_mode='both')` | 15 nodes + 30 similarity edges, `by_status: {current: 15}` |
| e2e vs Supabase (`-k "e2e or schema_drift"`) | last full run: **281 pass / 2 fail** (pre-claim-graph-fix). The 2: claim-graph overview (now fixed) + `test_ingestion_admin_endpoint_e2e` (OPEN, above). |
| offline suite | 2335 tests collected; not re-run this session |
| local Postgres path | intact — "offline suite" = `DATABASE_URL` unset; `*_e2e.py` self-skip. Not disturbed. |

## Current frontend state

- `frontend/` — **Next.js** (`next.config.js`, `app/`, `components/`, `lib/`, `node_modules/`). This is the tracked frontend (older debate/approval UI per CLAUDE.md). NOT Vite.
- `frontendv1/` — **untracked** separate Next.js app (`src/`, its own `package.json`, build logs `build2.log`/`build3.log`/`dev.log`). A parallel frontend experiment by another session; not on this branch, not mine.
- No Problem/Solution/Procedure/Task/Implementation/Evidence benchmark surfaces exist in either.
- No WebMCP (`document.modelContext.registerTool`) anywhere.

## Current claim-graph state

- Backend: `claim_graph_api.py` (`get_claim`, `get_claim_neighbors`, `traverse_claim_graph`, `get_claim_graph_overview`, `get_claim_evidence_api`, `get_claim_dependents`, `get_claim_history`). Two edge KINDS: `relation` (real claim↔claim `custom_edge_type` ∈ `ALL_CLAIM_RELATIONS`) and `similarity` (computed k-NN in embedding space, `sim_k=3`, `sim_threshold=0.55`). Deliberately distinct — the docstring says the relation graph is "sparse-to-empty in real corpora".
- MCP/web: `claim_graph_page.py` serves `/claim-graph` (vendored force-graph, no build step) → `fetch('./claim-graph/data')` → `get_claim_graph_overview`. Page JS branches on `l.kind` correctly.
- REST: `GET /v1/claims/graph` (in `api/claims.py`).
- **Distinct from the procedural graph** (§7) — claims/evidence/sources/temporal vs problem/procedure/task/implementation/execution. Correct separation; keep it.

## Current corpus state (verified)

`source_registry.jsonl` 50 sources, **49 RESOLVED** (S44 Papers-with-Code = ARTIFACT_MOVED, provenance-only). 50 `candidates/Sxx.md` (48 full 10-section procedures + S20 claims-source + S44 stub). `ranking.json`: **29 ADMITTED / 19 candidate / 1 inconclusive / 1 rejected**; 6 license-blocked from canonical ingest (S10 S18 S26 S29 S37 S49). Phase 9 ingest runner `.scratch/corpus_wave/ingest_run.py` → 28 procedures + 170 task_nodes + 15 claims in Supabase (`ingest_result.json`). **Phase 10 NOT done**: retrieval / solution-search tests, `final_report.md`.

## Current implementation / provider / execution state

Substantial substrate already present — **do not recreate**:

| concept | where |
|---|---|
| Implementation (durable) | `backend/db/33_implementation_registry.sql` (tables `implementations`, `implementation_tasks`); `backend/app/execution/implementation_registry.py`, `implementations.py` |
| ImplementationProvider | `backend/app/execution/providers.py` (`discover`/`inspect`/`execute` interface + `SubprocessSandboxExecutor` etc.); `implementation_executor.py` |
| ExecutionPlan / plan pinning | `backend/db/23_plan_persistence.sql` ("persist ExecutionPlan/TaskGraph [D→frozen] and bind every execution to an exact plan version"); `backend/app/execution/plan_persistence.py`, `plans.py` |
| Execution / graph executor | `backend/app/execution/graph_executor.py`, `procedure_graph.py`, `replay.py`; `backend/app/services/execution.py` |
| Verification / Evidence | `backend/app/execution/evidence.py`; `backend/db/24_evidence.sql`, `26_replayability.sql`, `27_failure_routing.sql` |
| Capability estimate | `backend/app/services/capabilities.py` (Wilson LB, independence groups, brand-blind) |
| Solution↔implementation | `backend/app/services/solution_implementations.py`; `api/solutions.py` |
| REST routers | `api/{implementations,solutions,procedures,tasks,search,repositories,projects,me,decompose,graph,claims,chat,ingest,agents,approval,admin}.py` — **no `problems`/`benchmarks`/`evaluations`** |
| Ingestion adapters | `skill_ingestion.py`, `ingestion_sources/`, `ingestion_jobs.py`, `chat_history_import.py`, `historical_bootstrap.py` (ChatGPT/Claude export parsing), `trace_redaction.py` |

## existing concept → final V1 concept map (§4)

| existing | final V1 role | action |
|---|---|---|
| `procedures` / `procedure` versions | Solution target (`solution_type='procedure'`) | compose, don't duplicate |
| `task_nodes` | Benchmark case (§13) + Solution target (`task`) | reuse as the case unit |
| `task_graphs` (migration 08b) | Solution target (`task_graph`) | reuse |
| `implementations` + `implementation_tasks` (mig 33) | Implementation Registry (§20) | extend fields if missing (locator/invocation/descriptor); do NOT new-table |
| `execution_plans` + plan-version binding (mig 23) | plan pinning (§23) | already pins; verify no mid-execution re-resolve |
| `executions` + `PlanNode` state | durable retry/resume substrate (§24) | add/verify node-state enum + attempt_count + resume path |
| `evidence` (mig 24) | Evaluation evidence lineage (§15/§16) | Evaluation aggregates over these; no payload duplication |
| `capabilities.py` Wilson LB | ranking / current-leader math (§19) | reuse for BEST_VERIFIED etc. |
| `knowledge_nodes` claims + `edges` | Claim graph (§7, §43) | keep separate from procedural graph |
| `historical_bootstrap.py` ChatGPT parse | §28 branch-reconstruction fix site | fix here |
| `skill_ingestion.py` + `ingestion_sources/` | §29 injection-defense pipeline site | harden here |
| **NEW**: `problems`, `benchmarks`, `solutions`, `evaluations` tables + services + routers | §11–19, §36–37 | additive migration; association/read-model layer only |

## Gap summary (what this wave must build)

1. **Product model** — `Problem` / `Benchmark` / `Solution` / `Evaluation` tables + services + REST (§36) + MCP (§37) + `evaluations_comparable` + ranking. Entirely new (additive).
2. **Implementation Registry completion** — locator/invocation split + harness descriptor (§22); verify plan pinning has no mid-run re-resolve.
3. **Durable retry/resume** — node-state machine + attempt_count durability + resume/idempotence/concurrent-resume/stale-worker over `executions`/`execution_plans`/PlanNode. Verify what migration 23/26 already give.
4. **§28 ChatGPT branch reconstruction** — release-critical; `historical_bootstrap.py` + regression tests.
5. **§29 ingestion injection defense** — release-critical; `skill_ingestion.py` delimited-data + schema/semantic validation + conservative reject; regression tests.
6. **Frontend** — benchmark-first surfaces on the existing Next.js app (§34–48). Large.
7. **WebMCP** — `document.modelContext.registerTool` wrapper + 13 semantic tools (§49–54). New.
8. **Corpus Phase 10** — retrieval/solution-search tests + `final_report.md`.
9. **E2Es** — product lineage (§57), retry/resume (§58), claim-graph (§59, mostly done), migration fresh+upgrade+Supabase (§60).
10. **Acceptance matrix** (`.scratch/final-v1-hardening-acceptance.md`) + docs (§62).

## Realistic sequencing (this session vs. staged)

**Doable + provable this session** (release-critical + scoped):
- #1 claim-graph fix — **DONE** (`bbf6ff0`).
- Corpus Phase 10 (offline retrieval tests + `final_report.md`).
- §28 ChatGPT branch reconstruction (scoped to `historical_bootstrap.py` + tests).
- §29 ingestion injection defense (scoped to `skill_ingestion.py` + tests).
- Acceptance-matrix skeleton.

**Genuinely multi-session / multi-lane** (cannot be "fast" to §69's "implemented and proven" bar):
- Product model (4 models + migration + services + 19 REST endpoints + MCP + comparability + ranking + §57 lineage E2E).
- Durable retry/resume state machine + §58 E2E.
- Full benchmark frontend (~15 routes) + §56 E2E.
- WebMCP (13 tools) + live browser test.

These are laid out with owned paths + queues for the build-board / scoped agents; a single fast fleet-spawn would produce an unreviewable diff that §5/§64/§66 explicitly reject.
