# Launch Compliance Implementation Ledger

**Owner:** security-hardening resume session
**Created:** 2026-09-08
**Specs:** `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` (LC-001..LC-012),
`STEALTHLAB-DATA-FLOW-AND-PROVENANCE-SPEC-V1.md` (INV-01..INV-10)
**Baseline commit:** `289947f` on branch `gate-2b`
**Prior security commit:** `5d03159` "security: Phase 1 P0 launch-compliance security boundaries"
(merged to `main` via `33d4c05`)

This is the durable reconstruction artifact required by the mission's Phase 0.
It is the authority on what actually landed vs. what is still owed. Update it at
the end of every phase (mission rule 15).

---

## Posture determination

**CURRENT STATE: LOCAL / LOOPBACK SAFE. NOT HOSTED PRODUCTION SAFE.**

Evidence:
- `render.yaml`: `REAL_AUTH_ENABLED=false`, `PRIVATE_VISIBILITY_ENABLED=false`,
  no `HOSTED_EXECUTION_ENABLED`, no `SUPABASE_*`, no `OIDC_*`.
- `backend/app/api/deps.py::get_scope` trusts the `X-Viewer-Id` header when no
  validated token is present. Documented as "deliberate temporary shortcut".
- No route-level authentication dependency exists. `admin.py` endpoints are
  rate-limited only.
- Migration `41_phase1_security_boundaries.sql` is **pending** (not applied to
  the live DB). `audit_events`, `registered_workspaces`, and the `'org'`
  `visibility_level` enum value do **not** exist in the database.
- The org-visibility path in `access.py` is never reached from HTTP: `get_scope`
  returns `AccessScope.for_user(subject)`, never `for_org_member(...)`.
- `backend/app/services/audit.py::record_audit_event` has **zero callers**.
- No Supabase client code exists anywhere (frontend or backend). The backend has
  an OIDC *preset* that derives a config from `SUPABASE_PROJECT_URL` +
  `SUPABASE_JWT_AUDIENCE`; the frontend has a generic OIDC PKCE client.

---

## Phase status (mission numbering 0–8)

| Phase | Title | Status |
|---|---|---|
| 0 | Reconstruction / baseline / policy audit | PARTIAL — this file completes it |
| 1 | Authentication + canonical identity (Supabase) | PARTIAL |
| 2 | Canonical authorization / scope enforcement | PARTIAL (strong pre-existing base) |
| 3 | Hosted repository / execution authorization | PARTIAL |
| 4 | Global Commons publication policy | PARTIAL (pre-existing publish ≠ spec gate) |
| 5 | Data classification + provider policy | NOT STARTED |
| 6 | Provenance / dependencies / deletion / export | NOT STARTED (spec requirements) |
| 7 | Auditability + frontend policy controls | NOT STARTED |
| 8 | Security acceptance + release evidence | NOT STARTED |

---

## Requirement ledger

Columns: requirement · existing implementation · status · production files · migration · tests · commit · remaining gap

### Phase 1 — Authentication

