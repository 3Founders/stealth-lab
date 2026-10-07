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
3. **The restricted role exists only in a script.** `scripts/sql/create_app_role.sql` creates `stealth_app`
   (no superuser, no `BYPASSRLS`, no DDL), and it is verified on the throwaway database.
   - **Not created on Neon.** Until it is, and the services' `DATABASE_URL` uses it, the services connect as the
     table owner, and FORCE RLS does nothing for them.
   - `stealth_worker` / `stealth_migrate` / `stealth_readonly` / `stealth_maint` are still documented only.
4. Supabase access tokens cannot be revoked before `exp`; mitigated by the per-request `users.is_active` check.
5. Shard-admin CLI actions (`app.ingestion.admin`) do not write `audit_events`.
6. `docker-compose.yml` ships a dev-only DB password; never reuse it.
7. Legacy admin key: in PRODUCTION it is off unless `ADMIN_API_KEY_LEGACY_ENABLED=true`.
8. **Five tables use migration 29's lenient rule** (`change_set_operations`, `change_sets`, `evidence`,
   `executions`, `failure_routes`). `sl_tenant_scope_allows()` *allows* a row when `app.tenant_id` is unset, so a
   path that forgets `tenant_transaction()` is not blocked there. The migration-136 tables are strict.
   - **Deferred:** switching them to strict first needs an audit showing every path that touches them runs inside
     `tenant_transaction()`. Otherwise those writes would start failing.
   - Pinned by `test_without_a_tenant_setting_strict_tables_see_nothing_and_fallback_tables_are_the_known_gap`.
9. **The routing tables have no row-level security.** That includes `routing_decisions` and
   `routing_observations`, which carry `visibility` / `owner_id`. Application checks are the only layer.
   - **Deferred:** move the routing log writes under `tenant_transaction()`, then add strict policies. A strict
     policy added first would break every routing write, and the lenient one would add nothing.
   - The global routing tables (params, model cards, prices, the model registry) hold no tenant data.
10. **Check the legacy admin key on the hosted service.** `STEALTHLAB_ENV` decides whether it works when
    `ADMIN_API_KEY_LEGACY_ENABLED` is unset, and it is on outside PRODUCTION. If the hosted service runs with
    `STEALTHLAB_ENV=STAGING`, set `ADMIN_API_KEY_LEGACY_ENABLED=false` there explicitly.

## Break-glass

`grant_break_glass(pool, grantor=<auth:admin human>, grantee_user_id=…, reason="≥20 chars", ttl_seconds≤3600)` — grantor ≠ grantee; grants `tenancy:cross` (never SYSTEM_INTERNAL) until it expires or `revoke_break_glass`; the grant and every request made under it are audited (`break_glass.granted|used|revoked`).
