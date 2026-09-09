# StealthLab — Final Release Readiness (Auth / Policy / Launch Compliance)

**Date:** 2026-09-09 (final — Option A)
**Branch:** `gate-2b` · **Acceptance checkpoint tag:** `authpolicy-verified-a610645`
**Specs:** `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` (LC-001..012),
`STEALTHLAB-DATA-FLOW-AND-PROVENANCE-SPEC-V1.md` (INV-01..10)
**Companion:** `docs/launch_compliance_implementation_ledger.md` (Phase 0 audit + per-requirement ledger)

Authoritative status of what is proven vs. designed vs. absent. Reconstructable
from git + tests without chat history.

---

## MERGE DECISION — Option A (2026-09-09)

The 19 auth/policy commits **cannot be cleanly cherry-picked onto `origin/main`**
(still `33d4c05`): Phase 1's `skill_ingestion.py` hard-imports
`RETRIEVAL_DOCUMENT_IMPORT_VERSION` which exists only in core-b's S3 retrieval
commit `289947f`; Phase 5 `embeddings.py` sits on the S4/S5 + `e476e96` chain;
Phase 7's frontend detail page conflicts with S8's redesign. A retrieval-free
extraction would require re-authoring Phase 1/5/7 against an older base →
untested code.

**Decision: Option A.** Auth/policy is tagged `authpolicy-verified-a610645` as
the verified-acceptance checkpoint on `gate-2b`. Nothing pushed to `main`. No
branches deleted. The whole `gate-2b` stack merges to `main` in one unit once
the **retrieval release gate** passes (core-b's retrieval *code* e2e is green;
their open items are product-readiness — embedding-model apply/billing,
threshold re-derivation, eval-label validation, abstention gap — see
`.scratch/retrieval-release-closure/FINAL-REPORT.md`).

```
AUTH/POLICY IMPLEMENTATION: PASS
AUTH/POLICY ACCEPTANCE:     PASS  (offline 2330/18/314, 0 net-new; disposable-DB e2e 75/0 + 24 + live smoke)
PHASE 3 LAUNCH SCOPE:       OUT OF SCOPE  (hosted_execution_enabled resolves False)
NEW REGRESSIONS:            NO
MERGE TO MAIN:              DEFERRED (Option A) — pending retrieval gate + combined integration gate
INGESTION RESUME:           BLOCKED — pending retrieval + combined gate
```

---

## Verified current state

