# Security runbook

## Neon database roles (least privilege) — operator step, not yet applied

| Role | Used by | Grants |
|---|---|---|
| `stealth_app` | REST API, MCP | `CONNECT`; `SELECT/INSERT/UPDATE/DELETE` on app tables; **no** `CREATE`, not superuser, **not** `BYPASSRLS` (else `db/29` FORCE RLS is bypassed) |
| `stealth_worker` | ingestion workers | `SELECT/UPDATE` on `ingestion_jobs`, write on canonical + projection tables; **no** access to `users`, `org_memberships`, `platform_role_grants`, `service_*`, `audit_events` (read) |
| `stealth_migrate` | `scripts/migrate.py`, CI | owner/DDL; never used by a running service |
| `stealth_readonly` | analytics | `SELECT` on non-identity tables |
| `stealth_maint` | reindex/repair | `stealth_worker` + `UPDATE` on projection/outbox tables |

`ALTER DEFAULT PRIVILEGES` for new tables; rotate passwords quarterly and on any suspicion; separate credentials per environment; Neon connection strings only in server-side secret stores (a test asserts the frontends never reference DB/service-role variables).

## Setup checklist

1. Supabase: enable asymmetric JWT signing keys (ES256). Set `SUPABASE_PROJECT_URL`, `SUPABASE_JWT_AUDIENCE=authenticated`. Do **not** enable the legacy HS256 secret path.
2. `STEALTHLAB_ENV=production`, `AUTH_ENVIRONMENT=production`, `FRONTEND_ORIGIN=https://…` (exact origins), `DATABASE_URL=…?sslmode=require`.
3. `SERVICE_TOKEN_ISSUER/AUDIENCE/KEYS` (≥ 32-byte random secrets), register services, mint credentials into each worker's secret store.
4. Grant platform roles: `INSERT INTO platform_role_grants (user_id, role, granted_by) VALUES (…, 'platform_admin', 'you')`; once operators use scoped identities set `ADMIN_API_KEY_LEGACY_ENABLED=false` and unset `ADMIN_API_KEY`.
5. Boot the API: `assert_production_safe` prints every refusal at once.

## Cache TTLs / revocation latency

| State | Staleness |
|---|---|
| Human `users.is_active`, memberships, platform roles | 0 (read per request) |
| Supabase access token after logout / disable | until `exp` (default 1 h) — but a **deactivated `users` row is 403 immediately**; disable there, not only in Supabase |
| JWKS / signing-key rotation | ≤ 5 min; unknown `kid` forces one refresh |
| Service registry (revoke/disable) | ≤ `AUTH_CACHE_TTL` (30 s) |
| Worker credential | verified at start-up; expires with its `exp` |

## Incident response

**Leaked worker credential** — `service_identity revoke <jti> --reason …` (or `disable <service_id>` if unsure); wait `AUTH_CACHE_TTL`; mint a new one; review `audit_events` for `actor_subject='service:<id>'`. If the **signing key** leaked: add a new kid, mint, remove the old kid, revoke all outstanding `jti`s (an attacker also needs a *registered* `jti`, so a leaked key alone cannot mint an accepted token).
**Leaked legacy admin key** — set `ADMIN_API_KEY_LEGACY_ENABLED=false`, rotate, review `admin.legacy_key_used` events.
**Compromised user** — `UPDATE users SET is_active=false WHERE …` (effective next request), then disable in Supabase; remove memberships/grants.
**Suspected cross-tenant leak** — capture `audit_events` `access.denied`, run the DB-backed isolation suites, check `pg_policies` and that `stealth_app` lacks `BYPASSRLS`.
**Leaked Neon URL** — rotate the role password; connections drop; redeploy secrets.

## Audit events

Fail-closed (`record_audit_event`): publication, withdrawal, export, deletion, workspace/authorization changes. Best-effort (`record_security_event`): `access.denied` (authenticated callers), `admin.legacy_key_used`. Never contain tokens or keys. Service register/issue/revoke/disable and break-glass grant/revoke are fail-closed audit rows. Not yet audited: shard registration/admin CLI actions.

## Residual risks (what remains, all operator/live-run)

State as of 2026-10-07. Evidence: [final_prod_docs/p1_results.md](../final_prod_docs/p1_results.md).