| Requirement | Existing implementation | Status | Files | Migration | Tests | Commit | Gap |
|---|---|---|---|---|---|---|---|
| Central JWT validation | OIDC ASGI middleware: JWKS off-loop, alg whitelist (no HS256/none), aud/iss/exp/nbf/sub, expired→401, malformed→401 | DONE | `backend/app/services/authn.py` | n/a | `test_authn_offline.py` (605L) | pre-existing (Band 2.9) | — |
| Supabase as canonical IdP | `OidcConfig.from_settings` preset: issuer `{url}/auth/v1`, JWKS derived, ES256/RS256, half-config fails loud | PARTIAL | `authn.py`, `config.py` (`supabase_project_url`, `supabase_jwt_audience`) | n/a | `test_phase1_security_boundaries_offline.py` (preset unit tests) | `5d03159` | No Supabase-JWT end-to-end verification test; `main.py` boot check ignores the preset |
| ONE reusable `require_authenticated_user` dependency | none | MISSING | — | — | — | — | Must add a central FastAPI dependency returning a typed principal; reject missing/malformed/expired; never trust body identity |
| Typed authenticated principal exposing user_id/email/claims | `authn.Actor` (subject/email/name/claims) on a contextvar | PARTIAL | `authn.py` | n/a | `test_authn_offline.py` | pre-existing | Not surfaced as an injectable request principal; `get_scope` collapses it to `viewer_id` |
| Server-derived identity beats client body fields | write/decision endpoints do `current_actor_id() or body.field` | DONE (write paths) | `api/{ingest,approval,decompose,agent_store}.py` | n/a | `test_*_actor_precedence_offline.py`, `test_mcp_server_identity_e2e.py` | pre-existing | REST *read* path: no explicit "body/query user_id ignored" regression test |
| Org membership resolution on the request | `tenant_scope_for_actor` exists; only MCP repo-exec calls it | PARTIAL | `authn.py` | 28 (applied) | `test_hardening_h1_identity_tenancy.py` | pre-existing | `get_scope` never calls it → `AccessScope.for_org_member` / `'org'` visibility is dead code from HTTP |
| Frontend Supabase browser client | none — generic OIDC PKCE client in `src/lib/auth.ts` | MISSING | `frontendv1/src/lib/auth.ts`, `src/lib/api/client.ts`, `src/app/auth/page.tsx` | n/a | `frontendv1/e2e/v1-flow.spec.ts` (seeds dev viewer id) | pre-existing | No `@supabase/supabase-js` / `@supabase/ssr`; no email/password signup/signin; no `onAuthStateChange`; no refresh-token persistence; sessionStorage access token only |
| Frontend attaches token centrally | `client.ts request()` merges `authHeaders()` (Bearer or X-Viewer-Id) | DONE | `frontendv1/src/lib/api/client.ts` | n/a | — | pre-existing | Header source must become the Supabase session |
| Service-role key never in browser bundle | no service-role key referenced anywhere in either frontend | DONE (by absence) | — | — | — | — | Add a build-time assertion test once Supabase is wired |
| Boot posture guards | `assert_boot_posture` (multi-user/real-auth/hosted without OIDC refuse), `require_trustworthy_identity` | PARTIAL | `authn.py`, `api/deps.py`, `main.py` | n/a | `test_authn_offline.py`, `test_access.py` | pre-existing + `5d03159` | `main.py` computes `oidc_configured_` from generic `oidc_issuer/oidc_audience` only (ignores Supabase preset) and does not pass `hosted_execution_enabled` |
| RLS additive, data-preserving | migrations 28/29 (`schema_migrations` ledger, `FORCE RLS`, commons default tenant) | DONE | `backend/db/28_identity.sql`, `29_rls_backstop.sql` | 28, 29 (applied) | `test_hardening_h2_rls_backstop.py` (431L) | pre-existing | migration 41's `'org'` value + tables not yet applied |
| MCP: public/global vs private separation | `_caller_access_scope()` → `AccessScope.anonymous()` fallback; identity from SDK token / Actor; graph mutation gated to `submit_approval` + `decide_decomposition` | PARTIAL | `backend/app/mcp_server/server.py` | n/a | `test_mcp_server_identity_{offline,e2e}.py`, `test_apply_change_set_removed_security.py` | pre-existing | No end-user auth transport for MCP; shared-token fallback documented; must confirm private rows cannot leak once HTTP auth turns on |

### Phase 2 — Authorization / scope

| Requirement | Existing | Status | Files | Migration | Tests | Commit | Gap |
|---|---|---|---|---|---|---|---|
| ONE centralized enforcement point | `visibility_predicate` + `tenant_predicate` + `scope_predicates` (tenant_scope has no default → TypeError if omitted) | DONE | `backend/app/services/access.py` | 03, 28, 29, 41(pending) | `test_access.py`, `test_hardening_h1/h2`, `test_wave3_tenancy_adoption.py` | pre-existing + `5d03159` | — |
| Canonical scopes PRIVATE/ORGANIZATION/GLOBAL CANDIDATE/GLOBAL VERIFIED | DB enum `public`/`private`/`org`; candidate vs verified is procedure `status` | PARTIAL | `access.py`, `services/procedures.py` | 41 (pending) | — | `5d03159` | 4-scope vocabulary not modeled uniformly; legacy PROJECT/REPOSITORY/PERSONAL mapping not done |
| Access before relevance ranking | documented retrieval contract; scope predicates applied in query | DONE | `services/retrieval.py`, `domain_search.py` | n/a | `test_wave3_tenancy_adoption.py` (retriever legs carry both axes) | pre-existing | — |
| Adversarial matrix (A→A allow, A→B deny, orgA→orgB deny, anon→public allow, anon→private deny) | 3 of 5 covered | PARTIAL | — | — | `test_cross_user_isolation_e2e.py`, `test_product_model_privacy_e2e.py` | pre-existing | orgA→orgB deny unproven end-to-end (org path unwired); explicit anon→private deny at REST read layer |
| IDOR across ids (execution, evidence, implementation, org) | agent-store, generated-file, product-model covered | PARTIAL | — | — | `test_agent_store_idor_e2e.py`, `test_agents_file_download_isolation_e2e.py` | pre-existing | No enumerated IDOR tests for execution-id / evidence-id / implementation-id / org-id |

### Phase 3 — Hosted repository / execution authorization