| Item | Result |
|---|---|
| Supabase JWT | live JWKS serves one key: **EC / ES256 / P-256**, kid `1206eda7…`. Backend `FetchingJwks` now keeps EC keys (was RSA-only → 500 on every authed request; fixed `7d1e8fa`, verified against the live JWKS). |
| Backend auth config | `oidc_configured(settings)` = **True**; issuer `…/auth/v1`, `aud=authenticated`, algs `ES256/RS256`. `assert_boot_posture` passes. |
| Env (no secrets) | `backend/.env`: `SUPABASE_PROJECT_URL`, `SUPABASE_JWT_AUDIENCE=authenticated`. `frontendv1/.env.local`: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (`sb_publishable_…`, gitignored). No `service_role`/`sb_secret_` in the frontend. |
| Frontend deps | `@supabase/supabase-js@2.116.0`. `next@16.3.4`. `tsc --noEmit` clean. |
| Frontend `/auth` | renders the email/password + Google branch; `/auth/callback` 200; dev server boots clean on `.env.local`. |
| Migrations applied | 01–48 — incl. **41** (`audit_events`, `registered_workspaces`, `'org'` enum), **46** (`model_provider_policies` + 8 seed rows, `publication_records`, `data_requests`), **47** (`procedures.tenant_id`), **48** (`contributor_profiles` — the peer session's people layer). All applied by the operator 2026-09-08. |
| Migrations PENDING | none for launch compliance. |
| DB state | `procedures.tenant_id` present; `model_provider_policies` 8 rows (external → PUBLIC_* only; `local` → private classes); `publication_records` / `data_requests` 0 rows (nothing published/requested yet). `visibility_level` = `{public, private, org}`. `users` 0. |
| Offline test baseline | see the "Regression" section below. |

---

## Phase status

| Phase | Verdict | Evidence |
|---|---|---|
| 0 Reconstruction / audit | **COMPLETE** | ledger, `3e47906` |
| 1 Authentication + canonical identity | **COMPLETE** (code + live config; JWKS bug fixed) | `3e47906` `e63ba85` `33463dc` `7d1e8fa` |
| 2 Centralized authorization / scope | **COMPLETE + ACCEPTANCE-VERIFIED** (mig 47 applied; ORG isolation proven on disposable DB) | `120a506` `a8d25dc` |
| 3 Hosted repository / execution security | **OUT OF LAUNCH SCOPE** — `hosted_execution_enabled` resolves False; registry dormant; local/loopback trusted posture preserved | `f18651a` (+ `5d03159`) |
| 4 Global Commons publication | **COMPLETE + ACCEPTANCE-VERIFIED** (mig 46 applied; publish/withdraw e2e + live smoke green) | `7ec8c51` |
| 5 Data classification / provider policy | **COMPLETE + ACCEPTANCE-VERIFIED** (mig 46 seeds live; can_send deny/allow + embeddings gate proven) | `6cea7fc` |
| 6 Deletion / export / provenance | **COMPLETE + ACCEPTANCE-VERIFIED** (mig 46 `data_requests` live; dependency-aware, legal-hold) | `939921a` `4ee89d5` |
| 7 Audit + frontend policy UX | **COMPLETE** (writer wired across all LC-011 transitions; scope labels; consequences; Privacy&Data page) | `f18651a` `7f2fbb8` `4ee89d5` |
| 8 Acceptance matrix | **PASS** — full offline regression + DB-backed adversarial e2e on disposable Postgres | this document |

"ACCEPTANCE-VERIFIED" = implementation + offline proving tests + DB-backed
adversarial e2e on a **disposable local Postgres 17 + pgvector 0.8.0**
(migrations 01→48 fresh, torn down after) — 75 passed / 0 failed — + a live
gate smoke. Migrations 41/46/47/48 are all applied to the production Supabase
DB (verified: 8 provider-policy rows, `procedures.tenant_id`, all tables
present; no pending, no checksum mismatch).

---

## Per-phase detail

### Phase 1 — Authentication (COMPLETE)

Unchanged from the prior report **plus the JWKS EC-key fix (`7d1e8fa`)**:
`FetchingJwks._refresh` filtered the JWKS to `kty=="RSA"`, discarding Supabase's
EC/P-256 signing key → `KeyError(kid)` → 500 on every authenticated request.
Now keeps RSA + EC, skips keyless/symmetric entries. Regression:
`test_authn_jwks_ec_keys_offline.py` (2). Verified: kid
`1206eda7-f95e-4e09-bc7b-2bcae21f417c` resolves to an `ECPublicKey`.

Closeout checklist: all 15 items green (see prior report; unchanged).

### Phase 2 — Authorization / scope (COMPLETE for PERSONAL + GLOBAL; ORG pending mig 47)

- `access.py` is the one enforcement point; access is applied in the SQL `WHERE`
  before ranking (`_fetch_visible_procedure`, `search_global` legs).
- `get_scope` hardened (`120a506`): `X-Viewer-Id` honoured **only** in the
  fully-public dev posture; once Supabase/OIDC is configured, an unauthenticated
  request is anonymous.
- `get_scope` now resolves org memberships → `AccessScope.for_org_member`
  (`a8d25dc`); resolution failure degrades to owner scope, never 500.
- `db/47` adds `procedures.tenant_id`; `capture_procedure` accepts
  `visibility='org'` (+ required `tenant_id`), threaded into the INSERT.
- Owner-identity fix (`120a506`): fast-create writes `owner_id = token subject`
  (what `get_scope` filters an owner read by).

Tests: `test_phase2_authorization_offline.py` 11 (validated actor beats spoofed
header; header ignored once Supabase configured; header works in the public
posture; anonymous = public-only; owner predicate is a bound param;
create bodies carry no id/scope field; `get_scope` resolves org membership;
org-A predicate cannot match org-B; resolution failure → owner scope).

Sub-matrix: own → allow ✅; other user's private → deny ✅ (offline bound-param
proof; DB e2e skipped); same-org → allow / other-org → deny ✅ **at the
predicate level** (needs mig 47 + org rows for a live proof); anon → public ✅;
anon → private ✅; IDOR via body/query/path ✅ structural.

### Phase 3 — Hosted repository / execution security (PARTIAL — OUT OF LAUNCH SCOPE)

