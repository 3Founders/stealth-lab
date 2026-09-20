# Production ingestion

## Canonical flow

```
source (adapter)
  → enqueue  (ingestion_jobs, idempotency_key, explicit scope)           app/ingestion/enqueue.py
  → lease    (SKIP LOCKED, lease_until, fenced)                          app/ingestion/queue.py
  → handler  (JOB_HANDLERS: ingest_candidate_bundle, ingest_skill_package, normalize_trace_event, …)
       ├ source registration + exact source dedup   ingestion_contexts, procedures.source_key
       ├ Goal identity     goals.find_or_create_goal → identity_resolution (FTS+ANN → JEV/NLI)
       ├ Procedure identity procedure_identity.ingest_procedure (goal-constrained → JEV/NLI)
       ├ Claims + provenance   claims.capture_claim (source_ref, ingestion_context_id)
       ├ shard routing      shards.choose_shard / choose_child_shard (+ object_routes via trigger)
       └ canonical persistence (procedures.achieves_goal_id + home_shard_id written IN the INSERT)
  → projection  DB trigger → projection_outbox → search_projection.drain_outbox (worker end-of-run)
  → optional GoalRelation  (proposed edges at creation, and by reconcile_goals later)
```

The **entry point** for already-extracted knowledge is the `ingest_candidate_bundle` job
(`app/ingestion/handlers.py`). Existing adapters converge on the same two chokepoints
(`capture_procedure` → `find_or_create_goal`) — they now get judged Goal identity, the atomic
Procedure→Goal link and shard/projection bookkeeping automatically; see
`docs/backend_redundancy_audit.md` for which adapters use which entry.

## Invariants (each has a test)

1. A source can be ingested many times (`source_key`, `idempotency_key`).
2. Workers run concurrently; leases are fenced.
3. The same Goal from different sources resolves to one Goal (exact; judged; reconciled).
4. Procedures link **directly** to that Goal in the same INSERT (`procedures.achieves_goal_id`);
   new versions carry the link; merges repoint it; a trigger refuses a link to a merged goal.
5. Different methods for one Goal stay separate Procedures.
6. Hierarchy lives in `goal_relations` (multi-parent, optional, never used by placement or retrieval).
7. Projections are rebuildable (`admin reindex`) and verifiable (`admin verify-projections`).
8. Private data stays private: job scope is explicit; public-only job types refuse private scope;
   projections copy `visibility/owner_id/tenant_id`; retrieval applies `visibility_predicate`.

## Schema (migration 95 + existing)

| Table | Important columns |
|---|---|
| `goals` (83–87) | `id, canonical_name, normalized_name, description, expected_outcome, verification_requirement, status(candidate/active/deprecated/merged), merged_into_id, aliases, version, embedding(+model/provider/text_hash), scope_*, visibility, owner_id, **home_shard_id, reconciled_at** (95)` |
| `goal_relations` (95) | `specific_goal_id, abstract_goal_id, relation_type='SPECIALIZES', status(proposed/accepted/rejected), confidence, provenance, decision_id` |
| `procedures` (18…) | `id (row/version), procedure_id (stable), name, goal (text, display), steps JSONB, preconditions, verification_state, verification_stats, availability, version, t_valid/t_invalid, embedding(+model), **achieves_goal_id, home_shard_id, source_key** (95: unique live)` |
| `knowledge_nodes` (claims: `node_type='claim'`) | `name (statement), subject/predicate/object, claim_status, properties, embedding, scope_*, visibility, ingestion_context_id` |
| `evidence`, `claim_sources`, `procedure_claim_refs`, `ingestion_contexts`, `sources` | provenance/evidence (unchanged) |
| `knowledge_shards`, `object_routes` (95) | registry + `(object_type,object_id) → home_shard_id` |
| `goal_search_index`, `procedure_search_index`, `claim_search_index` (95) | projections: name/summary, `search_tsv`, `embedding, embedding_model, embedding_version, embedding_dim`, `home_shard_id`, `status`, `version`, scope columns |
| `projection_outbox` (95) | durable, coalesced refresh queue |
| `identity_decisions`, `retrieval_decisions` (95) | durable audit |
| `ingestion_jobs` (12/42/95) | `id, job_type, payload, status, attempts, max_attempts, claimed_by, lease_until, run_after, idempotency_key, source_id, scope_*, owner_id, visibility, config_version, started_at, completed_at, last_error, usage` |

