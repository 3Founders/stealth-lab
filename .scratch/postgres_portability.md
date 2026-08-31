# Phase 1 — PostgreSQL Portability Audit

Written 2026-08-31, against `main` @ `a1d8519`. Real inspection of
`app/db/`, `scripts/migrate.py`, `app/config.py`, and every `db/*.sql`
file's extension/index/lock/trigger usage — not assumed from architecture
docs. Target, per the directive: application → PostgreSQL, with local and
hosted (Supabase/Neon-class) Postgres as interchangeable deployment
environments. **PostgreSQL itself is not being replaced or abstracted
away** — this audit exists to find what would break on a *different
Postgres deployment*, not to build a database-agnostic layer.

No migration was run against any hosted instance in this pass, per the
directive's own instruction ("do not perform the actual production
migration unless explicitly requested").

---

## 1. Classification

### Portable — works identically on local, Supabase, Neon, or any stock
### Postgres 15+ with no configuration change

- **All DDL** (`db/*.sql`): every migration uses `CREATE TABLE IF NOT
  EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and
  `DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$` for enum
  types — no destructive statement anywhere in `db/`, confirmed by a full
  file-by-file scan. Idempotent by construction, safe to run against an
  empty database or an already-provisioned one (the exact property
  `scripts/migrate.py`'s own ledger depends on).
- **Recursive CTE**: exactly one site, `app/db/graph_store.py:153`
  (bounded frontier-expansion traversal, `WHERE f.depth < $3` — no
  unbounded recursion). Standard ANSI-ish recursive CTE syntax, no
  Postgres-version-specific extension. Portable as written.
- **JSONB usage**: universal throughout (`properties`, `steps`, `scope`,
  `verification_stats`, etc.) via the real client-side codec registered
  in `app/db/session.py::_init_connection` (`set_type_codec("jsonb", ...)`)
  — this is asyncpg-side, not server-side, so it is identical on every
  Postgres deployment; the only failure mode is a caller bypassing this
  pool's `init` hook (e.g. a raw `asyncpg.connect()` elsewhere), which
  this pass found none of outside `scripts/migrate.py` (which never reads
  JSONB back, only executes DDL — no codec needed there).
- **Locking**: 7 real `FOR UPDATE` sites, all inside explicit
  `async with conn.transaction()` blocks (row-level locking is core
  Postgres behavior, not provider-tunable).
- **Trigger functions**: 6 `CREATE FUNCTION`/`CREATE TRIGGER` pairs
  (append-only enforcement on `execution_plans`/`task_graphs`/`executions`/
  etc., migration 23 and others) — plain PL/pgSQL, no extension
  dependency beyond core Postgres.
- **`btree_gist` / `pgcrypto` extensions**: both are standard PostgreSQL
  contrib modules, pre-installed and allow-listed on Supabase and Neon
  without superuser (confirmed against both providers' own published
  extension allow-lists as of this writing — **not independently tested
  against a live instance this pass**, flagged below).
- **RLS backstop** (`db/29_rls_backstop.sql`): mechanism (`FORCE ROW
  LEVEL SECURITY` + `current_setting('app.tenant_id', true)` bound via
  transaction-local `set_config`) is standard Postgres RLS, not a
  provider extension. Permissive-when-unset by design (Phase 0 finding,
  unchanged here) — behaves identically everywhere until adoption is
  swept wider, which is an application-layer decision, not a portability
  one.

### Provider-specific — works on Supabase/Neon-class hosted Postgres,
### but needs a real, named configuration choice, not "just works"

- **pgvector `HNSW` index type** (`db/01_ontology.sql:122`, `:127`,
  `07_agents.sql:71`, `11_fix_embedding_joint_drift.sql:24`,
  `19_procedures_embedding.sql:15` — 5 sites): HNSW indexing was added in
  **pgvector 0.5.0**. This is not "does the provider have pgvector" (both
  do) — it is "does the provider's installed pgvector version support
  `USING hnsw`." Supabase has shipped pgvector ≥0.5.0 for a while and
  supports HNSW; Neon added pgvector later and version currency should be
  confirmed against the specific project's extension version before
  migrating, not assumed from "Neon supports pgvector" alone. **Action
  for whoever runs the real hosted migration**: `SELECT extversion FROM
  pg_extension WHERE extname = 'vector';` against the target instance
  BEFORE running migration 01, and require ≥0.5.0.
- **asyncpg + PgBouncer transaction-mode pooling (the single most
  concrete, real risk found this pass)**: `app/db/session.py::create_pool`
  uses asyncpg's default server-side prepared-statement caching
  (unchanged from library defaults). Both Supabase's "Transaction pooler"
  connection string and Neon's pooled endpoint front the real Postgres
  instance with PgBouncer in **transaction mode**, under which
  server-side prepared statements are a well-documented, real failure
  mode (`prepared statement "..." does not exist` / duplicate-statement
  errors under concurrent pooled connections) — this is not speculative,
  it is asyncpg's own documented incompatibility with transaction-mode
  poolers. **The fix requires no code change**: `create_pool(dsn, **kwargs)`
  in `app/db/session.py` already forwards arbitrary kwargs to
  `asyncpg.create_pool`, which forwards unrecognized ones to
  `asyncpg.connect` — so `create_pool(dsn, statement_cache_size=0)`
  already works today, undocumented. **Action**: when deploying against a
  pooled Supabase/Neon connection string, callers MUST pass
  `statement_cache_size=0` (or use the provider's *direct*/session-mode
  connection string instead, which has no such restriction and is what
  `scripts/migrate.py` should always use regardless, since DDL inside a
  pooled transaction-mode connection is its own separate hazard). This is
  purely an operational/documentation gap today, not a code defect —
  closed in §3 below.
- **`FORCE ROW LEVEL SECURITY` + owner-role connections**: the real
  effect of `FORCE RLS` depends on connecting as a non-owner role, or
  Postgres treats the table owner as exempt regardless (migration 29's
  own comment states this correctly). Most hosted-provider quick-start
  connection strings connect as the **project owner role**
  (`postgres`/the Supabase `postgres` role, Neon's default role) — which
  means `FORCE RLS` is currently decorative on a fresh hosted project
  exactly the way the migration's own comment warns about for a
  same-privilege local setup, UNLESS the deploying operator deliberately
  provisions and connects as a non-owner application role. This is a
  real, named deployment decision (not a code gap) that needs to be made
  explicitly before RLS enforcement can be trusted on a hosted instance —
  flagged here so it isn't silently assumed to already hold.
- **`tstzrange` immutability** (`db/16_state_projection_index.sql`'s own
  documented verification): the migration's comment states it confirmed
  `provolatile='i'` for `tstzrange` on "this Postgres 16 instance"
  specifically. This is core builtin Postgres behavior (not
  provider-configurable), so it should hold identically on any standard
  Postgres 15+ including hosted ones — but the migration file's own
  wording frames it as an instance-specific check, so the honest
  classification is "should be portable, re-verify against the actual
  target instance once before trusting it in production," not "proven
  portable."

### Local-only — nothing found

No code path in `app/` or `db/` assumes a local-filesystem-only Postgres
feature (e.g. `COPY FROM PROGRAM`, local-file `COPY`, filesystem-based
large objects). This is a genuinely clean result, not a gap the audit
missed — confirmed by grep across `app/` and `db/` for `COPY`, `lo_import`,
`pg_read_file`, and `dblink`, all zero matches.

### Requires deployment configuration (not code, not provider-specific —
### operator decisions every deployment needs to make once)

1. `DATABASE_URL` must carry `sslmode=require` (or equivalent) for any
   hosted provider — `app/config.py`'s `database_url` field is a bare
   `Optional[str]`, no default sslmode is injected anywhere in
   `app/db/session.py`. asyncpg parses `sslmode` from a libpq-style DSN
   string natively, so this needs no code change — but it is a real,
   easy-to-forget operator step (a bare `postgresql://user:pass@host/db`
   without `?sslmode=require` will be refused by both Supabase and Neon).
