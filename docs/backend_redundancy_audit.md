# Backend redundancy audit

Baseline: `main` @ `bc91778`. Traced from code, not docs. Where a doc and the
code disagree, the code column is what is written here.

## 0. Ground truth that changes the plan

| Fact (verified in code) | Consequence |
|---|---|
| **Every** ingestion adapter (skill, trace, trajectory, repo, local `.stealth` sync, publication, synthesis) already ends in `procedures.capture_procedure()` and `goals.find_or_create_goal()`. | There is one real chokepoint for Goal + Procedure identity. Convergence = harden those two functions, not build a new pipeline. |
| No sharding exists anywhere in `backend/app` or `backend/db` (only an unrelated word match in `21_band1_contracts.sql`). No shard registry, no `home_shard_id`, no object routing, no projection tables. | Sharding, projections and routing are greenfield. |
| `procedures.achieves_goal_id UUID NULL` (migration 83) is the Procedure→Goal link. It is **nullable** and the free-text `procedures.goal` column is still authoritative for retrieval. | Direct-link invariant is not enforced today. |
| `find_or_create_goal` decides identity with exact name → alias → **SimHash Hamming ≤ N** → **cosine ≤ AUTO threshold auto-merge** → optional LLM. | Violates target rule "no `cosine > X` as final judge; JEV/NLI decides". |
| `ingestion_jobs` (mig 12/42) is a real `FOR UPDATE SKIP LOCKED` queue but has no lease expiry, no idempotency key, no retryable-vs-permanent split, no scope column; recovery is a manual `requeue_stuck_jobs()`. Nothing runs it as a standalone worker (only the in-process scheduler). | Reuse the table; extend it. Do **not** create a second queue. |
| The semantic layer (`services/semantic`: JEV → Gemini → Gemma chain, no heuristic provider, `ChainResult(ok=False)` on total failure) already exists and has claim-relation + applicability ops. | Reuse for Goal/Procedure identity and reranking. Add ops, not a new judge. |
| **Four** functions named/behaving as "find best way" exist (see §2). Only MCP `find_best_way` executes; the others are read-only rankers with different pipelines. | Main retrieval redundancy. |
| Nothing in retrieval resolves a Goal first. `find_applicable_procedures` embeds the raw query and searches procedures directly. | Two-tier requirement is unmet everywhere. |

## 1. Ingestion

