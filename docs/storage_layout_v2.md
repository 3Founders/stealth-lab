# Storage layout v2: everything on free Neon projects (512 MB each)

Status: design, approved in outline by Anuj on 2026-09-30 ("do slimming"), to be reviewed by Chaitanya.
Extends `docs/sharding.md`; nothing there is contradicted, three things are completed or added.

## Why

The first full production ingestion (2026-09-30) filled the only database (neondb, 512 MB free plan) at ~2% of
the run. `docs/sharding.md` already puts public knowledge on knowledge shards K001..K0nn, but it was only partly
implemented and it assumes the control database (A) and the search/log database (B) each stay under 500 MB. Measured
on production and scaled to the full run of the 8 sources (~70k items, ~130k Goals, ~450k routed objects):

| today, all in one database | bytes per unit (measured) | full run |
|---|---|---|
| claims (`knowledge_nodes`, vector + HNSW) | ~15 KB per claim | ~5 GB |
| claim search index | ~15 KB per claim | ~5 GB |
| Procedures + search index | ~20 KB + ~17 KB | ~2.5 GB |
| Goals + search copy | ~16 KB + ~6 KB | ~3 GB |
| ingestion bookkeeping (ledger, contexts, artifacts, raw objects, task registry) | ~7 KB per item | ~0.5 GB |
| routes, names, edges, jobs | ~2 KB per object | ~0.3 GB |

Three gaps in the current implementation, found while measuring:
1. **Claims are always written to the control database** (`claims.capture_claim` has no shard routing).
2. **Benchmarks keep a physical foreign key to `goals`** (`benchmarks_goal_id_fkey`, migration 110), so a Benchmark for
   a Goal on a remote shard cannot be written; migration 96 replaced the other cross-object keys, not this one.
3. **Trajectory data** (traces, episodes, extractions) is written to the control database; ~230 KB per OpenHands run.

## Roles

**A -- control (1 project, target < 420 MB at full scale).** Only what is joined in SQL or written in one
transaction with a canonical object:
- the slim Goal list `goal_search_index` **without** `embedding`, `search_tsv`, `search_text`, `short_description`
  (6.1 KB -> ~0.6 KB per Goal): id, name, status, scope, visibility, owner, has_procedures, resolved_at, home shard;
- `goal_names`, `goal_relations` (accepted + under review; stale proposed edges pruned), `goal_abstraction_state`,
  `goal_review_items`, `object_routes`, `procedure_row_routes`, `knowledge_shards`, `ingest_task_goals`;
- `ingestion_jobs` and `projection_outbox` (finished rows pruned daily: `admin prune-operational`);
- users, auth, service identities, audit, the credit ledger.

**B -- search and logs (a dynamic group of projects, S001, S002, ...).** Nothing here is joined or written in a
transaction with canonical rows:
- `goal_search_docs` (new): a Goal's searchable text, full-text vector and embedding (moved out of A);
- `procedure_search_index`, `claim_search_index`;
- `identity_decisions`, `llm_spend` (pruned to what the budget needs), `retrieval_decisions`,
  `routing_observations`, `routing_decisions`, `routing_params`, `routing_posteriors`, `ingest_ledger`.

A new row goes to one member (weighted rendezvous hashing over members that are not full, keyed by the object id,
recorded in `object_routes.search_shard_id` so later updates find it). **Reads ask every member in parallel and
merge**: text-search ranks (`ts_rank_cd` uses no corpus statistics) and vector distances are comparable across
members, so the merged top-k is the true top-k; lookups by key are indexed on every member. Because reads always
fan out, a member can be added at any time and nothing is ever rebalanced.

**K -- knowledge shards (K001, K002, ... dynamic).** Unchanged placement (a Goal by rendezvous hashing, its children
with it). Completed so that everything that belongs to a Goal lives with it:
- claims (`knowledge_nodes`, `claim_sources`, `procedure_claim_refs`) -> the Goal's shard;
- Benchmarks, solutions, evaluations -> the Goal's shard (the FK becomes the route-aware check of migration 96);
- evidence, ingested artifacts, raw objects, ingestion contexts -> the shard of the object they describe;
- trajectory data -> the shard of the run's task Goal (the pipelines name the task Goal before writing the run).