Global Implementation concept: **removed** (migration 98). An implementation is a one-step procedure or a step of a
multi-step one; execution bindings live on `procedures.steps[].binding`, provenance on `procedure.source_locator` /
`steps[].source_locator`, preserved source bytes on `ingested_artifacts`. See `docs/step_bindings_and_artifacts.md`.

## Migration and upgrade safety

`python scripts/migrate.py` (idempotent, ledgered). Migration 95 is `IF NOT EXISTS`/`ON CONFLICT`
throughout, backfills `object_routes` + `projection_outbox` for existing rows (a replayable drain,
not an inline copy), marks pre-existing goals as reconciled, and adds no NOT-NULL column without a
default. Verified by `tests/test_migration_95_upgrade_e2e.py`: fresh database (01–95); a database
built at 94 with a legacy goal, procedure, claim and job upgraded to 95 (routes + outbox backfilled,
legacy goals marked reconciled, old job statuses preserved, new status vocabulary enforced); re-run
is a no-op; draining the backfilled outbox yields consistent projections. Existing
tests that encoded the removed SimHash/cosine-threshold Goal tiers were rewritten
(`tests/test_goal_identity_e2e.py`), and fakes that pinned the old direct-procedure cascade were
replaced by adapter tests (`tests/test_domain_search_offline.py`).

## Measured scale



Command: `python scripts/benchmark_projection_scale.py --goals 100000 --procedures 20000 --queries 200`
(and `--only-fts-zipf` for the natural-vocabulary FTS run). Environment: Windows 10, PostgreSQL 18.4
with default settings (shared_buffers 128 MB), pgvector, single client, 1024-d vectors, synthetic rows
generated inside Postgres. **These are local single-machine numbers, not a production capacity plan.**

| Measurement (100,000 goal projections) | Result |
|---|---|
| bulk load incl. 1024-d random vectors | 92 s (dominated by vector generation) |
| HNSW build (`maintenance_work_mem=1GB`) | 231 s |
| vector ANN top-20 (uses `idx_goal_search_embedding`) | p50 **3.9 ms**, p95 6.4 ms |
| FTS, natural-shaped corpus (20k-word Zipf vocabulary, 3-term OR queries) | p50 **5.9 ms**, p95 **290 ms**, mean 37 ms (tail = queries containing a very common term) |
| FTS, deliberately low-selectivity corpus (30-word vocabulary; each term matches 10–40 % of rows) | p50 318 ms, p95 403 ms (worst case) |
| fused retrieval (both legs + RRF + visibility predicate) on the low-selectivity corpus | p50 640 ms, p95 849 ms |
| shard routing (rendezvous hash, 8 shards) | ≈ 22 k decisions/s/core |
| procedure projection bulk insert (SQL) | ≈ 17 k rows/s |
| projection upsert through the real path (`drain_outbox` → `project_object`, one drainer) | **≈ 127 rows/s** |
| worker throughput, 2 workers × 4 lanes, instant fake models (measures our overhead only) | 7.0 bundles/s (200/200 done) |

Reading these honestly:

* ANN is fine at 100k. FTS is fine for natural text *except* the tail when a query contains a very
  common term: an OR query then ranks a large posting list. Mitigations if it matters on the real
  corpus: drop over-frequent query terms (IDF pruning), or AND-first with an OR fallback. Not done.
* The fused number was measured on the low-selectivity synthetic corpus only; a natural-corpus fused
  run was not done.
* Projection upserts are the slowest stage (127 rows/s per drainer: advisory lock + two round trips
  per object). Drainers scale horizontally (`SKIP LOCKED`), but a 1 M-object initial backfill on one
  drainer is ~2 h; batching the upsert is an obvious optimisation, not done.
* Real ingestion throughput is bounded by embedding/JEV/LLM latency and rate limits, which this
  benchmark deliberately excludes.