| Requirement | Existing | Status | Files | Migration | Tests | Commit | Gap |
|---|---|---|---|---|---|---|---|
| principal→org→repository_id→workspace→sandbox | `resolve_workspace_for_actor` (tenant ownership; foreign tenant → NotFound), `enforce_hosted_repo_path` (server path wins) | PARTIAL | `backend/app/services/workspace_registry.py` | 41 (pending) | `test_phase1_security_boundaries_offline.py` | `5d03159` | table not in DB; no registration service; only MCP path guarded — `agents.py`/`runs.py` REST execution unguarded |
| repo membership / existence / deleted / disconnected checks | tenant-ownership check only | PARTIAL | `workspace_registry.py` | 41 (pending) | — | `5d03159` | no deleted/disconnected state or check |
| traversal / symlink escape prevention | RepoSandbox guards (defense in depth) | DONE (pre-existing) | `backend/app/execution/` sandbox | n/a | `test_agent_sandbox_dispatch_traversal_e2e.py`, `test_sandbox_input_path_escape.py` | pre-existing | — |
| disposable workspace, no docker socket, resource/network limits | partial in existing sandbox | NOT MEASURED | `backend/app/execution/` | n/a | `test_container_sandbox_offline.py`, `test_sandbox_executor.py` | pre-existing | must audit CPU/mem/process/timeout/network + docker-socket posture against LC-001 |
| unauthorized execution creates NO execution record | — | MISSING | — | — | — | — | needs E2E |
| execution records principal/org/repository | — | MISSING | — | — | — | — | needs E2E + schema check |

### Phase 4 — Publication gate

| Requirement | Existing | Status | Files | Migration | Tests | Commit | Gap |
|---|---|---|---|---|---|---|---|
| ONE canonical publication operation, 12 steps | ad-hoc `publish` flows (redact absolute paths, explicit publisher, fresh candidate, no auto-publish on verify) | PARTIAL | `services/*publish*`, see `test_publish_e2e.py` | 35 | `test_publish_e2e.py`, `test_local_claims_publish_e2e.py`, `test_second_user_global_reuse_e2e.py` | pre-existing | no single `PublicationService`; missing dependency traversal + classification + privacy/IP/license + sanitization record + authorization record + audit event |
| contribution / `publication_records` table (spec §24) | none | MISSING | — | — | — | — | additive table |
| private verification counts NOT copied to global | verification-promotion never auto-publishes | PARTIAL | — | — | `test_publish_e2e.py` | pre-existing | explicit test that global candidate starts with zeroed independent evidence |
| withdrawal state machine (spec §25) | none | MISSING | — | — | — | — | — |

### Phase 5 — Classification + provider policy

| Requirement | Existing | Status | Gap |
|---|---|---|---|
| canonical classification vocabulary (LC-004) | none | MISSING | 10-value vocab; drives storage/retrieval/publication/routing/export/deletion/audit |
| `model_provider_policies` registry | none | MISSING | provider/model/endpoint/region/residency/retention/training-use/subprocessors/DPA/transfer/deletion/allowed-classes/effective dates/policy version |
| `can_send(classification, provider, model, tenant_policy) → Decision` | none — calls gated by API-key presence + `CostGovernor` only | MISSING | single decision point; embeddings + model calls both obey; allow/deny both audited |

### Phase 6 — Provenance / deletion / export

| Requirement | Existing | Status | Gap |
|---|---|---|---|
| provenance substrate (reuse) | `claim_sources`, `procedure_dependencies`, `ingestion_provenance` (mig 32), `knowledge_nodes`/`edges` | PARTIAL | complete the canonical provenance field set (spec §4) on derived objects |
| dependency-aware `DeletionService` | none | MISSING | traces/observations/claims/procedures/implementations/embeddings/indexes/caches/exports/provider-deletion/backups/legal-hold |
| tombstone vs physical vs legal-hold model | none | MISSING | spec §21 |
| `ExportService` + `data_requests` | none | MISSING | account/profile/private procedures/traces/repo connections/executions/publication history/preferences |

### Phase 7 — Audit + frontend controls

| Requirement | Existing | Status | Gap |
|---|---|---|---|
| ONE audit writer | `services/audit.py::record_audit_event` (fail-closed, append-only) | PARTIAL | table unapplied (mig 41); **zero callers** — must wire into repo connect/disconnect, private create/delete, publication req/approve/reject, candidate created, verified, scope change, export/deletion req/complete, provider allow/deny, incident open/close |
| frontend scope labels PRIVATE/ORG/GLOBAL CANDIDATE/GLOBAL VERIFIED | none | MISSING | LC-010 |
| publication consequences UI | none | MISSING | LC-009 |
| Privacy/Data settings page | none | MISSING | LC-008 |
| "similarity ≠ authorization" in UI | retrieval-representation lane is removing raw "Match 82%" | IN PROGRESS (other lane) | coordinate |

### Phase 8 — Acceptance

Not started. No acceptance matrix. `demo.md` is the existing release-gate doc to extend.