1. **The isolation suites have run on a throwaway database, but not yet on a Neon branch.**
   - On 2026-10-07 the isolation e2e suites listed in securityp1.md §5.2, plus the new
     `tests/test_tenant_isolation_p1a_e2e.py`, ran on a throwaway loopback PostgreSQL 18, both as the owner and as
     `stealth_app`. Counts are in p1_results.md.
   - A Neon *branch* run is still to do before cutover. Never run them on the production branch.
2. Migration 99 now runs as part of the full migration set on the throwaway database (141 files applied, 0 pending).
3. **The restricted role is ready but not yet in use in production.**
   - `scripts/provision_app_role.py` creates `stealth_app` on the control database, S###, and K### (dry run by
     default, `--apply` to write; it prints host names only). It is verified on the throwaway database.
   - **Not run on Neon yet**, and the services still connect as the table owner. **That role has `BYPASSRLS`**
     (checked read-only on 2026-10-08: not superuser, `rolbypassrls = true`, owns the tables). So **in production
     today every RLS policy is bypassed**: migration 29's, migration 136's and migration 150's alike. Isolation rests
     entirely on the application checks, which the suites verify. Switching the services to `stealth_app` is what
     makes the database layer enforce. **This is the highest-value remaining step.**
   - Operator steps: generate a password, run the script with `--apply`, then switch each service's `DATABASE_URL`
     (and S###/K###) user to `stealth_app`. Keep the owner credentials for `migrate.py` only.
   - `stealth_worker` / `stealth_migrate` / `stealth_readonly` / `stealth_maint` are still documented only.
4. Supabase access tokens cannot be revoked before `exp`; mitigated by the per-request `users.is_active` check.
5. Shard-admin CLI actions (`app.ingestion.admin`) do not write `audit_events`.
6. `docker-compose.yml` ships a dev-only DB password; never reuse it.
7. Legacy admin key: on in TEST only, unless `ADMIN_API_KEY_LEGACY_ENABLED` says otherwise (since 2026-10-08;
   before that it was on everywhere outside PRODUCTION, including a host started as STAGING).
8. **Closed 2026-10-08 (migration 150): the five migration-29 tables** (`change_set_operations`, `change_sets`,
   `evidence`, `executions`, `failure_routes`).
   - With `app.tenant_id` unset they now allow only the shared commons tenant's rows, not every row.
   - The audit found no code path that writes another tenant to them, so nothing changed for existing paths.
   - `TenantScope.unrestricted()` binds the explicit system scope (`app.rls_system`).
9. **Closed 2026-10-08 (migration 150): the routing logs.** `routing_decisions` and `routing_observations` have
   FORCE RLS.
   - `public` rows are visible to everyone. Other rows are visible only to their owner's readers (the caller's
     subject and organisations, bound per request through `store.routing_scope`), or under the system scope
     (workers, the nightly fit, admin commands).
   - A routing row of an `org`-visible Goal records the organisation as its owner, so every member can reach it.
   - The sync tables (`synced_projects`, `sync_device_credentials`) had the same permissive-when-unset helper, and
     no code ever set their owner. They now deny without an owner bound, and every sync path binds its owner.
   - **Deploy order:** while the services connect as the `BYPASSRLS` owner, 150 changes nothing they see.
     - Done 2026-10-08: 150 applied to the control database and S001–S004.
     - Before switching to `stealth_app`: the new code (which binds every scope) must be live, **and** the knowledge
       shards K001–K084 need the pending migrations, 150 included. They carry the migration-29 tables too, and still
       have the old permissive helper. Run `migrate.py --dsn` per shard, as `scripts/provision_neon_shards.py` does.
10. Closed 2026-10-08: see 7. An explicit `ADMIN_API_KEY_LEGACY_ENABLED=false` on the host is still the clearest
    setting.

## Break-glass

`grant_break_glass(pool, grantor=<auth:admin human>, grantee_user_id=…, reason="≥20 chars", ttl_seconds≤3600)` — grantor ≠ grantee; grants `tenancy:cross` (never SYSTEM_INTERNAL) until it expires or `revoke_break_glass`; the grant and every request made under it are audited (`break_glass.granted|used|revoked`).
