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

## Dynamic growth

A capacity manager in the worker's maintenance loop (extends `shard_capacity`): for each role, when the free
headroom of writable members falls under a threshold, it creates a Neon project through the API
(`scripts/provision_neon_shards.py` logic), migrates it, registers it (`knowledge_shards.role` = knowledge | search)
and opens it to traffic; it keeps one spare per role and never exceeds a configured maximum. A is never grown;
it raises `control_database_near_capacity` like today.

## What must not change

- Retrieval results: the golden retrieval tests pass unchanged, and a side-by-side comparison of the single-database
  layout vs the sharded one on the same data shows the same top results (quality) and acceptable latency.
- Exactly-once and dedup guarantees (name registry, routes and outbox stay in one transaction on A).

## Existing production data

The ~2,200 items ingested before this change are re-ingested into the new layout (the ledger makes it idempotent;
~1 hour, ~$10), then K000's public knowledge is removed so A holds only control data.
