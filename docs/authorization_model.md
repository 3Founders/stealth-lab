# Authorization model

Authentication produces a principal (`AuthContext` | `ServiceAuthContext` | `AnonymousContext`). Authorization is then two independent checks, both centralized:

1. **Scope check** — *may this principal call this operation at all?* `require_scopes(...)` (REST dependency) / `_TOOL_SCOPES` (MCP).
2. **Object check** — *may it touch this object?* `services/authorization.py`: `can_read/write/publish/execute/admin(ctx, ObjectRef)` and `authorize(...)`. Read paths enforce the same rule in SQL through `AccessScope` → `visibility_predicate` (filter during candidate generation, never after ranking).

## Scopes

| Scope | Meaning | Granted to |
|---|---|---|
| `knowledge:read`, `retrieval:read`, `execution:read` | read/search/inspect | every active user |
| `knowledge:write` | create/modify **own** (or own-tenant per role) objects | every active user |
| `publication:request` | request publication of an object you own | every active user |
| `execution:run` | run procedures/goals | every active user |
| `ingestion:submit` | submit traces/sources | every active user, submitter services |
| `knowledge:publish` | global admission: approve/promote/decide | `reviewer`, `platform_admin` platform roles |
| `admin:ops`, `shards:admin`, `auth:admin`, `maintenance:run`, `projection:write` | operations | `platform_admin` (humans) / registered services |
| `ingestion:process` | process ingestion jobs | registered ingestion workers |
| `tenancy:cross` | read across tenants | **no role grants it** (explicit break-glass only; none exists yet) |

Platform roles live in `platform_role_grants` (revocable, per user). **Organization roles (`owner/admin/member/viewer`) are not platform roles**: an org admin manages their org's objects only and holds no `admin:ops`.

## Object scopes (from the existing `visibility` column)

| visibility | ObjectScope | read | write | publish (request) | admin |
|---|---|---|---|---|---|
| `public` | GLOBAL_PUBLIC | anyone | `knowledge:publish` holder; service with global-scoped job or `projection:write` (projections) | — | `admin:ops` |
| `private` | USER_PRIVATE | owner; service bound to that owner's job | owner; job-bound service | owner (`publication:request`); service only if job `publication_allowed` | owner / `admin:ops` |
| `org` | TENANT_PRIVATE | members of `tenant_id` (not the shard, not the caller's claim) | member/admin/owner (not viewer) | tenant admin/owner | tenant admin/owner |
| other / unknown | SYSTEM_INTERNAL | `admin:ops`; services with an internal scope | same | — | — |

Rules: admin scopes never imply data access; unknown visibility fails closed; expired contexts are denied everything; the shard is not an input to any decision.

## Denial semantics

`401` no/invalid/expired credential · `403` authenticated but not permitted (audited as `access.denied`, authenticated callers only, so anonymous floods cannot write audit rows) · `404` when `hide_existence=True` so a caller cannot tell "not yours" from "does not exist" · `429` rate limit · `402` budget.

## Ownership

`owner_id`, `tenant_id`, `created_by*` come from `owner_fields_for_create(ctx)` (whose signature has no owner/tenant parameter). Request bodies never contribute; legacy routes that still read `approver_id`/`submitted_by`/`actor` from a body do so only in the unenforced TEST posture — the verified actor always overrides.

## Route → scope (previously unauthenticated mutators)

| Route | Scope |
|---|---|
| `POST /v1/agent-store/submit` | `knowledge:write` |
| `POST /v1/agent-store/promote`, `POST /v1/agent-store/{id}/decide` | `knowledge:publish` |
| `GET /v1/agent-store/pending`, `GET /v1/approvals/pending`, `GET /v1/approvals/{id}`, `GET /v1/decompose/pending`, `GET /v1/decompose/{id}` | `knowledge:read` |
| `POST /v1/approvals/{id}`, `POST /v1/decompose/{id}/decide` | `knowledge:publish` |
| `POST /v1/approvals/{id}/human-turn` | `knowledge:write` |
| `POST /v1/traces` | `ingestion:submit` |
| `POST /v1/problems`, `/benchmarks`, `/benchmarks/{id}/freeze`, `/solutions/associate`, `/evaluations`, `/evaluations/{id}/complete|invalidate` | `knowledge:write` |
| `POST /v1/runs/{id}/resume`, `/nodes/{n}/retry` | `execution:run` |
| `/v1/admin/*`, `/v1/trajectories/*` | `admin:ops` (human) · `maintenance:run` (service) · legacy `X-Admin-Api-Key` |
| goals/procedures/workspaces/publications/me writes | `require_authenticated_user` (human only; services get 403) |

## Search & retrieval

Candidate SQL is built with `visibility_predicate(scope)`: `public`, plus `owner_id = <caller>`, plus `org` rows for the caller's `org_ids`; anonymous and service callers get `visibility = 'public'` only. Rows are filtered before lexical/vector ranking and before any external JEV/NLI/embedding call, so unauthorized text never reaches a model provider and cannot influence counts, snippets or timing. Private text sent to providers is additionally gated by `ProviderPolicyService` (pre-existing, migration 46).
