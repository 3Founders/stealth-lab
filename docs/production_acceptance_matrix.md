# Acceptance matrix (final pass)

Status: **CLOSED** = implemented and proven by a test that ran green; **PARTIAL** = real and tested
but incomplete against the requirement; **OPEN** = not done. Models in tests are frozen fixtures
(recorded verdicts / concept embedder); the optional live-provider tests
(`tests/test_retrieval_live_models.py`) were **not run** (no provider credentials in this environment).
Live-DB tests ran against a throwaway PostgreSQL 18.4 + pgvector on localhost.

## §31 invariants

| # | Requirement | Status | Code | Test | Notes |
|---|---|---|---|---|---|
| 1 | A source can be safely ingested multiple times | **PARTIAL** | `queue.enqueue` (unique idempotency key), `procedures.source_key`, `handlers.py` advisory lock, `identity_decisions.idempotency_key` | `test_same_source_delivered_twice_creates_one_of_everything`, `test_duplicate_enqueue_is_idempotent`, e2e redelivery | Closed for the canonical bundle path. Legacy adapters (`trace_worker`, `trajectory_semantics`, `local_sync`, publication) keep their own dedup and were not moved to `source_key`; skill packages keep the existing `ingested_artifacts` hash dedup |
| 2 | Multiple workers ingest concurrently | **CLOSED** | `queue.lease` (SKIP LOCKED, fenced), `Worker` | `test_two_workers_never_lease_the_same_job`, `test_concurrent_workers_on_overlapping_sources_create_no_duplicates` | |
| 3 | Same Goal from different sources → one Goal when semantically identical | **CLOSED** | `identity_resolution.resolve_goal_identity`, `reconcile_goals`, `merge_goal` | `test_paraphrase_is_merged_only_because_the_judge_said_same`, e2e (25/25 green after fixing test-fixture symmetry) | Judge = frozen fixture; live JEV/Gemini quality not measured |
| 4 | Procedures link directly to that Goal | **CLOSED** | `capture_procedure` (link in the INSERT), `supersede_procedure` carry, `merge_goal` repoint, trigger `sl_procedure_follow_merged_goal` | `test_procedures_from_different_sources_link_directly_to_the_one_goal`, e2e | Pre-existing procedures with NULL `achieves_goal_id` are not backfilled (migration 83 covered text-goal rows only) |
| 5 | Different Procedures for the same Goal remain separate | **CLOSED** | `procedure_identity.ingest_procedure` | `test_procedure_identity_same_refinement_and_alternative` | |
| 6 | Goal hierarchy stored separately and optional | **CLOSED** | `goal_relations`, `propose_goal_relations`, reconcile | `test_narrower_goal_creates_goal_and_a_separate_optional_goal_relation`, e2e | edges are `proposed`; no acceptance workflow |
| 7 | Hierarchy does not determine physical sharding | **CLOSED** | `shards.choose_shard` (hash of goal id only) | `test_shards_offline.py` | |
| 8 | Canonical knowledge is physically sharded | **PARTIAL** | registry, routing, `home_shard_id`, `hydrate_rows`, `ShardPools` | `test_remote_shard_canonical_is_projected_via_its_own_pool`, `test_candidates_on_multiple_shards…` | Reads/routing/projection/hydration of remote shards are real and tested against a second pool. **Canonical writes are restricted to K000** (`REMOTE_WRITES_SUPPORTED=False`) because control-DB tables FK into `procedures`/`goals`. Everything currently lives on K000 |
| 9 | Global indexes are projections, not duplicate canonical truth | **CLOSED** | `search_projection.py`, outbox, `reindex`, `verify_projection` | `test_search_projection_e2e.py` | Legacy inline embedding columns on canonical tables remain (denormalized) |
| 10 | Retrieval does not search all shards | **CLOSED** | `hydrate_rows` | shard batching test (remote fetch_calls==1, idle shard 0) | |
| 11 | Tier 1 resolves Goals first | **PARTIAL** | `retrieval_service.search_goals` | golden suite | True for the canonical service and REST `/search/recommend`; the other retrieval entry points still bypass it (row 18/24) |
| 12 | Tier 2 retrieves Procedures using resolved Goal(s) | **CLOSED** | `retrieve_procedures` | `test_same_goal_multiple_procedures_and_other_goal_procedure_never_outranks`, `test_procedures_are_never_searched_without_a_resolved_goal` | |
| 13 | Relevant local Claims influence both tiers | **CLOSED** | `build_query_context` → candidate text + judge text | `test_local_claims_change_the_resolved_goal`, `test_local_claims_change_procedure_applicability`, bounded working-set test | Claim ids logged in `retrieval_decisions` |
| 14 | Deterministic search generates candidates | **CLOSED** | `_legs`, `generate_goal_candidates` | golden + scale benchmark | |
| 15 | JEV/NLI performs semantic judgment | **CLOSED** | `SemanticJudge.judge_identity` + prompts/providers | golden (frozen) | live-provider test written, not executed |
| 16 | JEV failure has an explicit fallback | **CLOSED** | chain fallback; `mode=model` | `test_jev_unavailable_falls_back_to_the_nli_model_not_a_heuristic` | |
| 17 | All rerankers down → degraded, not fake certainty | **CLOSED** | `MODE_CANDIDATES`, `_select` | `test_all_semantic_rerankers_down_returns_degraded_candidates_not_fake_certainty` | |
| 18 | MCP and REST use the same retrieval service | **OPEN** | REST `/v1/search/recommend` → `domain_search.find_best_way` → service | `test_domain_search_offline.py` (adapter) | **MCP was not converged.** MCP `find_best_way` (lookup), `search_procedures`, `search_goals`, `find_best_solution` and REST `/procedures/search`, `/solutions/search`, `/goals/search` still use their old rankers. Needs a compatibility layer for `route_decision`'s result shape |
| 19 | Workers are provider-neutral | **CLOSED** | `app/ingestion/*` | worker tests | |
| 20 | Cloud Run / GHA / Oracle / local run the same code | **PARTIAL** | `deploy/ingestion/*`, `.github/workflows/ingest-worker-batch.yml` | none executed on those platforms | Templates only; nothing was deployed or run on Cloud Run/GitHub/Oracle |
| 21 | Projection failures can be replayed/repaired | **CLOSED** | `drain_outbox`, `reindex`, `verify_projection`, `retry_failed` | `test_lost_projection_is_detected_and_repaired`, `…dies_after_canonical_persistence…` | |
| 22 | Important dedup/retrieval decisions auditable | **CLOSED** | `identity_decisions`, `retrieval_decisions` | identity + golden tests | |
| 23 | Existing durable execution semantics still work | **PARTIAL** | untouched MCP execution; `compile_plan`+`persist_compiled_plan` consume the retrieved Procedure | e2e final step; regression run recorded in the report | MCP `find_best_way` execution path was deliberately not modified |
| 24 | No stale alternate path can silently bypass the canonical path | **OPEN** | — | — | See audit §7 "Quarantined, not yet converged". Adapters other than the bundle handler skip judged *Procedure* identity |
| 25 | We can begin bulk ingestion immediately | **PARTIAL** | worker + runbook | e2e | Skill-package and bundle ingestion can run on the new worker; blockers below |