**Launch decision (2026-09-08): this launch does not host user repositories.**
`HOSTED_EXECUTION_ENABLED=false`. `repo_path` stays local/loopback passthrough,
the workspace registry is dormant, and the open items below gate only a future
hosted-repo SaaS feature — they are NON-BLOCKING for this launch. Operational
guardrail: keep the MCP server local/trusted (its documented posture).


Done:
- `workspace_registry.resolve_workspace_for_actor` (tenant-ownership; foreign
  tenant → NotFound) + `enforce_hosted_repo_path` (registry path wins).
- `workspace_registry.register_workspace` (`f18651a`): owner/admin role only;
  `storage_path` canonicalised (`realpath`, no traversal) and must sit under an
  allowed root prefix — a hard backstop against binding `/etc` or a home dir.
  Emits `workspace_registered` audit.
- MCP repo tools (`find_best_way`, `reproduce_procedure`) guarded.
- `GET/POST /v1/workspaces` (auth + org owner/admin).

Open (why PARTIAL):
- `registered_workspaces` now exists (mig 41) but **REST execution paths
  (`agents.py`, `runs.py`) are still unguarded** — only the two MCP tools resolve
  a workspace.
- `SubprocessSandboxExecutor` **does not enforce network isolation** (its own
  warning). LC-001 "network policy" unmet on that path.
- No CPU/mem/process/timeout audit against LC-001; no repo deleted/disconnected
  state; "unauthorized execution creates no record" not implemented.

Tests: `test_phase3_workspace_registration_offline.py` 4;
`test_phase1_security_boundaries_offline.py` 23; sandbox traversal/symlink 46.

### Phase 4 — Global Commons publication (COMPLETE)

`services/publication.py::publish_procedure` — the ONE operation, reusing
`publish.py`'s real secret + path scrub. Gate (all must pass, else
`PublicationDenied` with reasons + a `publication_rejected` audit event and NO
global row): actor owns the source · source is PRIVATE/ORG · not
EXECUTION_SECRET/PERSONAL/CONFIDENTIAL/SECURITY · dependency traversal (private/
org/unresolved dep blocks) · provenance present, license captured · sanitize +
residual-secret backstop. On success: fresh GLOBAL CANDIDATE via
`capture_procedure` (candidate, public, **no embedding / evidence / verification
counts carried**) + a `publication_records` row (sanitization + dependency +
classification reports, license, review_state) + `publication_approved` and
`global_candidate_created` audit events.

`withdraw_publication` traverses lineage → `RETAINED_AS_INDEPENDENTLY_SOURCED`
(independent evidence) / `REQUIRES_REMEDIATION` (mixed/unknown deps) /
`WITHDRAWN_FROM_RETRIEVAL` (tombstone: `availability='withdrawn'`, evidence
intact). Never rewrites historical evidence.

Endpoints: `POST /v1/procedures/{id}/publish`, `GET /v1/publications`,
`POST /v1/publications/{id}/withdraw`.

Tests: `test_phase4_publication_offline.py` 11 (owner allow; non-owner /
public-source / private-dep / missing-provenance / surviving-secret deny with
no global row; 4 withdrawal outcomes; unknown source 404).

**Requires mig 46 (`publication_records`) applied to run against the live DB.**

### Phase 5 — Data classification / provider policy (COMPLETE)

- `classification.py`: `DataClass` (the 10 canonical values) + deterministic
  `classify()` (secret/personal/confidential marker > visibility > source class
  > verified-public; unknown fails closed to `USER_PRIVATE`). `classify_procedure_row()`.
- `provider_policy.py`: `can_send(classification, provider, model, tenant)` →
  `PolicyDecision` against `model_provider_policies`. Most-specific effective row
  (tenant > global, exact model > `*`). **No row → DENY (fail closed).**
  `guard_send()` raises `ProviderPolicyDenied`. Deny always audited
  (`provider_call_denied`); allow audited on request.
- `db/46` seeds 8 providers — external ones carry `PUBLIC_*` + `GLOBAL_PROCEDURE`
  only; `local` (in-boundary) carries the private classes.
- `embeddings.Embedder`: optional `data_classification` + `policy_pool`;
  `_enforce_provider_policy()` gates every **external** provider call (`local`
  never gated). A private embedding cannot reach a provider whose policy omits
  its class. `create_procedure` classifies its text `USER_PRIVATE` and passes
  the gate — `ProviderPolicyDenied` → row captured without a vector (an
  `"indexing"` note), not a 500.

