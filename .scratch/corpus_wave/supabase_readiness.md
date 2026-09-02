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
`DATABASE_URL=<session> python -m pytest tests -q -k "e2e or schema_drift"` →
**256 passed, 27 failed** (2052 deselected), 5m35s.

**Root cause of 26 of the 27: local clock skew, not the substrate.**
The Windows client clock is **~8.9 s behind** the Supabase server clock
(`SELECT now()` = client `now()` + 8.9 s, measured twice). Every failing test follows the shape
_insert a claim (row gets `t_valid = server now()`) → call `project_state(as_of = client now())` →
assert the claim is live_. `project_state` filters `t_valid <= $2` with `$2` = the client's
`datetime.now(timezone.utc)` (`app/services/state.py:131`), so for ~9 s after each insert the
just-written row is "in the future" and is excluded. Same mechanism drives every failure in
`test_state_e2e`, `test_claim_traversal_e2e`, `test_environment_probe_e2e`,
`test_precondition_*_e2e`, `test_procedure_extraction*_e2e`, `test_applicability_e2e`,
`test_staleness_selection_e2e`, `test_tms_readability_e2e`. `test_state_e2e.py` run in isolation
still fails 7/8 → confirmed not cross-test pollution.

**Fix (operator, client-side):** correct the Windows clock —
`w32tm /resync` from an elevated prompt, or Settings → Time & Language → "Sync now" with
"Set time automatically" on. An 8.9 s skew will also affect JWT `iat`/`exp`, rate-limit windows,
and any client-issued `as_of`. After the clock is fixed, re-run the e2e cluster; expect the 26 to
clear.

**The 27th failure is unrelated to both Supabase and the clock:**
`test_claim_graph_overview_e2e.py::test_claim_graph_overview_against_real_postgres` →
`KeyError: 'relation'`. This is in the claim-graph viewer code from commit `92a6eee` (a different
session's work). Flagged to that session; out of scope for this branch.

## GO / NO-GO

| gate | state |
|---|---|
| connection (session pooler) | ✅ |
| extensions (vector ≥ 0.5.0, pgcrypto, btree_gist) | ✅ |
| 34/34 migrations applied, checksums clean | ✅ |
| schema + 5 HNSW indexes + VECTOR(1024) columns | ✅ |
| e2e substrate behaviour | ✅ **256 pass**; 26 "failures" are client clock skew (fix `w32tm /resync`), 1 is a pre-existing claim-graph bug on `92a6eee` |
| offline suite on Supabase-config | ⬜ operator to run |

**Verdict: substrate is healthy on Supabase.** The blocker for the execution/evidence phases is
the **9-second client clock skew** — fix that before canonical ingestion and any timestamp-sensitive
retrieval/staleness/execution work, or those paths will be intermittently wrong in the same way.
Offline corpus extraction (Phases 1–2, 6, 8) is unaffected and can proceed now.
