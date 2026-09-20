# Retrieval architecture

**One service, one flow.** `backend/app/services/retrieval_service.py` is the only ranker for
"what should I do for this task". REST `POST /v1/search/recommend` reaches it through
`domain_search.find_best_way` (an adapter that only maps request/response shapes). Nothing
in the retrieval path reads `goal_relations` (hierarchy is optional and never required).

```
query + local Claims
  │  build_query_context()      select ≤ 12 relevant local Claims (budget; never "all")
  ▼                             compact text = query + top claim statements; claim ids logged
TIER 1 — Goal      search_goals()
  │  goal_search_index (global projection, scope-filtered by visibility_predicate)
  │    FTS (to_tsquery OR-terms, ts_rank_cd)  ┐
  │    pgvector ANN (same embedding_model)    ┘→ Reciprocal Rank Fusion → top-k (search_top_k=20)
  │  top rerank_top_k=8 → SemanticJudge.judge_identity("task_goal")   JEV → Gemini → Gemma
  ▼  matches | partial | unrelated  (+ confidence)  → resolved Goal(s) (≤ 3)
TIER 2 — Procedure  retrieve_procedures()          NEVER runs without a resolved goal_id
  │  procedure_search_index WHERE goal_id = ANY(resolved) AND status='active'
  │    FTS + ANN + RRF (goal-constrained)
  │  hydrate canonical rows: ONE batched query per shard that holds a candidate (hydrate_rows)
  │  hard FACTUAL constraints (existing applicability.check_hard_constraints:
  │    scope/exclusions, staleness, availability, verification if required, preconditions)
  │  SemanticJudge.judge_identity("task_procedure")  — local claims are in the judged text
  │    applies | partial | not_applicable
  │  evidence: verification_stats → Wilson lower bound; linked claims (procedure_claim_refs)
  ▼  Pareto front on (applicability, evidence_lcb) → selection policy
result: recommendation | None, alternatives, frontier, goal_resolution, retrieval{…}
```

Files / functions: `build_query_context`, `search_goals`, `_legs`, `_judge_all`,
`retrieve_procedures`, `_select`, `find_best_way`, `_record` (all in `retrieval_service.py`);
candidate fusion `identity_resolution.rrf_fuse`; sharded hydration `shards.hydrate_rows`;
projections `search_projection.py`; judge `semantic/chain.py::judge_identity`.

## What is deterministic and what is a model

| Deterministic (candidate generation, hard facts) | Model (JEV → NLI/model chain) |
|---|---|
| FTS, ANN, RRF, scope/visibility, status, staleness, exclusions, preconditions, verification gate, Wilson bound, Pareto | is this Goal what the task needs; does this Procedure apply here given the local claims |

There is **no** hand-weighted semantic scorer in this path. `relevance_gate` /
`compute_policy_score` / `find_applicable_procedures`'s similarity+capability RRF still exist
for other callers (see the audit) and are not used by the canonical service.

## Failure modes (all tested in `tests/test_retrieval_golden_e2e.py`)

| Situation | `retrieval.mode` | Behavior |
|---|---|---|
| JEV answers | `jev` | normal |
| JEV down, Gemini/Gemma answers | `model` | normal; `providers` shows who judged |
| JEV+NLI both down | `candidates_only` | `degraded=true`, reason recorded, goals `unjudged`, procedures returned **unranked**, `recommendation=None`, `confidence="none"` |
| embedding provider down | (unchanged) | lexical candidates only, `degraded=true` |
| one shard unreachable | (unchanged) | `unavailable_shards`, `degraded=true`; its candidates are **not** treated as nonexistent (`missing_ids` stays empty) |
| no execution evidence for any applicable procedure | | `recommendation=None`, alternatives listed ("no winner invented") |
| no goal resolved | | procedures are never searched globally; empty result with an explicit reason |

## Audit trail

Every `find_best_way` writes a `retrieval_decisions` row: query sha256, viewer, goal ids,
candidate procedure ids, selected procedure, **local claim ids used**, mode, degraded flag,
counts, shards, unavailable shards. OpenTelemetry spans (`retrieval.goal_search`,
`retrieval.procedure_search`) carry counts/latency only; telemetry is never the source of truth.

## Configuration

`RetrievalConfig` (search_top_k, rerank_top_k, local_claim_budget, goal_resolve_max,
min_confidence, judge_concurrency) — defaults in code; override by constructing the config.
Semantic providers: `SEMANTIC_PROVIDER_PRIMARY/FALLBACKS`, `JEV_BASE_URL`, `JEV_API_KEY`,
`JEV_CAPABILITIES` (must include `identity` for JEV to serve these judgments; otherwise the
chain uses the next provider), Gemini/Gemma settings — see `backend/app/config.py`.

## Scale

`goal_search_index` has a GIN index on `search_tsv` and an HNSW (`vector_cosine_ops`) index;
every ANN query filters `embedding_model` so vector spaces are never mixed. Measured numbers:
see `docs/production_ingestion.md`, "Measured scale" (command:
`python scripts/benchmark_projection_scale.py --goals 100000`).

## Every entry point uses this service

| Surface | How |
|---|---|
| REST `POST /v1/search/recommend` | `domain_search.find_best_way` -> `retrieval_service.find_best_way` |
| REST `GET /v1/search`, `/v1/procedures/search`, `/v1/solutions/search` | `domain_search._search_procedures` -> `retrieval_service.search_procedures` |
| REST `GET /v1/goals/search`, MCP `search_goals`, `execution/intent_resolution` | `goals.search_goals` -> `retrieval_service.search_goal_candidates` (judge-free, same candidate machinery) |
| MCP `search_procedures` | `retrieval_service.search_procedures` (+ relevant local Claims); contract keeps `contextual_judgment_status`, adds `goal_resolution`/`retrieval` |
| MCP `find_best_way` tier-1 + routing | `retrieval_service.diagnose_procedures` -> `route_decision.decide_route(candidates=...)`: hard-constraint verdicts incl. UNKNOWN preconditions still drive `needs_clarification`/`plan_ready`; plan_only and the durable execution path are unchanged |
| MCP resource `relevant procedures`, `execution/recursion_guard` alternative lookup | `retrieval_service.search_procedures` / `diagnose_procedures` |

Read-your-writes: `search_goals` applies a small projection backlog inline (`_catch_up_projection`); API and MCP processes
also run a background drainer (`PROJECTION_DRAIN_ENABLED`, `PROJECTION_DRAIN_INTERVAL_SECONDS`).

Not on this path (different concepts, left alone): `find_best_solution`/`product_model.find_best_way` (Problem leaderboards),
`retrieve_precedent`/`get_relevant_claims` (claim/node retrieval that *supplies* local claims). Still present and now unused by
any converted surface: `applicability.find_applicable_procedures`/`diagnose_candidates`, `claim_conditioned_retrieval`,
`relevance_gate`, `solution_search` ranking; `skill_ingestion.check_novelty` still calls `find_applicable_procedures`.
