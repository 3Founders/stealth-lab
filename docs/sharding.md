# Sharding

Sharding is **storage sharding**, not semantic. It never follows the Goal hierarchy:
a Goal's home is chosen by weighted rendezvous hashing and never moves; the Goal DAG
(`goal_relations`) is one canonical table on the control database.

Every database is a hosted project with a hard storage cap (today: Neon, 500 MB each),
so the control plane itself is split in two.

## Layout

```
CONTROL PROJECT A  (the control database, shard "K000")        joined or transactional together
  knowledge_shards            registry: shard_id, status, weight, dsn_env (NAME of an env var), capacity_bytes
  object_routes               (object_type, object_id) -> home_shard_id                 [global routing]
  procedure_row_routes        procedure version row -> home shard
  goal_names                  global exact-name identity index (scope, normalized name) -> goal, shard
  goal_relations              the Goal DAG (proposed / accepted / rejected SPECIALIZES edges)
  goal_search_index           Goal projection (+ derived resolved_at, t_created)       [joined with goal_relations]
  goal_abstraction_state      derived levels / coverage
  projection_outbox           pending projection refreshes (same transaction as the canonical write)
  ingestion_jobs              lease-based job queue
  benchmarks, benchmark_submissions, solutions, evaluations, benchmark_transfer_decisions
  credit_ledger_events + economy tables (append-only ledger)
  routing_models, routing_prices, routing_params, routing_posteriors   model recommender (draws, prices)
  all PRIVATE / ORG canonical Goals, Procedures, Claims (never leave K000)

CONTROL PROJECT B  (search/log database, SEARCH_DATABASE_URL)  nothing joins to these
  procedure_search_index      Procedure projection (FTS + ANN)
  claim_search_index          Claim projection (FTS + ANN)
  retrieval_decisions         per-request retrieval record (+ shard fan-out / latency telemetry)
  identity_decisions          every Goal/Claim/Procedure identity judgment (idempotent replay per key)
  llm_spend                   model-cost ledger (CostGovernor, ingest budget)
  routing_observations        model-recommender attempt outcomes (never pruned: the fit uses all of them)
  routing_decisions           model-recommender decision log with propensities (off-policy evaluation)

KNOWLEDGE SHARDS  K001 .. K0nn   canonical PUBLIC knowledge, each object once:
  a Goal + the Procedures (all versions) that achieve that exact Goal + their execution
  evidence, edges, change sets + the Goal's Claims
```

With `SEARCH_DATABASE_URL` unset, project B's five tables simply live on A (a single-database
deployment is unchanged and pays nothing: `shards.search_pool(pool)` returns `pool` itself).
`shards.search_pool` is the ONE accessor for B; `shards.SEARCH_DB_TABLES` lists what lives there.

Why this split: A holds everything that is joined in SQL (hierarchy walks join `goal_relations` with
`goal_search_index`; browsing and routing join routes and names) or written in the same transaction
as a canonical row (routes, names, outbox). B holds tables nothing joins to and no canonical write
shares a transaction with: projections are rebuilt from the outbox on A, and the three logs are
written by single idempotent statements.

## Placement (`app/services/shards.py`)

* New Goal -> `choose_shard(goal_id, writable_shards)`: weighted **rendezvous hashing** over
  `status='active' AND weight>0` shards. Deterministic; adding a shard only moves the keys that hash to
  it (existing objects never move -- `home_shard_id` is stored). Hierarchy never influences placement.
* A Goal's Procedures and Claims go to the Goal's shard (`choose_child_shard`); if it is
  `full/readonly/unhealthy` the new object rolls over to another shard, the Goal keeps its shard.
* Private/org rows always stay on K000.
* **Capacity guard** (`app/services/shard_capacity.py`): every worker loop (and `admin shard-capacity`)
  measures each shard against `knowledge_shards.capacity_bytes` and marks a remote shard `full` at
  `STEALTH_SHARD_FULL_RATIO` (default 0.85) -- new objects roll over before the provider refuses writes.
  K000 is never marked full (private data has no other home); it is reported as
  `control_database_near_capacity`.

