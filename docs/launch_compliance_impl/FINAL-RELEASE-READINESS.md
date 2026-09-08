# StealthLab — Final Release Readiness (Auth / Policy / Launch Compliance)

**Date:** 2026-09-08
**Branch:** `gate-2b` (not pushed)
**Head at report time:** `7f2fbb8`
**Specs:** `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` (LC-001..012),
`STEALTHLAB-DATA-FLOW-AND-PROVENANCE-SPEC-V1.md` (INV-01..10)
**Companion:** `docs/launch_compliance_implementation_ledger.md` (per-requirement ledger, Phase 0 audit)

This report is the authority on what is proven vs. designed vs. absent. It is
written to be reconstructable from git + tests without chat history.

---

## Verified current state (2026-09-08)

| Item | Result |
|---|---|
| Git | branch `gate-2b`, ahead of `origin/gate-2b` by ~30 commits, **not pushed** (per instruction). Working tree carries core-b's active retrieval-lane edits (`domain_search.py`, `embeddings.py`) — not part of this work. |
| Supabase JWT algorithm | JWKS at `https://wckeklqxmiglivfolujn.supabase.co/auth/v1/.well-known/jwks.json` serves **one key: `EC / ES256 / P-256`**. No HS256 key present. |
| Supabase issuer / audience | issuer `https://wckeklqxmiglivfolujn.supabase.co/auth/v1`; `aud` = `authenticated`. Backend `OidcConfig.from_settings` derives exactly these. `allowed_algs = ("ES256","RS256")`. |
| Backend env (no secrets shown) | `backend/.env` has `SUPABASE_PROJECT_URL` (https://…, populated) and `SUPABASE_JWT_AUDIENCE=authenticated`. `REAL_AUTH_ENABLED=false`, `PRIVATE_VISIBILITY_ENABLED=false`, `HOSTED_EXECUTION_ENABLED` unset (false). |
| Frontend env (no secrets shown) | `frontendv1/.env.local` has `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` (`sb_publishable_…` — new-format publishable key, browser-safe). `.env.local` is gitignored. No `service_role` / `sb_secret_` anywhere in the frontend. |
| Frontend deps | `@supabase/supabase-js@2.116.0` installed (supports `sb_publishable_` keys). `next@16.3.4`, `react@19.2.8`. |
| `oidc_configured(settings)` | **True**. `assert_boot_posture(...)` with the live config → **passes** (backend boots). |
| Frontend `/auth` render | Dev server booted clean on `.env.local`; `/auth` serves the email/password + Google branch (not the dev-viewer fallback); `/auth/callback` → 200; no compile/runtime errors. `tsc --noEmit` clean. |
| Migration ledger | 01–45 applied **except `41_phase1_security_boundaries.sql` → PENDING**. |
| Migration 41 applied? | **NO.** DB `visibility_level` enum = `{public, private}` only. `audit_events` and `registered_workspaces` tables **do not exist**. |
| DB state | `procedures` = 2537 (2536 public, 1 private). `users` = 0 (nobody has signed in). `organizations` = 1 (the seeded commons tenant). `procedures` has **no `tenant_id` column**. |
| Test baseline | Full offline suite (`DATABASE_URL` unset): **2270 passed / 18 failed / 312 skipped** (265s). All 18 failures are the ingestion/embedding lane (`Embedder` missing `embedding_model_id` / `embedding_provider`): `test_local_agent_runner_offline` ×12, `test_gate3_experiment_offline` ×1, `test_mcp_six_tool_surface_offline` ×2, `test_behavioral_validation_offline` ×2, `test_migration_upgrade_e2e` ×1. None touch auth / access / procedures / deps / authn. Baseline was 27 before core-b fixed 9 in parallel; this work adds **0** new failures. |

---

## Phase status

| Phase | Verdict | One line |
|---|---|---|
| 0 Reconstruction / audit | **COMPLETE** | `docs/launch_compliance_implementation_ledger.md`, commit `3e47906`. |
| 1 Authentication + canonical identity | **COMPLETE (code + config live); one manual step outstanding** | Supabase = canonical identity, one central dependency, server-derived subject, tests green. Email-confirm toggle is a Supabase dashboard preference, not code. |
| 2 Centralized authorization / scope | **PARTIAL — PERSONAL + GLOBAL enforced; ORGANIZATION/PROJECT/REPOSITORY not** | `access.py` is the one enforcement point; owner + public boundaries proven; `'org'` needs migration 41 + a `procedures.tenant_id` column that does not exist. |
| 3 Hosted repository / execution security | **PARTIAL — NOT PASS** | `workspace_registry` code + unit tests exist; MCP repo tools guarded. No `registered_workspaces` table (mig 41), no registration endpoint, REST exec paths unguarded, subprocess sandbox does **not** enforce network isolation. `HOSTED_EXECUTION_ENABLED=false`. |
| 4 Global Commons publication | **PARTIAL — NOT PASS** | Existing `publish` flows scrub paths, require an explicit publisher, never auto-publish on verify. No single `PublicationService`, no dependency traversal / classification / sanitization record / authorization-audit event / `publication_records` table / withdrawal state machine. |
| 5 Data classification / provider policy | **NOT STARTED** | No classification vocabulary, no `model_provider_policies` registry, no `can_send(...)`. Provider calls gated only by API-key presence + the cost governor. |
| 6 Deletion / export / provenance | **NOT STARTED (spec requirements)** | Provenance substrate exists to reuse (`claim_sources`, `procedure_dependencies`, mig 32). No `DeletionService`, no `ExportService`, no `data_requests`, no tombstone/legal-hold model. |
| 7 Audit + frontend policy UX | **PARTIAL** | Frontend scope labels (PRIVATE/ORGANIZATION/GLOBAL CANDIDATE/GLOBAL VERIFIED) + publication-consequences panel **DONE** (`7f2fbb8`). Backend `audit.py` writer exists but `audit_events` table is unapplied and it has **zero callers**. |
| 8 Full acceptance matrix | **PARTIAL** | Matrix below. AUTH + PERSONAL/GLOBAL authorization + sandbox traversal are green; ORGANIZATION, publication gate, provider policy, deletion/export, audit wiring are not implemented or not proven. |

---

## Per-phase detail

### Phase 1 — Authentication (COMPLETE)

**Implementation**
- Backend token validation: `backend/app/services/authn.py` — OIDC ASGI middleware, JWKS off-loop, alg whitelist (`ES256`/`RS256`, never `none`/HS256), `iss`/`aud`/`exp`/`nbf`/`sub` checks, present-but-invalid → 401, missing → 401 only in private posture. Supabase preset in `OidcConfig.from_settings`.
- `backend/app/api/deps.py`:
  - `require_authenticated_user` → `AuthenticatedPrincipal(user_id, subject, issuer, email, name, org_ids, claims)` — the **one** reusable authenticated-user dependency. Identity = verified token `sub` + provisioned `users` row + resolved memberships. **Never** reads request body/query/path. 401 missing · 403 deactivated (`IdentityInactive`) · 409 ambiguous org (`AmbiguousTenant`).
  - `optional_authenticated_user` for public-but-richer routes.
  - `principal.access_scope()` → `AccessScope.for_org_member` / `for_user`, resolved before ranking.
- `backend/app/main.py`: `assert_boot_posture` now uses `oidc_configured()` (counts the Supabase preset) and passes `hosted_execution_enabled`.
- Frontend `frontendv1`: `src/lib/supabase/client.ts` (one browser client, `sb_publishable_` key only, PKCE, `persistSession` + `autoRefreshToken` + `detectSessionInUrl`); `src/lib/auth.ts` reworked onto Supabase with a synchronous `authHeaders()` (so `src/lib/api/client.ts` is untouched); `/auth` (email sign-up/sign-in + Google); `/auth/callback`; dev `X-Viewer-Id` fallback kept only when Supabase is unconfigured.

**Tests — exact result**
```
pytest tests/test_supabase_auth_dependency_offline.py \
       tests/test_procedure_fast_create_offline.py \
       tests/test_authn_offline.py tests/test_access.py \
       tests/test_phase1_security_boundaries_offline.py \
       tests/test_hardening_h1_identity_tenancy.py \
       tests/test_hardening_h2_rls_backstop.py \
       tests/test_mcp_server_identity_offline.py \
       tests/test_apply_change_set_removed_security.py \
       tests/test_approval_decompose_actor_precedence_offline.py \
       tests/test_agent_store_actor_precedence_offline.py
=> 168 passed, 0 failed
```
Frontend `tsc --noEmit` → clean. Backend boot-posture check against the live
Supabase config → passes.

**Closeout checklist**
| Item | Status | Evidence |
|---|---|---|
| Supabase Auth is canonical identity | ✅ | `OidcConfig.from_settings` preset; live JWKS is ES256/P-256; `oidc_configured()`=True |
| Frontend uses one Supabase browser client | ✅ | `src/lib/supabase/client.ts` singleton; only importer of `@supabase/supabase-js` |
| Frontend exposes only anon/publishable creds | ✅ | `sb_publishable_…` in `.env.local` (gitignored); no `service_role`/`sb_secret_` in `frontendv1/` |
| PKCE / session persistence / refresh | ✅ (code) | `flowType:'pkce'`, `persistSession`, `autoRefreshToken`; not exercised by a real human yet |
| email/password signup/signin/signout | ✅ (code) | `signUpWithPassword` / `signInWithPassword` / `signOut`; `/auth` renders the form |
| Google OAuth callback path | ✅ (code) | `signInWithGoogle` + `/auth/callback` (200). Requires a Google OAuth client in Supabase to actually complete. |
| Backend has exactly one reusable authed-user dependency | ✅ | `require_authenticated_user` (grep: no other `require_*` auth dep in repo) |
| JWT subject is the identity | ✅ | `AuthenticatedPrincipal.subject = actor.subject`; write paths use `current_actor_id()` |
| users / org membership resolved server-side | ✅ | `ensure_user` + `resolve_memberships` in the dependency; `test_hardening_h1` |
| client-supplied user_id cannot impersonate | ✅ | `test_phase2_authorization_offline` (validated actor beats spoofed `X-Viewer-Id`; create bodies carry no identity field); `test_*_actor_precedence_offline` |
| public routes remain public | ✅ | `get_scope` → anonymous → `visibility='public'`; `test_authn_offline` middleware pass-through cases |
| authed routes reject missing/invalid/expired | ✅ | `test_supabase_auth_dependency_offline` (401 missing); `test_authn_offline` (invalid/expired → 401, never degrades to anonymous) |
| deactivated users rejected | ✅ | `require_authenticated_user` → 403 on `IdentityInactive`; `test_supabase_auth_dependency_offline` |
| ambiguous org resolution explicit | ✅ | 409 on `AmbiguousTenant`; `test_hardening_h1` (`tenant_scope_for` raises, no implicit pick) |
| existing worker/service auth still works | ✅ | `test_mcp_server_identity_offline` / `test_apply_change_set_removed_security` pass; service paths use `TenantScope.commons()` unchanged |
| existing MCP / global behavior not broken | ✅ | MCP identity + six-tool surface offline pass (bar the pre-existing ingestion-lane `embedding_model_id` failures, unrelated) |

**Unresolved (Phase 1):** none in code. Operational: a real end-to-end
browser sign-in has not been performed (`users` table empty). If Supabase
"Confirm email" is on, first sign-up needs an email click.

### Phase 2 — Authorization / scope (PARTIAL)

**Implementation**
- `backend/app/services/access.py` remains the single enforcement point
  (`visibility_predicate` + `tenant_predicate` + `scope_predicates`; `tenant_scope`
  has no default → `TypeError` on omission). Unchanged this phase.
- `deps.py::get_scope` hardened: `X-Viewer-Id` is honoured **only when
  `oidc_configured(settings)` is False** (fully-public dev posture). With
  Supabase configured, an unauthenticated request is anonymous — closing the
  hole where a caller could read another user's private procedure by sending
  `X-Viewer-Id: <owner subject>`.
- `procedures.py` fast-create endpoints write `owner_id = token subject`
  (what `get_scope` filters an owner read by), not the provisioned `users.id`.
- Access is applied in the SQL `WHERE` before ranking: `procedure_graph_api._fetch_visible_procedure`
  (`... WHERE id=$1 AND {visibility_predicate}`), `search_global` legs carry the
  scope predicate (`test_wave3_tenancy_adoption`).

**Tests — exact result**
```
pytest tests/test_phase2_authorization_offline.py    => 8 passed
pytest tests/test_procedure_fast_create_offline.py   => 5 passed
pytest tests/test_access.py                          => all pass
pytest tests/test_procedure_graph_api_offline.py \
       tests/test_personal_contributions_offline.py  => all pass
combined targeted run                                => 65 passed
```
DB-backed cross-user / cross-org proofs (`test_cross_user_isolation_e2e.py`,
`test_product_model_privacy_e2e.py`, `test_agent_store_idor_e2e.py`,
`test_second_user_global_reuse_e2e.py`) exist and **skip** in the offline run
(need `DATABASE_URL`); they were **not executed against the production DB**.

**Acceptance sub-matrix**
| Case | Result |
|---|---|
| own resource → allow | ✅ (owner predicate bound to subject) |
| another user's private → deny | ✅ offline (bound-param proof) · ⚠️ DB e2e not run |
| same-org resource → allow | ❌ not implementable (`procedures` has no `tenant_id`; `'org'` enum unapplied) |
| another-org → deny | ❌ same |
| anonymous → public | ✅ |
| anonymous → private | ✅ (`X-Viewer-Id` no longer names a user once Supabase configured) |
| IDOR via body/query/path | ✅ structural (create bodies expose no id/scope field; routes take only `body,pool,principal`) |

**Unresolved (Phase 2):** ORGANIZATION / PROJECT / REPOSITORY scope on
`procedures` needs a schema change (`tenant_id` column or a `scope_type='organization'`
mapping) + migration 41. Legacy GLOBAL/PROJECT/REPOSITORY/PERSONAL vocabulary
not mapped to a single enum. DB-backed adversarial matrix not run here.

### Phase 3 — Hosted repository / execution security (PARTIAL — NOT PASS)

**Implementation (present)**
- `backend/app/services/workspace_registry.py` — `resolve_workspace_for_actor`
  (workspace row must exist, be active, belong to the actor's tenant; foreign
  tenant → `WorkspaceNotFound`, not Forbidden, to avoid existence leak);
  `enforce_hosted_repo_path` (returns the registry's `storage_path`, never the
  caller's string; a supplied `repo_path` must match exactly).
- `backend/app/mcp_server/server.py::_authorize_repo_execution` — wired into
  `find_best_way` and `reproduce_procedure` (both tiers). Local mode: `repo_path`
  passthrough. Hosted mode: caller names a registered `workspace_id`; server
  resolves the path after tenant-ownership check.
- Sandbox traversal / symlink / absolute-path escape guards in the executor.

**Tests — exact result**
```
pytest tests/test_phase1_security_boundaries_offline.py  => 23 passed
       (workspace resolution, foreign-tenant NotFound, path mismatch refused,
        local passthrough, migration-41 static asserts)
pytest tests/test_agent_sandbox_dispatch_traversal_e2e.py \
       tests/test_sandbox_input_path_escape.py \
       tests/test_container_sandbox_offline.py \
       tests/test_sandbox_executor.py                     => 46 passed, 20 skipped
```

**Gaps (why NOT PASS)**
- `registered_workspaces` table **does not exist** (migration 41 unapplied) →
  `HOSTED_EXECUTION_ENABLED` cannot be turned on.
- **No workspace-registration endpoint / service** — only resolution exists.
- **REST execution paths** (`app/api/agents.py`, `app/api/runs.py`) have **no**
  hosted-workspace guard; only the two MCP tools do.
- `SubprocessSandboxExecutor` emits: *"network_access=False is NOT enforced —
  this executor cannot actually block network calls."* → LC-001 "network policy"
  is not met on that path.
- No audit of CPU / memory / process / timeout limits against LC-001.
- No repository "deleted / disconnected" state or check.
- "unauthorized execution creates NO execution record" / "execution records
  principal+org+repository" — not implemented, not tested.

### Phase 4 — Global Commons publication (PARTIAL — NOT PASS)

**Present:** `publish` flows (`test_publish_e2e.py`, `test_local_claims_publish_e2e.py`
— skip offline) redact absolute paths, require an explicit publisher, create a
fresh candidate, and never auto-publish on verification. Frontend now states the
consequences before "Propose to commons" (`7f2fbb8`).

**Missing:** one canonical `PublicationService`; dependency traversal
(procedure→impl→task→claims→observations→sources); data classification;
privacy/secret/confidential rejection with a recorded sanitization result;
source/license/IP provenance gate; publication authorization + audit event;
`publication_records` (spec §24) table; withdrawal state machine (spec §25);
explicit test that private verification counts are not copied.

### Phase 5 — Data classification / provider policy (NOT STARTED)

No `PUBLIC_SOURCE … AUDIT_DATA` vocabulary. No `model_provider_policies`
registry (provider/model/endpoint/region/retention/training-use/subprocessors/
DPA/transfer/deletion/allowed-classes/effective dates). No
`can_send(classification, provider, model, tenant_policy)` decision point.
Embedding and LLM egress is gated only by API-key presence + `CostGovernor`.

### Phase 6 — Deletion / export / provenance (NOT STARTED for spec requirements)

Provenance chain (source→observation→claim→procedure→implementation→execution→
evidence) is preserved by the existing substrate and its e2e replay tests. No
dependency-aware `DeletionService`, no tombstone / legal-hold model, no
`ExportService`, no `data_requests` table, no per-user export bundle.

### Phase 7 — Audit + frontend policy UX (PARTIAL)

**Done:** `frontendv1/src/components/scope-badge.tsx` — canonical
PRIVATE / ORGANIZATION / GLOBAL CANDIDATE / GLOBAL VERIFIED labels from backend
`visibility` + `verification_state`; shown on the procedure detail header and in
`/submit`. `/submit` "Propose to commons" shows a consequences panel
(visibility change, publication-gate steps, "your private execution history and
evidence stay private"). Quick Add stays private by default. `tsc` clean.

**Not done:** `backend/app/services/audit.py::record_audit_event` has **zero
callers** and `audit_events` is unapplied — no sensitive transition
(repo connect/disconnect, private create/delete, publication req/approve/reject,
candidate created, verified, scope change, export/deletion, provider allow/deny,
incident open/close) is recorded. Privacy/Data settings page not built.

### Phase 8 — Acceptance matrix (run results)

| Group | Case | Result | Test / evidence |
|---|---|---|---|
| AUTH | signup / signin / signout | code ✅, not human-exercised | `signUpWithPassword`/`signInWithPassword`/`signOut`; `/auth` renders |
| AUTH | Google OAuth structural flow | code ✅ | `signInWithGoogle` + `/auth/callback`; needs Google client in Supabase |
| AUTH | refresh persistence | code ✅ | `persistSession` + `autoRefreshToken` |
| AUTH | expired token | ✅ | `test_authn_offline` (expired → 401) |
| AUTH | malformed token | ✅ | `test_authn_offline` (garbage/`alg:none`/kid-unknown → 401) |
| AUTH | central JWT validation | ✅ | one ASGI validator + one dependency |
| AUTH | server-derived identity | ✅ | `test_phase2_authorization_offline`, `test_*_actor_precedence_offline` |
| AUTH | deactivated user | ✅ | `test_supabase_auth_dependency_offline` (403) |
| AUTHZ | own resource | ✅ | owner predicate |
| AUTHZ | another user's private | ✅ offline / ⚠️ DB e2e skipped | `test_phase2_authorization_offline`; `test_cross_user_isolation_e2e` (skipped) |
| AUTHZ | same-org / another-org | ❌ | no `tenant_id` on `procedures`; mig 41 unapplied |
| AUTHZ | public access | ✅ | anonymous → public-only |
| AUTHZ | IDOR (body/query/path/ids) | ✅ structural / ⚠️ DB e2e partial | create bodies expose no id; `test_agent_store_idor_e2e` (skipped) |
| AUTHZ | access before ranking | ✅ | scope predicate in SQL `WHERE` (`_fetch_visible_procedure`, `test_wave3_tenancy_adoption`) |
| REPOSITORY | authorized repo | ⚠️ code only | `workspace_registry` unit tests |
| REPOSITORY | unauthorized / cross-user / cross-org repo | ⚠️ code only | `resolve_workspace_for_actor` → NotFound; no DB / REST-path proof |
| REPOSITORY | path traversal / symlink escape | ✅ | `test_sandbox_input_path_escape`, `test_agent_sandbox_dispatch_traversal_e2e` (46 passed) |
| REPOSITORY | workspace isolation / disposable | ⚠️ partial | executor tests pass; disposable-workspace lifecycle not audited |
| REPOSITORY | network policy | ❌ | `SubprocessSandboxExecutor` does not block network |
| REPOSITORY | secret isolation / no ambient creds | ⚠️ not audited | — |
| PUBLICATION | valid / unauthorized publication | ⚠️ partial | `test_publish_e2e` (skipped offline); no canonical gate |
| PUBLICATION | missing provenance / private dependency / secret / personal / confidential | ❌ | no dependency traversal or classification |
| PUBLICATION | withdrawal | ❌ | not implemented |
| PROVIDER POLICY | any case | ❌ | not implemented |
| DELETION / EXPORT | any case | ❌ | not implemented |
| AUDIT | required events / no secret leakage | ❌ | writer exists, zero callers, table unapplied |
| MCP | global access | ✅ | `test_mcp_server_identity_offline` / six-tool surface (bar ingestion-lane failures) |
| MCP | authenticated private access | ⚠️ | no end-user auth transport for MCP; documented |
| MCP | unauthorized private access blocked | ✅ | `_caller_access_scope` → anonymous fallback; graph mutation gated |
| MCP | no private leakage | ⚠️ | holds today (nothing private on MCP path); revisit when org scope lands |
| REGRESSION | retrieval / embeddings / procedural memory | ⚠️ | 18 failing tests in the ingestion/embedding lane (core-b, active) |
| REGRESSION | Gate 3 | ⚠️ | `test_gate3_experiment_offline` 1 failing (ingestion lane) |
| REGRESSION | ingestion infrastructure | ⚠️ | `test_migration_upgrade_e2e` failing (`embedding_provider` column) |
| REGRESSION | worker/service auth | ✅ | `test_mcp_server_identity_offline`, `test_apply_change_set_removed_security` |

---

## Database migration state

| Migration | State | Notes |
|---|---|---|
| 01–40, 42–45 | applied | verified via `scripts/migrate.py --status` |
| **41_phase1_security_boundaries.sql** | **PENDING — NOT APPLIED** | Adds: `ALTER TYPE visibility_level ADD VALUE 'org'` (**IRREVERSIBLE** — an enum value cannot be dropped), `CREATE TABLE IF NOT EXISTS audit_events`, `CREATE TABLE IF NOT EXISTS registered_workspaces`. The two `CREATE TABLE`s are safe/idempotent; the `ALTER TYPE` is the one-way door. |

**To apply (one user command — the environment's safety classifier blocks the agent from running it):**
```
cd backend && python scripts/migrate.py
```
No data is rewritten. Existing `public` rows stay the global commons; `private`
rows are unchanged. Nothing about existing global ownership changes.

**Not done / not safe to automate:** nothing was wiped, reset, truncated, or
hand-mutated. No production data was edited to make a test pass.

---

## Production configuration state

| Setting | Where | State |
|---|---|---|
| `SUPABASE_PROJECT_URL` | `backend/.env` | set (https://…) |
| `SUPABASE_JWT_AUDIENCE` | `backend/.env` | `authenticated` |
| `NEXT_PUBLIC_SUPABASE_URL` | `frontendv1/.env.local` | set |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `frontendv1/.env.local` | set (`sb_publishable_…`) |
| Supabase JWT signing key | Supabase dashboard | **P-256 / ES256 asymmetric key is live** (JWKS confirmed) |
| Supabase Email provider | Supabase dashboard | assumed on (default); "Confirm email" toggle is the operator's choice |
| Supabase Google provider | Supabase dashboard | **requires a Google OAuth client id/secret** — not verified; email/password works without it |
| Supabase redirect allow-list | Supabase dashboard | must include `<origin>/auth/callback` for every deployed origin |
| `REAL_AUTH_ENABLED` / `PRIVATE_VISIBILITY_ENABLED` | `backend/.env` | both `false` — the new authenticated endpoints require a token regardless; flip these only when retrofitting every read route |
| `HOSTED_EXECUTION_ENABLED` | `backend/.env` | unset/false (local/loopback posture) |
| `frontendv1` deployment | — | **not deployed** (no `.vercel`, not in `render.yaml`); runs only via `npm run dev`. Old `frontend/` is the one on Vercel. |

### Secrets / configuration checklist (no values)

- [x] Supabase project URL — backend + frontend
- [x] Supabase publishable (anon) key — frontend only, gitignored, browser-safe
- [x] Supabase JWT audience — backend
- [x] Supabase asymmetric (P-256) signing key active
- [ ] Supabase Google OAuth client id + secret (only if Google login is wanted)
- [ ] Supabase redirect URLs for the deployed origin(s)
- [ ] `frontendv1` deployment target + its `NEXT_PUBLIC_*` vars
- [x] `service_role` / `sb_secret_` key absent from all frontend code and env
- [ ] Migration 41 applied
- [ ] `HOSTED_EXECUTION_ENABLED` left false until Phase 3 is closed

---

## Security findings (this work)

1. **Header-asserted identity while private data exists** — before this change,
   `X-Viewer-Id` named a user even with Supabase configured, so a private
   procedure could be read by spoofing the owner's subject. **Fixed** in
   `deps.py::get_scope` (`120a506`): the header is ignored once
   `oidc_configured()` is true. Regression: `test_phase2_authorization_offline`.
2. **Owner-identity mismatch in the fast-create path** — `create_procedure`
   wrote `owner_id = users.id` while `get_scope` filters owner reads by the
   token subject, so a creator could not see their own private procedure.
   **Fixed** (`120a506`), `owner_id = subject`. Regression added.
3. **Boot posture ignored the Supabase preset** — `main.py` computed
   `oidc_configured_` from generic `OIDC_ISSUER/OIDC_AUDIENCE` only, so a
   Supabase-only deployment with `real_auth_enabled=true` would have refused to
   boot. **Fixed** (`3e47906`), now via `oidc_configured()`; also passes
   `hosted_execution_enabled` to the guard.
4. **Subprocess sandbox does not enforce network isolation** — pre-existing,
   documented in the executor's own warning. Blocking for hosted execution
   (Phase 3), not for the current local posture.
5. **`access.py` `'org'` clause references a `procedures.tenant_id` column that
   does not exist** — latent bug from `5d03159`; an org-member query on
   `procedures` would raise. Contained today because no member/org rows exist
   and `get_scope` never emits `for_org_member`. Must be resolved before Phase 2
   org scope.

---

## Known limitations

- No real human sign-in has been performed; `users` table is empty.
- ORGANIZATION / PROJECT / REPOSITORY authorization is not enforceable on
  `procedures` (no `tenant_id` column; `'org'` enum unapplied).
- MCP has no per-end-user auth transport; the shared-token fallback is
  single-identity by design and documented.
- The 18 failing offline tests belong to the ingestion/embedding lane
  (`Embedder.embedding_model_id` / `embedding_provider`) and are actively being
  changed by another workstream.
- `frontendv1` is undeployed.

---

## Exact remaining blockers

| # | Blocker | Class | Owner action |
|---|---|---|---|
| B1 | Migration 41 not applied | **BLOCKING** for Phases 2(org)/3/7 | `cd backend && python scripts/migrate.py` (agent is classifier-blocked from running it). Irreversible `ALTER TYPE` — identified. |
| B2 | Phase 4 publication gate absent | **BLOCKING** for launch (LC-002, a stated launch feature) | implement `PublicationService` + `publication_records` + dependency/classification/sanitization/withdrawal |
| B3 | Phase 5 provider policy absent | **BLOCKING** (LC-005; private data can currently reach any provider with a key) | implement `model_provider_policies` + `can_send()`; route embeddings + LLM through it |
| B4 | Phase 6 deletion + export absent | **BLOCKING** (LC-006/007; DPDP/GDPR rights workflows) | implement `DeletionService` + `ExportService` + `data_requests` |
| B5 | Phase 7 audit writer unwired | **BLOCKING** (LC-011; every sensitive transition must be attributable) | apply mig 41, then call `record_audit_event` from auth/publication/workspace/deletion/export/provider paths |
| B6 | Phase 3 hosted-exec gaps (no table, no registration endpoint, REST paths unguarded, no network isolation) | **BLOCKING** only if hosted repos ship at launch; **NON-BLOCKING** if launch stays local/loopback (`HOSTED_EXECUTION_ENABLED=false`) | close before enabling hosted execution |
| B7 | DB-backed adversarial matrix (cross-user/cross-org/IDOR/publication) not executed | **NOT MEASURED** | run `*_e2e.py` against a disposable Postgres, not production |
| B8 | Google OAuth client + redirect URLs in Supabase | **NON-BLOCKING** (email/password works) | configure if Google login is desired |
| B9 | `frontendv1` undeployed | **NON-BLOCKING** for correctness; **BLOCKING** for a public launch | link the folder to a Vercel project + set `NEXT_PUBLIC_*` + redirect allow-list |
| B10 | Ingestion/embedding lane: 18 failing tests | **NOT MEASURED** here (other workstream) | that lane must go green before its own gate |

---

## RELEASE GATES

**AUTH/POLICY RELEASE GATE: NOT PASS**

Authentication (Phase 1) is complete and verified. Authorization (Phase 2) is
enforced for PERSONAL and GLOBAL only. Phases 3–7 (hosted execution hardening,
publication gate, provider policy, deletion/export, audit wiring) are partial or
absent, and migration 41 is unapplied. Blockers B1–B6.

**RETRIEVAL/PROCEDURAL-MEMORY RELEASE GATE: NOT PASS**

The offline suite is 2270 passed / 18 failed. The 18 failures are all in the
retrieval/embedding/ingestion lane (`Embedder.embedding_model_id` /
`embedding_provider`) plus `test_migration_upgrade_e2e` (`embedding_provider`
column). This gate cannot pass while that lane is red. It is a separate
workstream (core-b) and was not modified by the auth/policy work; the auth/policy
changes add zero new failures.

**INGESTION RESUME: NOT ALLOWED**

Prerequisites are not proven complete: (1) the ingestion/embedding lane has 18
failing tests including `test_migration_upgrade_e2e`; (2) migration 41 is
unapplied; (3) the audit trail that would record ingestion-sourced global
candidates (Phase 7) is unwired. Resume only after that lane is green, migration
41 is applied, and the publication/audit path can attribute what ingestion
writes.

---

## Commits (this work, on `gate-2b`, not pushed)

| SHA | What |
|---|---|
| `3e47906` | Phase 1 backend: `require_authenticated_user`, boot-posture fix, fast contribution endpoints |
| `e63ba85` | Phase 1 frontend: Supabase client, sign-in, `/submit` Quick Add |
| `382d53b` | Phase 0/1 ledger + board checkpoint |
| `33463dc` | docs: P-256 → ES256 terminology |
| `120a506` | Phase 2: `get_scope` hardening, owner-identity fix, IDOR tests |
| `7f2fbb8` | Phase 7 frontend: canonical scope labels + publication consequences |
| (this file) | Final release readiness report |
