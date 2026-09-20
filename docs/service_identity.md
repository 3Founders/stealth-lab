# Service identity

Workers authenticate as **services**, never as fake users. `ServiceAuthContext(service_id, roles, scopes, environment, credential_id, expires_at)` has no `user_id` field; the only way a service touches private data is `ctx.bind_job(JobAuthority)` with the authority the API stamped on the job.

## Standard identities

| service_id | allowed scopes (typical) |
|---|---|
| `cloud-run-ingestion`, `github-actions-ingestion`, `oracle-ingestion`, `local-ingestion` | `ingestion:process`, `projection:write` |
| `projection-worker` | `projection:write` |
| `reindex-worker` | `projection:write`, `maintenance:run` |
| `maintenance-worker` | `maintenance:run`, `projection:write` |

No service role can be granted `tenancy:cross`, `knowledge:publish` or `auth:admin` by convention; `register_service` only checks scopes exist, so **review registrations** (see runbook).

## Credential format

JWT, `alg` pinned by `SERVICE_TOKEN_ALG` (HS256 default), header `kid`, claims `iss`, `aud`, `sub`(=service_id), `jti`, `iat`, `nbf`, `exp`, `env`, `scp`(list). Lifetime ≤ `SERVICE_TOKEN_MAX_TTL_SECONDS` (3600). HTTP callers send it in `X-Stealth-Service-Token` (never `Authorization`). Only `sha256(token)` and the `jti` are stored (`service_credentials`); the token is printed once by the CLI and never returned by an API.

## Operations

```bash
python -m app.services.service_identity register cloud-run-ingestion --scope ingestion:process --scope projection:write --role ingestion_worker
python -m app.services.service_identity mint cloud-run-ingestion --scope ingestion:process --ttl 900   # prints the token once
python -m app.services.service_identity revoke <jti> --reason "leaked in CI log"
python -m app.services.service_identity disable cloud-run-ingestion                                     # identity + all live credentials
```

Rotation: add the new key to `SERVICE_TOKEN_KEYS` (`k1:old,k2:new`), mint with `kid=k2`, wait for old tokens to expire, remove `k1`. Retired kids fail with `unknown_key`.

## Verification order and failure reasons

`malformed` · `bad_alg` · `unknown_key` · `bad_signature|bad_issuer|bad_audience|expired|invalid` · `ttl_too_long` · `issued_in_future` · `wrong_environment` · `unknown_or_disabled_service` · `credential_revoked_or_unregistered` · `bad_scopes` · `scope_not_granted`. Over HTTP every failure is a generic `401 invalid service credential`; the reason is logged (never the token).

Revocation latency = `AUTH_CACHE_TTL` (default 30 s) for a running API process; workers verify at start-up with TTL 0.

## Job authority

Migration 99 adds to `ingestion_jobs`: `submitted_by_user_id`, `submitted_by_service_id`, `auth_tenant_id`, `auth_scope`, `auth_visibility`, `source_access_scope`, `publication_allowed`. `queue.enqueue(..., authority=JobAuthority(...))` writes them from the verified caller; `queue.job_authority_from_row(row)` reads them (conservatively derived for pre-99 rows; `publication_allowed` is never inferred). Every `enqueue` writes authority: an explicit `JobAuthority` when given (API code builds it from `AuthContext`), otherwise one derived from the job's declared scope/visibility/owner — so no job is authority-less and none implies publication. A worker holding a service identity hydrates blobs only through `authorized_hydrate`, i.e. after `can_read` on the job's own object.