Tests: `test_phase5_provider_policy_offline.py` 12 (classification determinism,
allow/deny, fail-closed, `guard_send` raises, Embedder gates before any call).

**LLM-call sites in `app/debate/panel.py` are not yet individually wired** to
`can_send` — those calls operate on public procedure/claim text; the private
risk (embeddings, private-repo LLM runs) is covered. Documented gap.

**Requires mig 46 (`model_provider_policies` + seeds) applied to run live.**

### Phase 6 — Deletion / export / provenance (COMPLETE)

`services/data_rights.py`:
- `export_user_data(subject)` → `stealthlab.export/v1` bundle: account row,
  private procedures (`owner_id = subject`, non-public), publication **actions**
  as references (not the Commons objects — §22), preferences.
  `export_requested`/`export_completed` audit.
- `plan_user_deletion` (never mutates): splits private procedures into
  physical-delete (no downstream publication) vs tombstone (published source);
  lists preserved global objects + retained publication records.
- `delete_user_data(dry_run)`: executing → `DELETE` unpublished private rows
  (removes the private vector too, INV-17), `private_object_deleted` audit each;
  tombstone published sources (`availability='deleted'`, `embedding=NULL`,
  `t_invalid=now` — history queryable); independently-sourced global objects and
  `publication_records` untouched. Refused under legal hold.
- Endpoints: `GET /v1/me/export`, `GET /v1/me/deletion` (preview),
  `POST /v1/me/deletion` (execute).
- Frontend: `/me/privacy` (`4ee89d5`) — what StealthLab stores, download export,
  preview + confirm deletion, connected repos link, AI/provider note, privacy
  contact.

Tests: `test_phase6_data_rights_offline.py` 5.

**Requires mig 46 (`data_requests`) applied to run live.**

### Phase 7 — Audit + frontend policy UX (COMPLETE)

`audit_events` (mig 41) is written for every LC-011 transition:
`private_object_created` (procedures create + from_text),
`private_object_deleted` (data-rights physical delete),
`publication_approved` / `publication_rejected` / `global_candidate_created`,
`provider_call_allowed` / `provider_call_denied`,
`export_requested`/`export_completed`, `deletion_requested`/`deletion_completed`,
`workspace_registered`. `details` carries context, never secrets. Writes are
fail-closed where they are the authorization evidence (publication) and
best-effort where they must not fail the user operation (`private_object_created`).

Frontend: canonical scope labels PRIVATE / ORGANIZATION / GLOBAL CANDIDATE /
GLOBAL VERIFIED (`src/components/scope-badge.tsx`) on the procedure detail
header and `/submit`; publication-consequences panel; `/me/privacy` page.

Tests: `test_phase7_audit_offline.py` 3 (create emits `private_object_created`;
from_text emits it; a required-action-vocabulary net asserting each LC-011
action literal is emitted somewhere in the service/api layer).

Not done: `scope_changed`, `verified`, `incident opened/closed` transition
events (no incident subsystem yet); repo connect/disconnect (no repo-connect
flow — Phase 3).

### Phase 8 — Acceptance matrix

See "Acceptance matrix" and "Regression" below.

---

## Acceptance matrix (offline proving tests; `*_e2e.py` = skipped offline, NOT run against prod)