2. The role in `DATABASE_URL` needs `CREATE EXTENSION` privilege for
   `vector`/`pgcrypto`/`btree_gist` the FIRST time migrations run against
   a fresh project — both providers allow-list these for the default
   project role, but a custom least-privilege application role (a real,
   good practice for §2 RLS enforcement above) would need
   `CREATE EXTENSION` revoked and the extensions pre-installed by an
   owner-role bootstrap step instead. This is exactly the tension between
   "migrations run unattended" and "the app connects as a least-privilege
   role" — a real deployment-process decision, not something this audit
   can resolve unilaterally.
3. `scripts/migrate.py` should always be pointed at the provider's
   *direct* (non-pooled) connection string, never the pooled one — DDL
   (`CREATE INDEX`, `ALTER TABLE`) under a transaction-mode pooler is a
   second, independent hazard beyond the prepared-statement issue in §2
   (a pooled connection can be silently handed to a different backend
   mid-multi-statement-transaction under some pooler configurations).
   `migrate.py --dsn <url>` already supports pointing it anywhere
   (confirmed, `scripts/migrate.py`'s own `--dsn` flag) — this is a
   usage instruction, not a missing capability.

---

## 2. Migration additivity check (directive's explicit ask)

> Ensure new migrations: are additive where possible; preserve existing
> constraints; preserve provenance; work on an existing DB; work on an
> empty DB; have appropriate indexes; do not require destructive reset.

All 32 files in `db/` were already re-scanned for this pass (not just
migration 32 — every file, since the directive asks about "new
migrations" as an ongoing discipline this repo should keep, not a
one-time check of the newest file). Result: **zero destructive
statements found** (`DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, or a bare
`ALTER TYPE ... RENAME` outside an idempotent guard) across all 32
files. `scripts/migrate.py`'s checksum-mismatch hard-error (§ found in
Phase 0) is the real enforcement mechanism keeping this true going
forward — a migration cannot be silently edited after being applied
without the ledger catching it. This property already holds; nothing to
fix here, only to keep true.

---

## 3. Documentation fix landed this phase (no schema/code change)

`app/db/session.py::create_pool` already accepts `statement_cache_size=0`
via its existing `**kwargs` passthrough — the gap was that nothing in the
repository said so, so a future hosted deployment against a pooled
connection string would hit the PgBouncer prepared-statement failure mode
(§1) with no documented fix. Added a real docstring note at the exact
site a deploying operator would read next to `create_pool` itself,
pointing at this file for the "when do I need this" explanation, rather
than leaving it buried only here.

---

## 4. What this phase did NOT do (explicit, per the directive's own
## "do not perform the actual production migration unless explicitly
## requested")

- No connection to any real hosted Supabase/Neon instance was made.
- `SELECT extversion FROM pg_extension WHERE extname='vector'` was not
  run against a hosted target (no target exists yet) — listed as the
  first real command to run once one does.
- The RLS non-owner-role deployment decision (§1) was not made — it is a
  product/ops decision (does this release actually need enforced
  multi-tenant isolation on a hosted instance, or does the commons-tenant
  posture ship as-is) that belongs to whoever authorizes the hosted
  migration, not to this audit.

---

## Phase 1 verdict

**Audit complete.** The application already targets plain PostgreSQL with
no code-level portability blockers found. The one real, concrete risk
(PgBouncer + asyncpg prepared statements) has a zero-code-change fix that
is now documented at its call site. The two genuine open items —
confirming the target instance's real pgvector version, and deciding the
RLS role-separation posture — are both **operator decisions gated on
having an actual target instance**, not implementation work this phase
can complete in the abstract. No schema or application-behavior change
was made this phase; the one edit (§3) is a comment.

**Recommendation for Phase 2**: proceed. Nothing in this audit blocks the
claim-graph work — it is orthogonal to deployment target.
