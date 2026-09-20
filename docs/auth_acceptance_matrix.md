# Auth + authorization acceptance matrix

Status: CLOSED = implemented and covered by a passing test in this pass · PARTIAL = implemented in part, or covered offline only · OPEN = not done.
Offline suite result: `cd backend && python -m pytest tests -q` → **3351 passed, 592 skipped** (skips = DB-gated e2e; no database was available).

| Requirement | Status | Code | Test | Notes |
|---|---|---|---|---|
| Supabase remains human IdP; nothing rebuilt in Stealth | CLOSED | `services/authn.py` | `test_authn_offline`, `test_supabase_auth_dependency_offline` | pre-existing |
| Server-side JWT verification (sig/iss/aud/exp/sub, alg pinned, JWKS cache+rotation) | CLOSED | `authn.validate_token_async`, `FetchingJwks` | `test_authn_offline`, `test_auth_enforcement_offline::test_bad_tokens_are_401_never_anonymous` | HS256/none/wrong iss/aud/expired/no sub/unknown kid → 401 |
| One canonical AuthContext per request | CLOSED | `auth_context.py`, `deps.get_auth_context` | `test_valid_supabase_jwt_resolves_one_canonical_context` (1 resolution) | `AuthenticatedPrincipal` is now an alias |
| AuthN separate from AuthZ; centralized authorization | CLOSED | `authorization.py`, `deps.require_scopes` | `test_authorization_model_offline` (85) | |
| Object scope model + can_read/write/publish/execute/admin | CLOSED | `authorization.py` | truth-table + property tests | maps existing `visibility` |
| Tenant isolation in policy | CLOSED | `authorization`, `access.visibility_predicate` | matrix + `test_membership_removal_takes_effect_on_the_next_request` | |
| Tenant isolation across Goals/Claims/Procedures/… on real data | PARTIAL | existing predicates | existing `*_e2e` (skipped here) | not run: no database |
| Shard routing never an authorization input | CLOSED (structural) | `ObjectRef` has no shard field | `test_property_authorization_is_independent_of_physical_placement` | |
| Auth identical across shards / rollover / stale routing (live) | OPEN | — | — | needs sharded DB |
| Deactivated account fails closed | CLOSED | `deps.get_scope` (was fail-open) | `test_deactivated_account_is_403_on_read_and_write_paths` | security fix |
| Previously open mutating REST routes gated | CLOSED | `api/*.py` `dependencies=` | `test_anonymous_is_401_on_every_previously_open_mutating_route` (17) | |
| MCP cannot bypass authz; same verification/scopes/orgs as REST | CLOSED | `mcp_server/server.py` | 8 MCP tests in `test_auth_enforcement_offline` | every tool classified (test) |
| MCP data routes no longer `unrestricted` | CLOSED | `_route_scope`, `_route_gate` | `test_mcp_data_routes_never_use_unrestricted_scope`, `_route_gate_local_vs_remote` | |
| Service identity (ServiceAuthContext) | CLOSED | `service_identity.py` | `test_service_identity_offline` (20) | |
| Worker credentials: expire, rotate, revoke, env-bound, scope-bounded | CLOSED (offline) / PARTIAL (DB registry unexecuted) | `PgServiceRegistry`, migration 99 | offline + fake pool | migration 99 never applied to a real DB |
| Worker least privilege (REST surface) | CLOSED | scopes | `test_worker_scopes_are_least_privilege` | |
| Worker authenticates at start-up | CLOSED | `worker.authenticate_worker` | `test_worker_refuses_to_start…`, `…requires_the_ingestion_process_scope` | |
| Worker ↔ DB least-privilege roles | OPEN (operator) | runbook GRANT model | — | Neon setup is yours |
| Ingestion job authority metadata | CLOSED (offline) | migration 99, `queue.enqueue` (default authority derived from declared scope on EVERY job; publication never implied), `enqueue_skill_packages(submitted_by=)`, `job_authority_from_row` | `test_every_enqueue_carries_authority…`, `test_skill_package_enqueue_stamps…` | SQL unexecuted until migration 99 is applied |
| Search authz in candidate generation | PARTIAL | `visibility_predicate` (pre-existing) | offline predicate tests | live leakage suites not run |
| External models get only authorized data | PARTIAL | predicate-before-rank + `ProviderPolicyService` (pre-existing) | — | not re-verified here |
| Object storage authorization | CLOSED | blobs are content-addressed + deduplicated, so ACL lives on the owning job/object: `object_storage.authorized_hydrate`, used by the worker whenever it holds a service identity; no public URLs are issued | `test_blob_hydration_requires_read_authority…` | signed URLs unnecessary while no endpoint serves blobs |
| Private→global publication explicit + authorized; workers cannot publish | CLOSED | `can_publish`, existing publication flow | `test_can_publish` | |
| Admin ops explicit, no permanent universal token | CLOSED | scoped `admin:ops`/`maintenance:run`; legacy key is opt-in in PRODUCTION (`legacy_admin_key_enabled`), audited, ≥32 chars | `test_legacy_admin_key_is_opt_in_in_production`, legacy-key tests | operators must set `ADMIN_API_KEY_LEGACY_ENABLED=true` to keep using it in production |
| Break-glass elevation | CLOSED (offline) | `grant_break_glass` (auth:admin, two-person, reason ≥20 chars, TTL ≤1 h, audited) → `tenancy:cross` while unexpired; every use audited; never opens SYSTEM_INTERNAL | `test_break_glass_*` | table in migration 99 |
| Audit logging | CLOSED | `record_security_event`; service register/issue/revoke/disable and break-glass grant/revoke are fail-closed audit rows | denial, legacy-key, operator-action tests | shard-admin CLI actions are not audited (shard registration lives outside this pass) |
| Rate limits keyed on verified identity | CLOSED | `scope_key_for` + `scale_limit_for_class` (anonymous ½×, user 1×, service 5×) | `test_rate_limits_scale_by_verified_caller_class` | |
| CORS/CSRF | CLOSED | `main.py` | — | bearer, no credentials; PUT/DELETE added |
| Secrets hygiene / no service-role key | CLOSED | — | `test_no_secrets_in_tracked_source`, `…service_role…`, `…frontends_never_receive_database_credentials` | |
| Env separation + prod boot refusals | CLOSED | `runtime_guard._auth_posture_problems` | ~40 tests in `test_auth_hardening_offline` | includes dead-code fix in `assert_production_config` |
| No dev auth bypass in prod | CLOSED | `deps.get_scope` gate | `test_unenforced_posture_exists_only_in_test_without_identity`, `test_no_debug_admin_headers…` | |
| Supabase-specific DB assumptions removed | CLOSED | none existed | `test_migrations_do_not_depend_on_supabase_auth_schema` | RLS is GUC-based |
| Neon TLS | CLOSED (guard) | runtime_guard | `test_database_tls_required_for_remote_hosts` | |
| Red-team: forged JWT, alg confusion, wrong iss, escalation, header spoof, service reuse/crossover, env crossover | CLOSED (offline) | — | see enforcement + service tests | |
| Red-team: SQL injection, IDOR, token replay across services | CLOSED (offline) | bound parameters; `hide_existence`; jti bound to service | `test_hostile_identity_strings…`, `test_idor_probe…`, `test_replayed_credential…` | live stale-role cache and object-URL guessing need a running stack |
| Docs | CLOSED | `docs/auth_*.md`, `service_identity.md`, `security_runbook.md` | — | |
