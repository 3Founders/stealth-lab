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

## Known blocker (honest)

`REMOTE_WRITES_SUPPORTED = False`. Reads, routing, projection and hydration of remote shards are
implemented and tested against a real second pool, but **canonical writes are restricted to the
home shard** because control-database tables foreign-key into `procedures`
(`execution_plans`, `procedure_implementations`, …) and `procedures` into `goals`
(`achieves_goal_id`). Putting a canonical row in another database would silently break those
constraints. Enabling remote writes requires turning those edges into application-validated
references (a migration + call-site changes), then flipping the flag. Until then every registered
shard other than K000 can serve reads/hydration but is never chosen for new objects.