| Area | Existing path(s) | Canonical path to keep | Redundancy / problem | Action | Risk |
|---|---|---|---|---|---|
| Skill/doc ingestion | `skill_ingestion.compile_skill_artifact` (2684 lines), `ingestion_jobs.handle_ingest_skill_package`, `ingestion_sources/{github_corpus,skill_md,document_adapter}`, `document_ingestion.ingest_canonical_document` | Adapters stay; both funnel to `capture_procedure` + `find_or_create_goal` | `skill_ingestion` calls `find_or_create_goal` at 4 sites (step goals, primary, capability) with per-site kwargs; `document_ingestion` is a second, smaller sources→canonical path | Keep; route through one `resolve_goal()` (§3). Do not delete `document_ingestion` (used by adapters/tests) | Med |
| Trace/trajectory | `trace_worker`, `ingestion_jobs.handle_normalize_trace_event/…extract_procedure_from_episode/…consolidate_local_episode`, `trajectory_semantics`, `ingestion_sources/{dispatch,openhands,normalized_trajectory}` | `dispatch.TRAJECTORY_ADAPTERS` → `write_normalized_trajectory` → job handlers | One adapter boundary already ("no second ingestion system"). `trajectory_semantics` re-calls `find_or_create_goal` 3× | Keep | Low |
| Repo/local | `repo_procedural`, `stealth/local_sync`, `repository_knowledge` (read-side) | same | `local_sync` has its own `find_or_create_goal` call w/ separate quality catch | Keep; shared resolver | Low |
| Publication (private→global) | `publication.py`, `publish.py`, `claim_publication.py`, `publication_deps.py` | `publication.publish_*` | `publish.py` vs `publication.py` overlap (not traced to a live caller of each) | Flag; no removal in this pass | Med |
| Claim extraction | `claim_extraction`, `observations`, `claims.capture_claim`, `claim_evidence` | same | ok | Keep | — |
| Embeddings | `embeddings.Embedder` (Voyage/Gemini/local, disk cache, token bucket), `embed_cache` | same | Model id stored per-row on goals/procedures (`embedding_model_id`) but **no version** and no cross-space guard on search | Add model+version to projections, filter search by model | Low |
| **Job queue** | `ingestion_jobs` + `claim_jobs` + `process_pending_jobs`; `ingestion_scheduler` (in-proc), `semantic/jobs.py` (requeue), `verification_queue.py` | `ingestion_jobs` (extended) | No lease/heartbeat, no idempotency key, no retryable state, manual requeue, no worker entry point | **Extend** table (mig 95) + add `stealth-ingest` worker CLI; keep `process_pending_jobs` as the shared inner loop | Med |
| Goal dedup | `goals.find_or_create_goal` tiers 1/2/2.5/3-4/5, `_adjudicate_same_goal` (raw OpenAI client, bypasses semantic chain) | exact-name + alias (deterministic identity) then FTS+vector **candidates** → semantic chain judge | SimHash + cosine auto-merge are semantic heuristics acting as final judge; adjudication bypasses provider chain and fails closed to "create" | Replace tiers 2.5/3-4/5 with candidate+judge; persist decision | **High** (tests assert simhash/cosine behavior) |
| Claim dedup | `claim_equivalence`, `claim_family`, `dedup.py` (node dedup sweep, cosine clusters), `knowledge_conflict`, `temporal_conflict` | `claim_equivalence` (already model-classified, chain op `claim_relation`) | `dedup.py` = threshold cosine clustering over `knowledge_nodes`; `claim_family` = second identity relation next to `claim_equivalence` edges | Keep `claim_equivalence`; mark `dedup.merge_cluster`/`claim_family` legacy (not wired into new path) | Med |
| Procedure dedup | `procedure_dedup_sweep` script, `reuse_detection`, `procedures.capture_procedure` (versioning by `procedure_id`) | `capture_procedure` versioning + Goal-scoped candidate judge | No goal-constrained same-method judge at write time | Add in resolver (§3 of plan) | Med |

## 2. Retrieval

| Area | Existing path(s) | Canonical path to keep | Redundancy / problem | Action | Risk |
|---|---|---|---|---|---|
| Procedure search | `applicability.find_applicable_procedures` (1536 lines: hard cascade + hand-weighted similarity/capability RRF), `domain_search._search_procedures`, `solution_search.search_solutions`, MCP `search_procedures`, REST `/procedures/search`, `/solutions/search`, `/search` | New `retrieval_service` (Tier 1 Goal → Tier 2 Procedure) calling the **hard-constraint** half of `applicability` only | 3 REST + 2 MCP procedure searches, each with its own filter/gate order; `relevance_gate` measured-cutoff gate + hand-authored weights = deterministic semantic ranker | Route all via service; keep `check_hard_constraints` (true factual constraints); stop using hand weights for ranking | **High** |
| "Best way" | (a) MCP `find_best_way` (executes; tier-1 lookup via `find_applicable_procedures`), (b) `domain_search.find_best_way` (REST `/search/recommend`), (c) `product_model.find_best_way` (REST `/problems`, MCP `find_best_solution`), (d) MCP `find_best_solution` | MCP `find_best_way` keeps execution; its **lookup** + (b) + (c) delegate to `retrieval_service.find_best_way` | (b)(c)(d) are three read-only re-implementations with different verdict shapes | (b) and (c) become thin adapters; (d) redirected | High |
| Goal search | `goals.search_goals` (lexical+semantic RRF, opt-in), `execution/intent_resolution` (re-rank over `search_goals` with *hand-authored scoring*), `execution/goal_resolution` | `retrieval_service.search_goals` (FTS+vector RRF → chain rerank) | `intent_resolution._rank_candidates` is a deterministic semantic re-ranker | Replace scorer by chain judgment; keep its resolve/decide envelope | Med |
| Node retrieval | `retrieval.HybridRetriever` (task_nodes/knowledge_nodes; chat/decompose/relevant_claims), `hierarchy.hierarchical_search`, `local_retrieval.retrieve_local_first`, `agent_search` | Keep `HybridRetriever` for claims/nodes (selecting local Claims) | `hierarchical_search` + `coarse_route` = hierarchy-dependent retrieval | Leave for chat/decompose; **not** on the Goal/Procedure path | Low |
| Claim-conditioned | `claim_conditioned_retrieval.find_applicable_candidates` (MCP `search_procedures`), `applicability_judge` (+ cache), `semantic.chain` | Its shape (survivors → NLI judge → `PENDING_SEMANTIC_JUDGMENT` on outage) is the target degraded-mode contract | `compute_policy_score` hand weights | Keep judge path; reuse for Tier-2 rerank | Med |
| Plan-only | MCP `find_best_way(mode="plan_only")` → durable plan compile | untouched | — | Preserve | — |