| Group | Case | Result | Test |
|---|---|---|---|
| AUTH | signup / signin / signout | code ✅ (not human-exercised) | frontend `auth.ts` |
| AUTH | Google OAuth structural | code ✅ (needs a Google client in Supabase — user hit `invalid_client`) | `/auth`, `/auth/callback` |
| AUTH | refresh persistence | code ✅ | `persistSession`+`autoRefreshToken` |
| AUTH | expired / malformed token | ✅ | `test_authn_offline` |
| AUTH | EC/ES256 Supabase key accepted | ✅ | `test_authn_jwks_ec_keys_offline` + live JWKS check |
| AUTH | central JWT validation | ✅ | one ASGI validator + `require_authenticated_user` |
| AUTH | server-derived identity | ✅ | `test_phase2_authorization_offline`, `test_*_actor_precedence_offline` |
| AUTH | deactivated user | ✅ (403) | `test_supabase_auth_dependency_offline` |
| AUTHZ | own resource | ✅ | owner predicate |
| AUTHZ | another user's private | ✅ offline / ⚠️ DB e2e skipped | `test_phase2_authorization_offline`; `test_cross_user_isolation_e2e` (skip) |
| AUTHZ | same-org / other-org | ✅ at predicate level / ⚠️ needs mig 47 + org rows | `test_phase2_authorization_offline` |
| AUTHZ | public access | ✅ | anonymous → public-only |
| AUTHZ | IDOR attempts | ✅ structural / ⚠️ DB e2e partial | `test_phase2_authorization_offline`; `test_agent_store_idor_e2e` (skip) |
| AUTHZ | access before ranking | ✅ | scope predicate in SQL `WHERE` |
| REPOSITORY | authorized repo | ⚠️ code + unit only | `workspace_registry` |
| REPOSITORY | cross-user / cross-org repo denied | ⚠️ code + unit only | `resolve_workspace_for_actor` → NotFound |
| REPOSITORY | path traversal / symlink escape | ✅ | `test_sandbox_input_path_escape`, `test_agent_sandbox_dispatch_traversal_e2e` |
| REPOSITORY | registration authz + path allowlist | ✅ | `test_phase3_workspace_registration_offline` |
| REPOSITORY | workspace isolation / disposable | ⚠️ partial | executor tests |
| REPOSITORY | network policy / secret isolation | ❌ | subprocess executor does not block network |
| PUBLICATION | valid publication → fresh candidate | ✅ | `test_phase4_publication_offline` |
| PUBLICATION | unauthorized publication | ✅ (no global row) | ″ |
| PUBLICATION | missing provenance | ✅ | ″ |
| PUBLICATION | private dependency | ✅ | ″ |
| PUBLICATION | secret / personal / confidential | ✅ (classification + residual-secret backstop) | ″ + Phase 5 |
| PUBLICATION | no private evidence / counts promoted | ✅ | ″ (capture forwards none) |
| PUBLICATION | withdrawal (4 outcomes) | ✅ | ″ |
| PROVIDER POLICY | allowed data/provider | ✅ | `test_phase5_provider_policy_offline` |
| PROVIDER POLICY | denied data/provider | ✅ (fail closed, audited) | ″ |
| PROVIDER POLICY | embedding routing protected | ✅ | Embedder gates before any call |
| PROVIDER POLICY | LLM routing protected | ⚠️ decision point exists; per-call-site wiring in panel.py pending | — |
| PROVIDER POLICY | region / policy version recorded | ✅ | `PolicyDecision` + audit details |
| DELETION | private deletion + dependency handling | ✅ | `test_phase6_data_rights_offline` |
| DELETION | embeddings / vector covered | ✅ (row delete / `embedding=NULL`) | ″ |
| DELETION | global knowledge preserved | ✅ | ″ |
| DELETION | legal hold respected | ✅ | ″ |
| EXPORT | authenticated export, correct scope | ✅ | ″ |
| EXPORT | no cross-user data / publication history as refs | ✅ | ″ |
| AUDIT | required events emitted | ✅ | `test_phase7_audit_offline` + Phase 4/5/6 suites |
| AUDIT | no secret leakage in details | ✅ (asserted) | `test_phase7_audit_offline` |
| SECRETS | service-role key never client-visible | ✅ | grep: none in `frontendv1/` |
| MCP | global access preserved | ✅ | `test_mcp_server_identity_offline` |
| MCP | unauthorized private access blocked | ✅ | `_caller_access_scope` anonymous fallback; graph mutation gated |
| MCP | no private leakage | ⚠️ holds today; revisit when org write path lands | — |
| MCP | authentication semantics documented | ✅ | no end-user MCP transport; shared-token fallback documented |
| REGRESSION | retrieval / embeddings / procedural memory | ⚠️ ingestion-lane suite red (other workstream) | see Regression |
| REGRESSION | worker/service auth | ✅ | `test_mcp_server_identity_offline`, `test_apply_change_set_removed_security` |

---

## Regression

Full offline suite (`DATABASE_URL` unset), latest run on `gate-2b` HEAD (401s):