## Other required items

| Requirement | Status | Notes |
|---|---|---|
| Redundancy audit doc | CLOSED | `docs/backend_redundancy_audit.md` |
| Claim identity (same/generalizes/contradicts) at ingestion | **OPEN** | exact statement only; judge op exists |
| Object storage for large raw payloads | **OPEN** | not implemented; `OBJECT_STORAGE` config not introduced; no object-store failure tests |
| Global Implementation ontology migration | **OPEN** | table retained (runtime binding, 60 dependents); callers not migrated |
| Production configuration validation | PARTIAL | `--validate-config` checks DB + providers; `KNOWLEDGE_SHARDS` JSON/`OBJECT_STORAGE`/`EMBEDDING_PROVIDER` as *named* settings were not introduced (existing `settings` are used; shard config = registry + `<ID>_DATABASE_URL`) |
| Migration from baseline + fresh | CLOSED | `test_migration_95_upgrade_e2e.py` |
| Load benchmark (100k goal projections) | CLOSED | `scripts/benchmark_projection_scale.py`; results in `docs/production_ingestion.md` |
| Live-model integration test | OPEN (written, not run) | needs credentials |
| Recorded/frozen semantic fixtures | CLOSED | `tests/identity_fakes.py` |

**Verdict:** PRODUCTION INGESTION + RETRIEVAL NOT READY — rows 8, 11, 18, 24 (and 1, 20, 23, 25 partially) are unresolved; see the final report.

## Regression results recorded at the end of the pass

* Offline suite (`-k offline`, minus the pre-existing uncollectable `test_document_skills.py`): **2333 passed, 2 failed** — the 2 are `test_document_adapters_offline` docx tests that also failed at baseline (missing `docx` module).
* New live-DB suites, all green on PostgreSQL 18.4 + pgvector: projection (5), goal identity (11), retrieval golden (18), distributed ingestion (14), migration 95 (2), end-to-end (25/25 repeated runs).
* Curated pre-existing live-DB regression (18 files, 100 tests): **89 passed, 11 failed** — NOT triaged to closure:
  * `test_domain_search_e2e` (4) and `test_search_privacy_leak_e2e` (2) pinned the *old* direct-procedure recommendation. Under the new goal-first service they receive no judge (or a configured-but-unreachable one), so they report `candidates_only`/no goal. They need to be rewritten with injected judge fixtures; two also failed inside setup because `capture_procedure` now fails closed (`SemanticJudgmentUnavailable`) when a provider is configured but unreachable and a goal candidate exists.
  * `test_retrieval_quality_e2e` (5) exercise `find_applicable_procedures` against a seeded real corpus that is absent from the scratch DB; not shown to be caused by this pass, but no baseline run was done to prove it.
* Not run: the rest of the ~150 `*_e2e.py` files (MCP/execution/verification suites), live-provider tests, any Cloud Run / GitHub Actions / Oracle execution.
