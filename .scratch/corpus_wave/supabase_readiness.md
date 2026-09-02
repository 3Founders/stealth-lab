# Supabase readiness — corpus wave Phase 3

Run 2026-09-02 ~14:30 UTC against branch `core-a/ingestion-testing` @ `5b2fc15`.
Target project ref `wckeklqxmiglivfolujn`, region `ap-south-1`, Postgres **17.6**.

## connection

| item | value |
|---|---|
| direct host `db.<ref>.supabase.co:5432` | **DOES NOT RESOLVE from this machine** — IPv6-only, `getaddrinfo failed`. Kept as `DATABASE_URL_DIRECT` in `backend/.env` for a host with IPv6 egress. |
| **working DSN** | session-mode pooler: `postgresql://postgres.wckeklqxmiglivfolujn:***@aws-0-ap-south-1.pooler.supabase.com:5432/postgres` — resolves over IPv4 (`3.111.105.85`), connects clean. |
| pooler mode | **session** (port 5432). One dedicated backend per client connection ⇒ safe for DDL, and asyncpg's default `statement_cache_size` is fine (no `=0` needed — that is only the `:6543` transaction pooler). |
| role | `current_user = session_user = postgres` (project owner). Owner is exempt from `FORCE ROW LEVEL SECURITY`, so migration 29's backstop is decorative on this connection — **commons-tenant posture, as documented**. To actually enforce tenant isolation on the hosted box, provision a non-owner app role (audit §8); not done for this proving wave. |
| `backend/.env` | updated: `DATABASE_URL` → session pooler; original preserved as `DATABASE_URL_DIRECT`. |

## extensions

`SELECT extname, extversion FROM pg_extension` →

| extension | version | required | ok |
|---|---|---|---|
| vector | **0.8.2** | ≥ 0.5.0 (5× `USING hnsw`) | ✅ |
| pgcrypto | 1.3 | present | ✅ |
| btree_gist | 1.7 | present (migration 16) | ✅ |

## migration state

`python scripts/migrate.py --dsn <session> --status` → all 34 pending on a fresh DB.
`python scripts/migrate.py` (run by operator) → **34/34 applied clean**, no checksum errors,
`CREATE EXTENSION` for vector/pgcrypto/btree_gist all succeeded. `schema_migrations` = 34 rows,
newest `34_evidence_stats_count_failures.sql`.

## schema

- 45 public base tables. Spot-check of core objects present: `knowledge_nodes`, `task_nodes`,
  `procedures`, `evidence`, `implementations` + `implementation_tasks` (migration 33),
  `change_sets` + `change_set_operations` (migration 25), `edges`, `observations`, `episodes`,
  `executions`, `execution_plans`, `task_graphs`, `ingestion_*`.
- `udt_name='vector'` columns (all `VECTOR(1024)`): `knowledge_nodes.embedding`,
  `task_nodes.embedding`, `task_nodes.embedding_joint`, `procedures.embedding`, `agents.embedding`.

## indexes

- HNSW cosine indexes: **exactly 5** — `idx_kn_embedding`, `idx_tn_embedding`,
  `idx_tn_embedding_joint`, `idx_procedures_embedding`, `idx_agents_embedding`.
- GIN FTS indexes: 4.
- `FORCE ROW LEVEL SECURITY` on 5 tables (`change_sets`, `change_set_operations`, `evidence`,
  `executions`, `failure_routes`), policy `tenant_isolation … sl_tenant_scope_allows(tenant_id)`.

## connection pooling

- App + migrations both use the **session** pooler string above. No prepared-statement hazard in
  session mode. If a future deployment switches to the transaction pooler (`:6543`), callers must
  pass `statement_cache_size=0` and migrations must still use a session/direct string.
- `tstzrange/2` `provolatile = 'i'` on this PG17 instance — the migration-16 index assumption holds.

## test status

### offline suite — not re-run this pass
`DATABASE_URL` unset, `python -m pytest tests -q` — 2335 tests collected. Not affected by the DB
move (only `DATABASE_URL` changed in `.env`); left to the operator.

### e2e + schema-drift against Supabase