## 3. Data model

| Concept | Representations found | Canonical | Action |
|---|---|---|---|
| Goal | `goals` (mig 83-87), **plus** free-text `procedures.goal`, `implementations.goal`, `procedure` step-level goal in JSONB | `goals` | `procedures.achieves_goal_id` becomes the invariant link; `procedures.goal` text remains as display/back-compat |
| Claim | `knowledge_nodes` (+`edges`), `claim_sources`, `evidence` | `knowledge_nodes` | project into claim index |
| Procedure / Step | `procedures` (steps = JSONB list), `task_nodes`, `procedure_graph` | `procedures` | project into procedure index |
| Evidence/provenance | `evidence`, `sources`, `ingestion_contexts`, `ingested_artifacts`, `claim_sources`, `procedure_claim_refs` | as-is | no change |
| Hierarchy | `knowledge_nodes` internal nodes (`hierarchy.py`), `edges`, `decompositions` — **no** Goal relation table | new `goal_relations` | add table; retrieval does not read it |
| Embeddings | inline `vector(1024)` columns on `goals`, `procedures`, `knowledge_nodes`, `agents` | inline stays canonical for single-DB; **projections** carry search copy | projection tables (rebuildable) |
| Implementation | `implementations` (+ mig 33/58/71/80/81/85/87), `procedure_implementations`, `implementation_registry`, `implementation_execution_telemetry`, REST `/implementations`, `/solutions/{id}/implementations`, MCP tools; 60 files reference it; migration 59 already dropped one redundant binding table | **Runtime binding, not knowledge** | Do **not** drop. Mark ontology use (`implementations.goal`, `implementation_goals` enrichment, goal-scoped implementation search) deprecated; execution-binding fields already live in `execution/implementations.py` + `PlanNode`. Blind removal would break 60 modules; migration plan in §6 |
| Execution | `execution_runs`, `execution_run_nodes/events`, `execution_plans`, durable_* | untouched | — |

## 4. Docs vs runtime mismatches

* `ingestion.md` §8 dedup tiers 2.5/3/4/5 are implemented as *threshold* tiers; the
  target prompt requires judge-decided identity.
* `MCP_HARDENING_*`, `STEALTHLAB_FINAL_INGESTION_*` describe an ingestion
  queue with recovery; the code has a manual-only `requeue_stuck_jobs`.
* `PRODUCTION_READINESS.md` claims production paths without a shard concept;
  none exists.
* `CLAUDE.md`-style convention "no backfill" conflicts with target migration
  requirement; migration 95 backfills only *new* tables from existing rows.

## 5. Baseline health

* Fresh DB: migrations 01–94 apply cleanly on PG 18 + pgvector.
* Pre-existing: `tests/test_document_skills.py` fails at collection
  (`app.skills.excel_generation` missing — `app/skills/` contains only
  `__pycache__`; untouched by this pass).

## 6. Risks of deleting each duplicate (why this pass quarantines, not deletes)

* `find_applicable_procedures` — imported by `skill_ingestion`, `resources`,
  MCP tools, `product_model`; 20+ tests. Kept as the hard-constraint gate.
* `implementations` ontology — 60 modules. Migrate callers first.
* `dedup.py` / `claim_family` — offline-only sweep tooling; deprecate with
  comments, no removal until the claim-relation path has run on real data.
