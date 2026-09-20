# Sharding

Sharding is **storage sharding**, not semantic. It never follows Goal hierarchy.

```
CONTROL DB (this database, shard "K000")
  knowledge_shards      registry: shard_id, status, weight, dsn_env (NAME of an env var; never a DSN)
  object_routes         (object_type, object_id) -> home_shard_id            [global routing]
  goal_search_index / procedure_search_index / claim_search_index            [projections]
  goal_relations        hierarchy, separate, optional
  identity_decisions, retrieval_decisions, projection_outbox, ingestion_jobs
KNOWLEDGE SHARDS  K000 (built-in) K001 K002 …   canonical goals/procedures/claims
```

## Placement (`app/services/shards.py`)

* New Goal → `choose_shard(goal_id, writable_shards)`: weighted **rendezvous hashing** over
  `status='active' AND weight>0` shards. Deterministic on every worker/provider; adding a shard
  only moves the keys that hash to it (and existing objects never move — `home_shard_id` is stored).
* Existing Goal → its `home_shard_id` is the preferred shard for new Goal-local Procedures
  (`choose_child_shard`); if that shard is `full/readonly/unhealthy` the new object rolls over to
  another shard while the Goal keeps its identity and shard. A Goal may therefore end up with
  knowledge on more than one shard; `object_routes` says where each object lives.
* Cross-goal objects keep **one** canonical home; they are exposed through the projections, never
  duplicated into every shard.
* Rollover lever: `python -m app.ingestion.admin shard-status K002 full`.

## Retrieval touches only the shards it needs

Projections carry `home_shard_id`. `hydrate_rows` groups candidate ids by shard and runs **one**
query per involved shard (concurrently), never one per id and never on uninvolved shards
(`test_candidates_on_multiple_shards_are_hydrated_in_one_batch_per_shard_and_untouched_shards_are_not_queried`).
`ShardPools` connects lazily from `dsn_env`, backs off a dead shard for 10 s (no connection
storm), and reports outages as `unavailable_shards` (never as "no such object").

## Projection consistency

Canonical write in the control DB ⇒ trigger `sl_canonical_touch` records the route and one
coalesced `projection_outbox` row **in the same transaction**. `drain_outbox` applies entries
idempotently under a per-object advisory lock (it always projects *current* canonical state), so
replay, duplicate drainers and out-of-order entries converge. Lag is observable
(`admin status`). Repair: `admin reindex [goal|claim|procedure|all] [--shard K]`; check:
`admin verify-projections` (missing / orphans / route mismatches / lag). Remote-homed objects are
read through `ShardPools`; a shard-side writer must call `search_projection.enqueue` after commit
(`reindex --shard` repairs a lost call).

## Remote canonical writes (migration 96)

Canonical rows really live in the shard databases. The control database used to foreign-key into
`procedures`/`goals`/`knowledge_nodes`; migration 96 **replaced** those FKs (it did not just drop them):

* `sl_check_ref` triggers: a reference (`procedures.achieves_goal_id`, `execution_plans.procedure_row_id`,
  `goal_relations.*`, `claim_sources.claim_id`, `goals.merged_into_id`, ...) is valid iff
  the object exists in this database **or** the global routing table homes it on another shard
  (`object_routes`, and `procedure_row_routes` for versioned procedure rows). Same-shard integrity is kept.
* `goal_names` is the **global unique index for exact goal identity** (a per-database unique index cannot see other shards).
* hard `DELETE` of a goal/procedure that is still referenced is refused (tombstone instead), replacing `ON DELETE`.
* `goals.home_shard_id` / `procedures.home_shard_id` are no longer local FKs on shard databases.
* `admin verify-refs` checks every routed object exists on its shard and every remote procedure's goal resolves.

Write path (public data): `find_or_create_goal` claims the name in `goal_names`, records the route, writes the goal on its
shard, projects it immediately (outbox repairs a failure). `capture_procedure` writes on the goal's shard (rolling over
if it is full/readonly), registers `object_routes` + `procedure_row_routes`. Versions (`supersede_procedure`) and
execution evidence (`record_execution_outcome`) run **on the procedure's home shard** (evidence, edges and change sets are
colocated with it, so the existing engine triggers and transactions still work). Claims (`claim_identity.ingest_claim`)
are placed with their goal. `merge_goal` repoints procedures on every shard. Retrieval hydrates only involved shards.

Provisioning a shard: `python scripts/migrate.py --dsn <shard dsn>` (same migrations), then
`python -m app.ingestion.admin register-shard K001 --dsn-env K001_DATABASE_URL` (checks the schema first).
Operators can stop remote placement instantly with `STEALTH_REMOTE_SHARD_WRITES=0`.

**Rules:** private/org rows always stay on K000 (so `data_rights`, personal sync and other private-only paths are complete on
the control database). Everything else that touches `procedures`/evidence/claims uses one of two mechanisms
(`services/shards.py`):

* **by id -> `home_pool(pool, "procedure", id[, by_row_id=True])`**: the row's home shard (one extra indexed lookup only when a
  remote shard is registered). Used by `get/supersede/record_execution_outcome/approve/reject`, `procedure_graph_api`
  (detail/versions/evidence), `fetch_procedure_version`, MCP `_resolve_live_procedure`, `local_sync`, admin reextract, publication
  withdraw/dependency targets, failure handlers, synthesis/extraction follow-up UPDATEs, `index_freshness.mark_procedure_indexed`.
* **scans -> `fanout_fetch / fanout_fetchrow / fanout_fetchval_sum / fanout_sum_row / fanout_best_row / all_pools`**: the same SQL on
  the control DB and every readable shard, then merged in Python. Lenient by default (an unreachable shard is skipped and logged);
  `strict=True` for writers and verifiers (`admin verify-dedup`, claim-ref backfill, episode idempotency) so a shard outage is never
  read as "nothing there". Used by goal resolution, `list_goal_procedures`, `get_goal`, `applicability` (legacy candidate legs, claim
  and evidence reads), `contributors`, `index_freshness`, extraction registry, replay, task/repository/product views, stealth
  projection, `claim_impact`, `procedure_claim_refs`.

Known approximations (documented, not silent): the legacy `applicability` ranker's id-only lexical/embedding legs are concatenated
per shard (each leg keeps its own order; cost/similarity legs that return the sort key are merged exactly). The canonical retrieval
path does not use them - it works from the global projections. Sharding is proven by `tests/test_sharded_readers_e2e.py` and
`tests/test_sharded_writes_e2e.py` against a second real PostgreSQL database.
