# Auth architecture

Three separate concerns, three separate systems:

| Concern | System | Where in code |
|---|---|---|
| **Identity** (who is this human?) | Supabase Auth (email/OAuth/reset/sessions/JWT issuance stay there — Stealth rebuilds none of it) | `services/authn.py` verifies the token only |
| **Authorization** (what may they do?) | Stealth backend | `services/auth_context.py`, `services/authorization.py`, `api/deps.py`, MCP `_TOOL_SCOPES` |
| **Data** | Postgres (Supabase-hosted today, Neon shards target) | `db/*.sql`, `services/shards.py` |

Neon is **not** an auth provider and no migration depends on `auth.uid()` (a test enforces it).

## Human request

```
browser / MCP client
  └─ Authorization: Bearer <Supabase access token>
      ▼
ASGI actor_middleware (authn.py)
  · alg pinned to ES256/RS256 (never HS256/none), JWKS via FetchingJwks (5 min TTL, forced refresh on unknown kid → rotation-safe)
  · iss, aud, exp, nbf, sub required; bad token ⇒ 401 (never anonymous); token never logged/echoed
  ▼
deps.get_auth_context()  ── once per request (cached on request.state) ──►  AuthContext
  · users row (ensure_user; inactive ⇒ 403)      · org_memberships ⇒ org_ids/tenant_id/org_roles
  · platform_role_grants ⇒ platform roles        · scopes = user baseline ∪ role scopes
  ▼
authorization  (require_scopes(...) dependency; can_read/write/publish/execute/admin(ctx, ObjectRef))
  ▼
repository / SQL:  AccessScope = ctx.access_scope()  ⇒  visibility_predicate() in the candidate query
  ▼
Shard router (services/shards.py)  ── routing only; ObjectRef has no shard field ──►  Neon / Postgres
```

Identity state is **not cached across requests**: `users.is_active`, memberships and platform roles are read on every request, so deactivation, removal from a tenant and role changes apply on the next request. The only cached auth state is the JWKS (5 min) and the service registry (`AUTH_CACHE_TTL`, default 30 s).

## Service (worker) request

```
worker / maintenance job
  └─ X-Stealth-Service-Token: <short-lived signed credential>      (never in Authorization: that header is for Supabase JWTs)
      ▼
actor_middleware → service_identity.verify_service_token
  · alg pinned, kid ∈ SERVICE_TOKEN_KEYS, iss/aud/exp/iat/jti, TTL ≤ SERVICE_TOKEN_MAX_TTL_SECONDS
  · env claim == AUTH_ENVIRONMENT   · service registered, not disabled, registered for this env
  · jti registered & not revoked (PgServiceRegistry)   · requested scopes ⊆ registered allowed_scopes (escalation ⇒ reject)
  ▼
ServiceAuthContext(service_id, roles, scopes, environment, credential_id)      request presenting BOTH credentials ⇒ 400
  ▼
require_scopes(...)   ·   reads = PUBLIC rows only   ·   private data only via bind_job(JobAuthority) + authorization.py
```

The ingestion worker (`python -m app.ingestion.worker`) connects to the control DB directly, so its "request" is the database session. It therefore **authenticates at start-up** (`authenticate_worker`): outside TEST it refuses to run without a valid `INGEST_SERVICE_TOKEN` holding `ingestion:process`, and stamps `service_id` into its `claimed_by`.

## MCP

Same building blocks, different transport. `OidcAwareTokenVerifier` tries, in order: Supabase/OIDC JWT → service credential → the legacy shared `STEALTHLAB_MCP_TOKEN` (disabled when `DEPLOYMENT_MODE=shared`). Human tokens are resolved through the **same** `resolve_auth_context` as REST, so scopes and org memberships are identical (org ids ride as server-stamped `org:<uuid>` token scopes). `_TOOL_SCOPES` maps every tool to one scope (a test fails if a new tool is unclassified); unclassified tools default to `knowledge:write`. Graph/goal-run custom routes use the verified caller's scope or public-only — never `AccessScope.unrestricted()`.

## Posture rules (enforced at boot by `runtime_guard.assert_production_safe`)

Outside TEST (unset/unknown environment resolves to PRODUCTION): identity provider mandatory (Supabase preset or OIDC, https issuer); no fake `auth_provider`; `AUTH_ENVIRONMENT` must equal the runtime environment; service-token config all-or-nothing, HS keys ≥ 32 bytes, issuer/audience distinct from the human IdP; `ADMIN_API_KEY` ≥ 32 chars and not a known placeholder while the legacy key is enabled; remote DB URLs need `sslmode=require|verify-*`. `X-Viewer-Id` is honoured only in TEST with no IdP configured. There is no `AUTH_DISABLED`-style switch (a test greps for one).

## Public endpoints (complete list)

`/health`, `/docs`, `/redoc`, `/openapi.json`; and read routes that resolve to `AccessScope.anonymous()` return **public rows only** (`/v1/search`, `/v1/claims/*`, `/v1/procedures/*` reads, `/v1/goals` reads, `/v1/problems` reads, `/v1/contributors/*`, …). Every mutating route requires a scope; see `authorization_model.md`.

## Environment variables

| Variable | Purpose |
|---|---|
| `SUPABASE_PROJECT_URL`, `SUPABASE_JWT_AUDIENCE` | Supabase preset. Issuer = `<url>/auth/v1`, JWKS = `<issuer>/.well-known/jwks.json` (so `SUPABASE_JWT_ISSUER`/`SUPABASE_JWKS_URL` are derived, not separate). Legacy shared-secret (HS256) mode is intentionally unsupported. |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` | generic OIDC alternative |
| `SUPABASE_ANON_KEY` | frontend only (`NEXT_PUBLIC_SUPABASE_*`); backend does not use it |
| `SUPABASE_SERVICE_ROLE_KEY` | **not used by the backend and must not be added**; a test fails if code references it |
| `DATABASE_URL` / `CONTROL_DATABASE_URL`, shard DSN env names | server-side only; `sslmode=require` for remote hosts |
| `AUTH_ENVIRONMENT` | `test`/`staging`/`production`; must match `STEALTHLAB_ENV`; bound into service tokens |
| `AUTH_CACHE_TTL` | max staleness of the service registry cache (default 30 s) |
| `SERVICE_TOKEN_ISSUER`, `SERVICE_TOKEN_AUDIENCE`, `SERVICE_TOKEN_KEYS` (`kid:secret,…`), `SERVICE_TOKEN_ALG`, `SERVICE_TOKEN_MAX_TTL_SECONDS` | service credentials |
| `INGEST_SERVICE_TOKEN` | the worker's own credential |
| `ADMIN_API_KEY`, `ADMIN_API_KEY_LEGACY_ENABLED` | legacy admin key (deprecated; set the flag `false` once operators hold `platform_admin`) |
| `STEALTHLAB_MCP_TOKEN`, `DEPLOYMENT_MODE` | MCP shared token (single-user only) / `shared` disables it |
