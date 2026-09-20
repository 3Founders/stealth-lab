# Auth vs. moving canonical data from Supabase Postgres to Neon

**Auth does not migrate.** Supabase Auth keeps issuing JWTs; Stealth verifies them and enforces authorization in the backend. Only data moves.

## Audit of Supabase-specific database dependencies

Checked over every `backend/db/*.sql` (enforced by `test_migrations_do_not_depend_on_supabase_auth_schema`):

| Dependency | Found? | Disposition |
|---|---|---|
| `auth.uid()`, `auth.jwt()`, `auth.role()`, `auth.users` | **none** | nothing to migrate |
| `service_role` / `anon` / `authenticated` roles, `supabase_admin` | **none** | nothing to migrate |
| Row-level security | `db/29_rls_backstop.sql`: FORCE RLS on `evidence`, `executions`, `change_sets`, `change_set_operations`, `failure_routes`; policy `tenant_isolation` keyed on `current_setting('app.tenant_id')`, set by `access.tenant_transaction` | plain Postgres — portable to Neon as-is. **Caveat**: FORCE RLS does not bind superusers/BYPASSRLS roles; the application role on Neon must be a normal role (see runbook) or the backstop is decorative |
| All other tables | no RLS | isolation is the backend predicate builder (`visibility_predicate`, `tenant_predicate`); unchanged |
| Supabase Storage | `models/ontology.py` mentions a Storage path for large payloads; object storage is now `services/object_storage.py` (file/S3-compatible) | no Supabase dependency at runtime |
| `pgvector`, `pg_trgm` etc. | extensions | enable on Neon before running `scripts/migrate.py` |

## Procedure

1. Create Neon control DB + shard DBs; enable the extensions; run `python scripts/migrate.py` per DB (includes migration 99).
2. Create least-privilege roles (runbook), point `DATABASE_URL`/`CONTROL_DATABASE_URL`/shard `dsn_env` at them with `sslmode=require`.
3. Copy data; re-run the **DB-backed** isolation suites (`test_cross_user_isolation_e2e.py`, `test_hardening_h2_rls_backstop.py`, `test_wave3_tenancy_adoption.py`, `test_mcp_server_identity_e2e.py`) against the Neon target with `TEST_DATABASE_URL`. **Do not cut traffic until they pass** — they were *not* run in this hardening pass (no database available; they skip without one).
4. Keep `users` / `org_memberships` / `platform_role_grants` in the control DB; `users.external_subject` is the Supabase `auth.users` uid, so identity links survive the move.
5. Cut over. Supabase keeps Auth only; its Postgres is no longer read by Stealth.

## Not equivalent, by design

Supabase RLS on Supabase-managed tables (if any exist outside `backend/db`) is not reproduced; none were found in the repo, but a live-project check (`select * from pg_policies`) is still an operator step.