---

## Pre-existing test baseline (do not regress)

Offline suite, `DATABASE_URL` unset, on `289947f`:
**2208 passed / 27 failed / 301 skipped.**
The 27 failures are all the ingestion lane's `Embedder`/`FakeEmbedder` missing
`embedding_model_id` / `embedding_provider` (files: `test_domain_search_offline`,
`test_local_agent_runner_offline`, `test_gate3_experiment_offline`,
`test_mcp_six_tool_surface_offline`, `test_behavioral_validation_offline`,
`test_migration_upgrade_e2e`). Not security-related. Confirmed identical set
pre/post the `33d4c05` merge.

---

## Coordination

- `frontend integration stealth-lab` session — declared owner of "auth only"
  per `thingstodo.md`. Any edit to `frontendv1/src/lib/auth.ts`,
  `frontendv1/src/app/auth/**`, or auth wiring in `client.ts` must be cleared
  with them.
- `retrieval representation frontend` (core-b) — owns `frontendv1/src/lib/api/{client,types}.ts`
  and several backend services + migration 44. Overlaps on `client.ts`.
- `evidence-tracking-temporal-reasoning` — no overlap declared.

---

## Phase checkpoints

### Phase 0 — COMPLETE
- Reconstruction audit written (this file). Committed `3e47906`.

### Phase 1 (authentication) — CODE COMPLETE; awaiting live config + migration
Commits: `3e47906` (backend), `e63ba85` (frontend). Both on `gate-2b`,
stacked on core-b's `902096f`. Not pushed (branch push cadence is core-b's).

Landed:
- `deps.py` — `require_authenticated_user` → `AuthenticatedPrincipal`
  (server-derived: verified token subject + provisioned `users` row +
  resolved org memberships). Strict: validated bearer only. 401 missing /
  403 deactivated / 409 ambiguous-org. `optional_authenticated_user`.
  `principal.access_scope()` resolves org visibility before ranking.
- `main.py` — boot posture uses `oidc_configured()` (Supabase preset
  counts) and passes `hosted_execution_enabled`.
- `procedures.py` — `POST /v1/procedures` + `POST /v1/procedures/from_text`,
  authenticated, private + candidate, `provenance=system_pending_review`,
  `scope_type=user`. Deterministic retrieval document; best-effort inline
  embed.
- `skill_ingestion.ingest_skill_md` — owner/visibility/scope/embed
  passthrough; owner submissions are always `system_pending_review`.
- Frontend `frontendv1`: `src/lib/supabase/client.ts` (anon-key browser
  client, PKCE, persistSession + autoRefresh), `src/lib/auth.ts` reworked
  onto Supabase (sync `authHeaders()` preserved — `client.ts` untouched),
  `/auth` email-password + Google + dev-viewer fallback, `/auth/callback`,
  `/submit` "Quick add (private)" fast form, `src/lib/api/contribute.ts`.
- `.env.example` (backend) + `.env.local.example` (frontend) document the
  Supabase vars and required dashboard config.

Tests (offline): `test_supabase_auth_dependency_offline.py` (7),
`test_procedure_fast_create_offline.py` (5) — green. Broader targeted
regression: 492 passed / 4 failed — the 4 are the pre-existing
ingestion-lane `embedding_model_id` baseline, unchanged by this work.
Frontend `tsc --noEmit` clean (repo eslint is pre-broken, unrelated).

STILL REQUIRED to make sign-in live (config only — no more code):
1. Supabase dashboard (project `wckeklqxmiglivfolujn`):
   - Project Settings → JWT Keys → migrate to **asymmetric (ES256)** keys.
   - Authentication → Providers → enable **Google** (client id/secret).
   - Authentication → URL Configuration → add `<origin>/auth/callback`
     redirect URLs (localhost + deployed).
2. `backend/.env`: `SUPABASE_PROJECT_URL=https://wckeklqxmiglivfolujn.supabase.co`,
   `SUPABASE_JWT_AUDIENCE=authenticated`. (Leaving `REAL_AUTH_ENABLED` /
   `PRIVATE_VISIBILITY_ENABLED` as-is is fine — the new endpoints require a
   token regardless; flip those only when retrofitting the read surface.)
3. `frontendv1/.env.local`: `NEXT_PUBLIC_SUPABASE_URL`,
   `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
4. `cd frontendv1 && npm install` (lockfile already updated).

BLOCKING (one-way door — needs explicit go-ahead):
- Apply `backend/db/41_phase1_security_boundaries.sql` to the live DB
  (`python scripts/migrate.py`). `ALTER TYPE ... ADD VALUE 'org'` is
  irreversible. Not needed for basic sign-in + private procedures; IS
  needed before enabling org-scoped visibility, hosted workspaces, or the
  audit writer. The classifier declined the automated apply.
