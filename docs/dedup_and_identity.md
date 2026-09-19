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

## Not done in this pass

* **Claim** semantic identity (same proposition / contradicts / generalizes) is not wired into
  ingestion: claims dedup only on exact normalized statement per scope. The judge op exists
  (`judge_identity("claim")`, relation vocabulary incl. `contradicts`) and the existing
  `claim_equivalence` module already classifies claim pairs; connecting them at write time is open.
* Adapters other than the candidate-bundle handler (`skill_ingestion`, `trace_worker`,
  `trajectory_semantics`, `local_sync`, …) still call `capture_procedure` directly: they get judged
  **Goal** identity and the atomic Procedure→Goal link automatically, but not judged **Procedure**
  identity (that is opt-in via `procedure_identity.ingest_procedure`). Auto-enabling it inside
  `capture_procedure` would change what those callers' follow-up UPDATEs touch, so it was not done blind.
