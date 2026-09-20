# Deduplication and identity

Identity is **object-specific**. There is no universal "cosine > X ⇒ duplicate". Deterministic
code generates candidates; a model decides identity; the decision is durable.

| Object | Exact (deterministic) | Candidate generation | Decider | Outcomes |
|---|---|---|---|---|
| Source | `procedures.source_key` unique (live rows); `ingestion_jobs (job_type, idempotency_key)` unique; `ingested_artifacts` hash for skill packages | — | — | duplicate ⇒ return existing, no write |
| Goal | normalized name / alias string equality (`goals.find_or_create_goal`); DB partial unique indexes stop exact races | FTS + ANN over canonical `goals`, RRF | `judge_identity("goal")` | same → reuse `goal_id`; specializes/generalizes → new Goal + **proposed** `goal_relations` edge; related/distinct → new Goal |
| Procedure | `source_key` | goal-constrained FTS + ANN (`procedure_identity._candidates`) | `judge_identity("procedure")` | same → reuse + attach source as provenance (`evidence_refs`); refinement → `supersede_procedure` (new **version**, same `procedure_id`); distinct → new Procedure on the **same** `goal_id` |
| Claim | exact normalized statement in scope (`handlers.py`) | — | (existing `claim_equivalence` remains the semantic relation system) | see "Not done" |

## Rules

* A "same" verdict below `SAME_MIN_CONFIDENCE` (0.75, the *model's* confidence) is treated as
  `related`: a low-confidence merge never happens.
* Judge chain exhausted while candidates exist ⇒ `SemanticJudgmentUnavailable` ⇒ the worker marks
  the job **retryable** (nothing half-written). Only when *no provider is configured at all*
  (dev) does it create a new object and record `judge_unavailable` for later review.
* Every judged resolution writes `identity_decisions` (candidates with ranks, judge chain,
  provider, model, prompt version, FTS/vector candidate counts, job id). With an
  `idempotency_key` a replayed job **reuses** its earlier decision instead of re-judging.
* Merged goals are kept (`status='merged'`, `merged_into_id`), never deleted; their
  Procedures/Implementations/hierarchy edges are moved to the survivor; a DB trigger
  (`sl_procedure_follow_merged_goal`) stops a late writer from linking to a merged goal.

## Concurrent paraphrases (why reconciliation exists)

Two workers ingesting *different phrasings* of one Goal at the same moment cannot see each other
(neither is committed when the other checks); only exact-name races are stopped by a unique index.
So new goals start with `reconciled_at IS NULL` and `identity_resolution.reconcile_goals` (run at
the end of every worker run, or `admin reconcile-goals [--all]`) judges each against goals
created within ±30 min (`--all` = whole corpus) and merges **the newer id into the older** (a
deterministic tie-break: every worker/direction converges on the same survivor). It also adds
hierarchy edges that could not be known at creation time. A judge outage leaves the goal
unreconciled (retried next sweep). Single-flight via a blocking advisory lock.

Check: `admin verify-dedup` (duplicate live goal names, live procedures without a goal link,
procedures linked to merged goals, goals created while the judge was unavailable).

## Claim identity (`services/claim_identity.py`)

Exact normalized statement (same scope, same visibility/owner class) -> candidates from `claim_search_index` (all shards;
FTS+ANN+RRF) -> `judge_identity("claim")`: `same` (>= 0.85) reuses the claim and **attaches the source as provenance**
(`properties.source_refs`, on the claim's own shard); `contradicts` keeps **both** and queues a pending `contradicts` row in
`claim_relation_candidates` (the existing review table - nothing is auto-resolved, no truth-state flip); specializes /
generalizes / related create a new claim + a pending `related` candidate; a low-confidence "same" is "related".
Private claims are only compared with the same owner's private claims; public only with public. Same-goal claim
resolution is serialised by an advisory lock and projected inside it, so concurrent paraphrases cannot race.
Judge outage: public fails closed (retryable); private creates + records `judge_unavailable`.

## Procedure identity for the older adapters

`capture_procedure(procedure_dedup=True, source_key=...)` runs the same goal-constrained judge as `ingest_procedure`:
same method -> reuse + provenance (`{"reused": True}`; callers must not re-stamp extractor metadata), refinement -> new
version, distinct -> new procedure on the same goal. Enabled in `skill_ingestion` and `publication`; deliberately NOT
in `local_sync`, `trajectory_semantics`, `procedure_extraction` (see docs/local_vs_canonical.md). With no semantic
provider configured the flag is a no-op (nothing can judge). `skill_ingestion.check_novelty` (a cosine >= 0.90 refuse-only
gate used by `ingest_skill_md`) is the last similarity-threshold dedup left; it is legacy and not on the worker path.

## Not done

* Claim reconciliation for claims created while the judge was down (they carry `judge_unavailable`; a sweep like
  `reconcile_goals` for claims is not written).