## Reads touch only the databases they need

* **By id / exact name / owning Goal** (`app/services/routed_reads.py`, `home_pool`): the object's home
  shard, via `object_routes`, `goal_names` or the procedure projection -- never "ask every shard", never
  "look only in the control database".
* **Many known ids**: `hydrate_rows` groups ids by shard, one query per involved shard, concurrently,
  each bounded by `STEALTH_SHARD_READ_TIMEOUT_S` (a slow shard is reported unavailable, not waited on).
* **Genuine scans** (no id: counts, Claims by subject, legacy applicability legs): `fanout_*` helpers run
  the same SQL on every readable shard, bounded per shard.
* **Hierarchy** traversal runs entirely on A (`goal_relations` + `goal_search_index`); only the Goals
  actually displayed / judged are read from their shards.
* **Telemetry**: `shards.track_shard_requests()` records distinct shards, targeted hydrations vs
  broadcasts, route lookups, per-shard latency, timeouts and cold connects; `find_best_way` and
  `find_ways` store it in `retrieval_decisions.detail.shard_requests`.

## Projection consistency

A canonical write on K000 records its route and one coalesced `projection_outbox` row **in the same
transaction** (trigger `sl_canonical_touch`); remote writers enqueue after commit. `drain_outbox`
applies entries idempotently under a per-object advisory lock on A: Goal projections are written on A,
Procedure/Claim projections on B. The outbox entry is marked applied only after the projection write,
so a failed write to B leaves it pending and it is retried -- never lost. Repair:
`admin reindex [goal|claim|procedure|all] [--shard K]`; check: `admin verify-projections` (for B-hosted
types it compares id sets across the two databases instead of joining).

## Remote canonical writes (migration 96)

Cross-object FKs are replaced by route-aware validation triggers (`sl_check_ref`); `goal_names` is the
global unique index for exact Goal identity; hard `DELETE` of a referenced Goal/Procedure is refused;
`admin verify-refs` checks every routed object exists on its shard.

## Runbook

Provision many knowledge shards at once (Neon projects K001..K100: create, migrate, register; idempotent, resumable):

```
export NEON_API_KEY=...   DATABASE_URL=<control db>
python scripts/provision_neon_shards.py --count 100 --region <app region> --dry-run
python scripts/provision_neon_shards.py --count 100 --region <app region> --weight 0
```

Connection strings go to `backend/.neon_shards.env` (git-ignored); load them into every API / MCP / worker
process, then raise weights (`admin shard-weight K001 100` ...) when ready.

Provision a single knowledge shard by hand:

```
python scripts/migrate.py --dsn <shard dsn>
python -m app.ingestion.admin register-shard K001 --dsn-env K001_DATABASE_URL --capacity-bytes 524288000
```

Bring up the search/log database (control project B):

```
python scripts/migrate.py --target search --dsn <B dsn>     # creates only B's five tables (migration 117)
export SEARCH_DATABASE_URL=<B dsn>                           # every API / MCP / worker process
python -m app.ingestion.admin search-db-backfill             # reindex procedure + claim projections into B, verify
```

The three logs start empty on B (history stays on A until pruned or dropped by hand).

Keep new public Goals off the control database once remote shards exist:

```
python -m app.ingestion.admin shard-weight K000 0            # K000 keeps private/org data and existing rows
```

Keep the control databases small (schedule it, e.g. daily):

```
python -m app.ingestion.admin prune-operational --older-than-days 30 --apply
```

(prunes only finished rows: old `retrieval_decisions`, applied `projection_outbox` entries, done/cancelled
`ingestion_jobs`; never canonical data, relations, the Credit ledger or identity decisions.)

Watch capacity: `python -m app.ingestion.admin shard-capacity` (report) / `--apply` (the worker does this
every loop). Stop remote placement instantly with `STEALTH_REMOTE_SHARD_WRITES=0`.

Sharding is proven by `tests/test_sharded_*_e2e.py`, `tests/test_shard_capacity_e2e.py` (two real
databases) and the A/B split by `tests/test_search_database_e2e.py`.