## Dynamic growth (as built: `app/services/storage_autoscale.py`)

The worker's maintenance pass runs the capacity guard (`shard_capacity`: a database at 85% of its limit is marked
`full` -- it keeps its rows and stays readable, it takes no new placements), then `autoscale`: each role that the
deployment uses keeps `STEALTH_MIN_WRITABLE_KNOWLEDGE` (4) / `STEALTH_MIN_WRITABLE_SEARCH` (2) writable databases with
headroom. Short of that it first opens an idle registered database of the role (weight 0 -> 100), else creates a Neon
project (`scripts/provision_neon_shards.py --kind knowledge|search`: create, migrate, mark its role), registers it at
weight 0, waits out the registry cache every process holds, then weights it -- so no reader ever misses a row placed on
a database it does not know yet. New connection strings go to the git-ignored `backend/.neon_shards.env`, which
`shards.shard_dsn` reads when a variable is not in the environment: running processes reach a new database without a
restart. `STEALTH_STORAGE_AUTOSCALE=0` turns it off; tests always do. A is never grown.

## As built (2026-10-01)

- B group: `search_group` (placement recorded in `search_routes`; reads ask every member and merge by the query's own
  order). Identity, dedup, `has_procedures` and budget reads are STRICT: an unreachable member fails the read, as one
  database that is down does -- never a decision on a partial view. Read-only user paths (retrieval legs, product
  search) answer from the members that can.
- On A the Goal row is slim (no text, tsvector or vector); readers that rank by text or vector (hierarchy neighbours,
  product search, more-specific Goals, routing priors) take those scores from `goal_search_docs` and apply the
  single-database ordering exactly.
- Benchmarks, solutions, evaluations, submissions, usage events stay on A (measured: 2.8 MB for 589 benchmarks); their
  Goal references are route-aware checks (migration 133). Evidence lives with its Procedure on the Procedure's shard.
  Claim refs, ledger, contexts, artifacts stay on A (~20 KB per ingested item in all).
- A knowledge shard / search member is marked in `sl_database_role` (migration 133) so its triggers keep no
  control-plane rows of their own.

## What must not change -- and how it is checked

- `tests/test_search_group_equivalence_e2e.py`: one corpus, every read path the group touches run on one database and
  then on a search group -- retrieval, hierarchy ranking, product search pages, more-specific Goal order, routing
  vectors, projection checks -- must be IDENTICAL (each fix is proven by a mutant that makes it fail).
- `tests/test_verified_write_sharded_e2e.py`: the verified pipeline's `write_task` in the production layout: every row
  where its reader looks; retrieval counts the benchmark support read from the Procedure's shard; a retry reuses all.
- The full suite on the pre-change code vs the new code, single database and search group: no test that passes before
  fails after.
- Exactly-once and dedup guarantees (name registry, routes and outbox stay in one transaction on A).

## Existing production data -- cutover runbook

The 2,213 items ingested before this change ($22 of model spend) are wiped and re-ingested into the new layout
(approved by Anuj, 2026-10-01): the ledger is wiped with them, so the verified pipeline starts over.

1. stop ingestion; `scripts/storage_v2_prepare_shards.py` (every K/S database: migrations, ledger correction for
   132/133, role marker);
2. wipe what ingestion wrote on A (accounts, credentials, config, audit, spend ledger, registry and schema kept);
3. `scripts/migrate.py` on A (132, 133);
4. `provision_neon_shards.py --kind search --count 2` (search members), re-run the knowledge provisioning to register
   the remaining K shards;
5. `scripts/storage_v2_move_logs.py` (the spend ledger moves onto the members, sequences advanced -- the budget cap
   keeps counting today's spend);
6. K000 weight 0; the worker's autoscale opens the knowledge shards it needs; resume the pipelines.