**First run** (local Windows clock **8.9 s behind** the Supabase server clock —
`SELECT now()` = client `now()` + 8.9 s, measured twice, and independently against
Google/Microsoft/AWS `Date` headers which all agreed with the *server*, confirming the client
was the one adrift): **256 passed, 27 failed**, 5m35s. Every one of 26 failures followed
_insert a claim (row `t_valid = server now()`) → `project_state(as_of = client now())` → assert
live_; `project_state` filters `t_valid <= $2` with `$2` = client `datetime.now(timezone.utc)`
(`app/services/state.py:131`), so for ~9 s post-insert the row read as "future" and was excluded.
`test_state_e2e.py` failed 7/8 in isolation → not cross-test pollution.

**After the operator corrected the clock** (`w32tm` NTP config + resync; skew now **7 ms**,
verified against Google/MS/AWS `Date` headers *and* Supabase `now()` — all within ±0.8 s):
`DATABASE_URL=<session> python -m pytest tests -q -k "e2e or schema_drift"` →
**281 passed, 2 failed** (2052 deselected), 4m41s. **All 26 clock-skew failures cleared.**

Remaining 2 failures (neither is Supabase-portability, neither is the clock):

1. `test_claim_graph_overview_e2e.py::test_claim_graph_overview_against_real_postgres` →
   `KeyError: 'relation'`. Claim-graph viewer code from commit `92a6eee` (another session's
   work). Flagged to that session; out of scope for this branch.
2. `test_ingestion_admin_endpoint_e2e.py::test_admin_ingestion_endpoint_drives_real_traces_to_a_real_procedure_candidate`
   → `assert 0 >= 1`. Fails 1/2 in isolation → real, not pollution. The round-2 job drain reports
   `done >= 5` but produces **0 claims linked to the episode via `episode_links`**.
   `handle_promote_observation_to_claim` (`app/services/ingestion_jobs.py:180`) by design returns
   cleanly (job → `done`) when the observation has no resolvable `task_ids` **and** no
   `justification_episode_id` anchor, or when `promote_observation_to_claim()` returns `None`
   (logged at `:261`, not raised). So "5 done, 0 claims" is self-consistent — the enqueued
   promote jobs are not carrying (or not resolving) an anchor on this DB.
   **Ruled out:** clock (query filters `t_invalid IS NULL`, no `as_of`); embeddings
   (`Embedder().embed_one(...)` returns a real 1024-vec from this environment); schema (all
   tables/indexes present). **Likely:** the round-1 enqueue path
   (`handle_normalize_trace_event`) or `promote_observation_to_claim`'s own anchor-resolution
   query behaves differently here than on the local disposable-PG V1 gate. **Impact on this
   wave: low** — corpus ingestion is `skill_ingestion.py` (artifact → procedure), not the
   trace → observation → claim path this test exercises. Tracked as a follow-up, not a blocker.

## GO / NO-GO

| gate | state |
|---|---|
| connection (session pooler) | ✅ |
| extensions (vector ≥ 0.5.0, pgcrypto, btree_gist) | ✅ |
| 34/34 migrations applied, checksums clean | ✅ |
| schema + 5 HNSW indexes + VECTOR(1024) columns | ✅ |
| e2e substrate behaviour | ✅ **281 pass / 2 fail** post-clock-fix. Fail #1 = pre-existing claim-graph bug on `92a6eee` (other session). Fail #2 = `test_ingestion_admin_endpoint_e2e` trace→claim anchor follow-up (low impact — not the corpus path). |
| clock skew | ✅ resolved — client vs Supabase now **7 ms** |
| offline suite on Supabase-config | ⬜ operator to run |

**Verdict: substrate is healthy on Supabase.** Migrations, extensions, indexes, and 281/283
e2e all green. The clock skew that produced 26 spurious failures is fixed (`w32tm` NTP resync;
7 ms residual). Canonical ingestion / retrieval / staleness / execution phases are unblocked.
The one substrate-side follow-up (`test_ingestion_admin_endpoint_e2e`) is on the trace→observation→claim
path, not the corpus artifact→procedure path this wave ingests through, so it does not gate the wave.
