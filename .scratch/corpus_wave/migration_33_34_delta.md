# Migration 33 / 34 portability delta

Agent H, corpus-ingestion wave, 2026-09-02. Offline. No DB connection made.

Extends `.scratch/postgres_portability.md` (2026-08-31, covered migrations
01-32). This file re-scans **only** `backend/db/33_implementation_registry.sql`
and `backend/db/34_evidence_stats_count_failures.sql` against the same
criteria the Aug-31 audit used.

Files were read line by line. Every claim below cites `file:line`.

---

## Criteria checklist

| Criterion | 33 (`33_implementation_registry.sql`) | 34 (`34_evidence_stats_count_failures.sql`) |
|---|---|---|
| Destructive statement (`DROP` / `TRUNCATE` / rename outside guard) | **None.** `CREATE TABLE IF NOT EXISTS implementations` (`33:52`), `CREATE TABLE IF NOT EXISTS implementation_tasks` (`33:228`). No `DROP`/`TRUNCATE`/`RENAME` anywhere in the file. | **None.** Single `CREATE OR REPLACE VIEW procedure_evidence_stats` (`34:52`). No `DROP VIEW`, no `ALTER`. |
| Non-idempotent DDL | **None.** Tables are `IF NOT EXISTS`. Every constraint is added inside `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '...') THEN ALTER TABLE ... ADD CONSTRAINT` (`33:137-196`). Every index is `CREATE INDEX IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS` (`33:202-240`). Safe to re-run against empty or populated DB (`33:37-39` states this intent). | **Idempotent by `CREATE OR REPLACE`** (`34:8-9` states this, citing 24_evidence.sql's note that PG has no `CREATE VIEW IF NOT EXISTS`). |
| Provider-specific extension | **None.** No `CREATE EXTENSION` in the file. `gen_random_uuid()` is used as a column default (`33:53`, `33:229`); it is supplied by `pgcrypto`, already created in `01_ontology.sql:5` (and is core in PostgreSQL 13+ regardless). No new extension dependency introduced. | **None.** No extension usage. |
| Unbounded recursive CTE | **None.** No CTE at all. | **None.** Flat `SELECT ... FROM evidence e JOIN procedures p ... GROUP BY` (`34:52-78`). No recursion. |
| Superuser-only operation | **None.** No `CREATE EXTENSION`, no `ALTER SYSTEM`, no `CREATE ROLE`, no `COPY`, no `pg_read_file`. All DDL is table-owner-level. | **None.** View replace is table-owner-level. |
| Prepared-statement / pooler hazard | **None new.** Pure DDL file run by `scripts/migrate.py`, which opens its own `asyncpg.connect(dsn)` (`scripts/migrate.py:97`) and must be pointed at the **direct** connection string (carried-forward rule from Aug-31 §1/§3). Nothing in 33 changes the app's runtime query surface. | **None new.** Redefining a view changes no client prepared-statement behaviour; `procedure_evidence_stats` is queried the same way before and after. |
| New index type | **No.** All five indexes in 33 are plain B-tree: one `CREATE UNIQUE INDEX idx_implementations_identity ON implementations(name, provider, version)` (`33:202-203`), plus `idx_implementations_status` / `_provider` / `_kind` / `_scope` (`33:208-217`) and a **partial** B-tree `idx_implementations_content_hash ON implementations(content_hash) WHERE content_hash IS NOT NULL` (`33:214-215`). Partial B-tree is core PostgreSQL, portable to every Postgres 15+ / Supabase / Neon. `implementation_tasks` adds two plain B-tree indexes (`33:237-240`). No `hnsw`, no `ivfflat`, no `gin`, no `gist`. | **No.** Creates no indexes. |
| Other portability-relevant constructs | `derived_from UUID REFERENCES implementations(id)` self-FK (`33:121`); `implementation_id`/`task_node_id` FKs to `implementations`/`task_nodes` (`33:230-231`); `visibility visibility_level NOT NULL DEFAULT 'public'` reuses the existing enum from earlier migrations (`33:129`); one `CHECK ... NOT VALID` constraint `scope_type_chk_implementations` (`33:190-195`). `NOT VALID` and self-FKs are core PostgreSQL, identical on all targets. `CREATE OR REPLACE VIEW` requires the replacement column list to be a superset in the same leading order — this is uniform Postgres behaviour, not provider-specific; a same-file re-run is safe. |

---

## Verdict on the delta

**No new code-level portability blockers introduced by migration 33 or 34.**

Both files hold the same discipline the Aug-31 audit verified across 01-32:
additive, idempotent, guarded DDL; no destructive statements; no new
extension, index type, recursive CTE, or superuser operation. The
`schema_migrations` ledger row count moves from 32 to **34** after applying
these two (35 physical files exist in `backend/db/` — migration 15 is
deliberately absent, migration 08 is split into `08a`/`08b` — so the ledger
holds one row per file: 34 rows expected at head).

Carried forward unchanged from Aug-31 (not re-verified this pass, because
no target instance exists): the two operator decisions — confirm target
`vector` extension >= 0.5.0 before migration 01, and decide the
non-owner-role / `FORCE RLS` posture — and the asyncpg + transaction-mode
pooler `statement_cache_size=0` rule for the **app** connection string.