**RESULT: 2330 passed / 18 failed / 314 skipped.** (Earlier run `2318` — the
delta is the people-layer session's own new tests, not auth/policy.)

Every one of the 18 failures classified:
- `test_local_agent_runner_offline` ×13 + `test_gate3_experiment_offline` ×1 +
  `test_behavioral_validation_offline` ×1 — **TEST DEFECT, PRE-EXISTING, UNRELATED**:
  `tests/fake_embeddings.py` (commit `827d745`, ingestion lane) monkeypatches
  `Embedder._embed_via_chain`, a method that never existed.
- `test_mcp_six_tool_surface_offline` ×2 — **TEST DEFECT, PRE-EXISTING, UNRELATED**:
  `fake_find()` missing an `embedding_model_id` kwarg the ingestion lane threaded.
- `test_migration_upgrade_e2e` ×1 — **PRE-EXISTING, UNRELATED**: migration-order
  bug since migration 42 (ingestion lane); confirmed still fails on a fresh DB.

**NEW REGRESSIONS FROM AUTH/POLICY: NO.** Zero of the 18 touch auth/policy code.
Independently corroborated by the retrieval session (16/18 fail on a checkout
predating their first commit; identical set with their deltas stashed).

The Phase-2 env-leak regression
(`test_claim_graph_api_offline::…owner_viewing_their_own_private_claim`, one run
showed 19 failed) is fixed in `b2265e9`. The 18 remaining failures are the
pre-existing ingestion/embedding lane
(`Embedder` missing `embedding_model_id`/`embedding_provider`;
`fake_find()` kwarg; `test_migration_upgrade_e2e` `embedding_provider` column) —
`test_local_agent_runner_offline` ×12, `test_behavioral_validation_offline` ×2,
`test_gate3_experiment_offline` ×1, `test_mcp_six_tool_surface_offline` ×2,
`test_migration_upgrade_e2e` ×1. None touch auth / access / procedures /
publication / provider-policy / data-rights / audit. This work adds **0** net
new failures once `b2265e9` lands.

New offline proving tests added this run (all green): `test_authn_jwks_ec_keys_offline`
(2), `test_phase2_authorization_offline` (11), `test_phase3_workspace_registration_offline`
(4), `test_phase4_publication_offline` (11), `test_phase5_provider_policy_offline`
(12), `test_phase6_data_rights_offline` (5), `test_phase7_audit_offline` (3) — 48.

---

## Database migration state

| Migration | State | Irreversible? | Notes |
|---|---|---|---|
| 01–45 + **41** | applied | 41's `ALTER TYPE … ADD VALUE 'org'` was (applied by operator) | — |
| **46_provider_policy_and_publication.sql** | **PENDING** | **No** | `model_provider_policies` (+ 8 seed rows), `publication_records`, `data_requests`. All `CREATE TABLE IF NOT EXISTS` / `INSERT … ON CONFLICT DO NOTHING`. |
| **47_procedures_tenant_id.sql** | **PENDING** | **No** | `ALTER TABLE procedures ADD COLUMN IF NOT EXISTS tenant_id UUID` + partial index. |

Apply: `cd backend && python scripts/migrate.py` (the agent is
classifier-blocked from running it). No data rewrite; no existing global
ownership change.

Nothing was wiped, reset, truncated, or hand-mutated.

---

## Production configuration state

| Setting | Where | State |
|---|---|---|
| `SUPABASE_PROJECT_URL` / `SUPABASE_JWT_AUDIENCE` | `backend/.env` | set |
| `NEXT_PUBLIC_SUPABASE_URL` / `_ANON_KEY` | `frontendv1/.env.local` | set (publishable key) |
| Supabase P-256/ES256 signing key | dashboard | **live** (JWKS confirmed) |
| Supabase Email provider | dashboard | assumed on (default) |
| Supabase Google provider | dashboard | **misconfigured** — user saw `Error 401: invalid_client`; needs a Google OAuth client id/secret + redirect `https://<ref>.supabase.co/auth/v1/callback`, or disable Google. Email/password works without it. |
| Supabase redirect allow-list | dashboard | add `<origin>/auth/callback` per deployed origin |
| `REAL_AUTH_ENABLED` / `PRIVATE_VISIBILITY_ENABLED` | `backend/.env` | both `false` — new endpoints require a token regardless; flip only when retrofitting every read route |
| `HOSTED_EXECUTION_ENABLED` | `backend/.env` | unset/false |
| `frontendv1` deployment | — | **not deployed** (no `.vercel`); old `frontend/` is the Vercel one |
| migrations 46, 47 | DB | **not applied** |

### Secrets / configuration checklist (no values)

- [x] Supabase project URL — backend + frontend
- [x] Supabase publishable key — frontend, gitignored
- [x] Supabase JWT audience — backend
- [x] Supabase asymmetric (P-256) signing key active
- [x] `service_role` / `sb_secret_` absent from all frontend code + env
- [ ] Migration 46 applied
- [ ] Migration 47 applied
- [ ] Supabase Google OAuth client id + secret (or Google disabled)
- [ ] Supabase redirect URLs for deployed origin(s)
- [ ] `frontendv1` deployment target + its `NEXT_PUBLIC_*` vars
- [ ] `HOSTED_EXECUTION_ENABLED` left false until Phase 3 REST-exec guard + network isolation land

---

## Security findings (this work)

1. **JWKS RSA-only filter → 500 on every authed request** — FIXED `7d1e8fa`. Regression added.
2. **Header-asserted identity while private data exists** — FIXED `120a506` (`X-Viewer-Id` ignored once identity configured). Regression added.
3. **Owner-identity mismatch in fast-create** — FIXED `120a506` (`owner_id = subject`). Regression added.
4. **Boot posture ignored the Supabase preset** — FIXED `3e47906`.
5. **`access.py` `'org'` clause referenced a missing `procedures.tenant_id`** — RESOLVED by mig 47 (pending apply) + `capture_procedure` contract.
6. **Subprocess sandbox does not enforce network isolation** — pre-existing, documented. Blocking for hosted execution only.
7. **Provider-policy fail-closed** — a missing policy row DENIES egress. Intended, but means external embedding of any classified data is blocked until mig 46 seeds exist.

---

## Remaining blockers

| # | Blocker | Class | Action |
|---|---|---|---|
| B1 | Migrations 46 + 47 not applied | **BLOCKING** (Phases 4/5/6 endpoints 500; org reads 500 once a user joins an org) | `cd backend && python scripts/migrate.py` — additive, no irreversible op |
| B2 | Phase 3: REST execution paths (`agents.py`, `runs.py`) unguarded by workspace authz | **BLOCKING** iff hosted repos ship at launch; **NON-BLOCKING** if launch stays local (`HOSTED_EXECUTION_ENABLED=false`) | wire `_authorize_repo_execution`-equivalent into those routers |
| B3 | Subprocess sandbox has no network isolation | **BLOCKING** for hosted execution; NON-BLOCKING for local posture | container executor / netns / egress proxy |
| B4 | LLM call sites in `panel.py` not individually policy-gated | **NON-BLOCKING** (they process public text; embeddings + private paths are gated) — close before any "run my private repo through an LLM" feature | thread `guard_send` into `_call_with_retry` with a per-call classification |
| B5 | DB-backed adversarial matrix (`*_e2e.py`) not executed | **NOT MEASURED** | run against a disposable Postgres, not production |
| B6 | Google OAuth misconfigured in Supabase | **NON-BLOCKING** (email/password works) | add a Google client, or disable the provider |
| B7 | `frontendv1` undeployed | **NON-BLOCKING** for correctness; **BLOCKING** for a public launch | link to Vercel + set `NEXT_PUBLIC_*` + redirect allow-list |
| B8 | Ingestion/embedding lane: 18 offline tests red | **NOT MEASURED** here (core-b workstream) | that lane must go green before its own gate |
| B9 | No incident-response subsystem (LC-011 incident events; P1 incident rehearsal) | **BLOCKING** for broad public launch (P1), not for a limited launch | build later |
| B10 | Legal documents (ToS, Privacy Policy, DPA, subprocessor list) | **BLOCKING** for public launch (spec §6, §9 P1) | counsel + product; engineering hooks exist (export/delete/audit/provider register) |

---

## RELEASE GATES

**AUTH/POLICY RELEASE GATE: PASS** (for a launch that does not host user repositories)

Update 2026-09-08 (final):

- **B1 closed** — migrations 46/47/48 applied and verified live (8 provider-policy
  rows, `procedures.tenant_id`, `publication_records`/`data_requests`/`contributor_profiles`).
- **Phase 3 formally OUT OF LAUNCH SCOPE** — this launch does not host user
  repositories. `HOSTED_EXECUTION_ENABLED=false` (the default): `repo_path` is
  local/loopback passthrough, the workspace registry sits dormant, and the
  subprocess-sandbox network-isolation gap (B3) and the un-guarded REST execution
  paths (B2) apply only to a future hosted-repo product. The one operational
  guardrail: keep the MCP server (`find_best_way` / `reproduce_procedure`)
  local/trusted, per its own documented posture.
- **B5 closed** — the DB-backed adversarial e2e suites were run against a
  **disposable local Postgres 17 + pgvector 0.8.0** (port 55432, migrations
  01→48 applied fresh, torn down after). **Result: 75 passed / 0 failed** across
  `test_cross_user_isolation_e2e`, `test_product_model_privacy_e2e`,
  `test_agent_store_idor_e2e`, `test_agents_file_download_isolation_e2e`,
  `test_publish_e2e`, `test_local_claims_publish_e2e`,
  `test_second_user_global_reuse_e2e`, `test_hardening_h1_identity_tenancy`,
  `test_hardening_h2_rls_backstop`. Plus `test_schema_drift` +
  `test_wave3_tenancy_adoption` (24 passed) and a live smoke of the new gates
  against the real schema: provider policy denies `USER_PRIVATE → gemini` /
  allows `PUBLIC_DERIVED → gemini`; `publish_procedure` produces a GLOBAL
  CANDIDATE + `publication_records` row + 3 audit events; a non-owner publish is
  denied. The only e2e failure was `test_migration_upgrade_e2e`
  (`embedding_provider` column) — the pre-existing ingestion-lane migration-order
  bug (baseline), not a security regression.

Nothing security-relevant is outstanding for a non-hosted launch. Remaining
non-blocking items: legal documents (B10, counsel), incident-response subsystem
(B9, needed before *broad* public launch — P1), `frontendv1` deployment (B7),
Google OAuth config or disable (B6), and per-call-site LLM policy wiring in
`panel.py` (B4 — embeddings + private paths are already gated).

**RETRIEVAL/PROCEDURAL-MEMORY RELEASE GATE: NOT PASS**

18 offline tests remain red, all in the retrieval/embedding/ingestion lane
(`Embedder.embedding_model_id` / `embedding_provider`, `test_migration_upgrade_e2e`).
That lane is a separate workstream and was not modified by the auth/policy work;
the auth/policy changes add zero net new failures. This gate cannot pass while
that lane is red.

**INGESTION RESUME: NOT ALLOWED**

Migrations 46/47/48 are now applied (was blocker 2). Remaining prerequisites not
proven: (1) the ingestion/embedding lane still has ~18 failing offline tests
including `test_migration_upgrade_e2e` (`embedding_provider` column) — a
migration-ordering bug in that lane's own migrations, confirmed still failing on
a fresh DB; (2) no DB-backed proof that ingested private material stays out of
global retrieval under the new org/classification model (the isolation e2e run
above covered user/procedure isolation, not an ingestion→classification→retrieval
path). Resume only after that lane is green and one e2e proof of
ingestion → classification → retrieval isolation exists.

---

## Commits (this work, on `gate-2b`, NOT pushed)

| SHA | What |
|---|---|
| `3e47906` | Phase 1 backend: `require_authenticated_user`, boot fix, fast contribution |
| `e63ba85` | Phase 1 frontend: Supabase client, sign-in, `/submit` Quick Add |
| `382d53b` `33463dc` | ledger + P-256/ES256 doc |
| `120a506` | Phase 2: `get_scope` hardening, owner-identity fix, IDOR tests |
| `7f2fbb8` | Phase 7 frontend: scope labels + publication consequences |
| `593046b` | prior FINAL-RELEASE-READINESS |
| `7d1e8fa` | JWKS EC/ES256 fix (500 on every authed request) |
| `6cea7fc` | Phase 5: classification + provider-policy egress gate |
| `7ec8c51` | Phase 4: canonical publication gate + records + withdrawal |
| `939921a` | Phase 6: export + dependency-aware deletion |
| `f18651a` | Phase 3 workspace registration + Phase 7 audit wiring |
| `a8d25dc` | Phase 2 completion: ORGANIZATION visibility |
| `4ee89d5` | Phase 7 frontend: Privacy & Data page |
| `b2265e9` | test: isolate SUPABASE_*/OIDC_* from the offline suite |
| (this file) | Phase 8 acceptance matrix + gates |
