# P1 results: isolation, SLA, health checks

This is the evidence file for [securityp1.md](../securityp1.md). Every number states its source and date.
Nothing here touched a shared database unless a row says so, with the owner's approval quoted.

## Step 0: what already existed (2026-10-07)

- **Searched:**
  - `origin/main` history (`docs/*isolation*`, `*health*`, `*sla*`, `final_prod_docs/*`);
  - every local worktree (`sl-core-a`, `sl-core-b`, `sl-measure`, `sl-ship`, `sl-research`, `sl-evaluation`,
    `stealth-lab`, `stealth-lab-demo`) for files named `*isolation*`, `*probe*`, `*uptime*`, `*latency*`, `*load*`,
    `*p1_results*`, `*healthz*`, `*readyz*`.
- **Found:** only older code and tests already on `main`: `environment_probe.py`, the `*_isolation_e2e.py` suites, and
  `.scratch/perf_probe.py` (2026-09-03). There are **no** prior isolation run logs, no latency or uptime probe data,
  and no load-test output. Every side worktree is 0 commits ahead of `main`, so they hold nothing unique.
- **Asked the owner (2026-10-07)** whether the other person's results exist outside the repo. There has been no answer
  yet, so every number below is measured here.

## Production migrations (2026-10-07, owner-approved)

The owner gave explicit approval in the session: "i give you the permission to apply those migrations".

| Database | Before | Applied | After |
|---|---|---|---|
| Control (Neon) | 8 pending | `134_way_reports`, `135_route_aware_procedure_refs`, `136_org_governance`, `137_ledger_cost_components`, `138_ledger_timing_and_tier`, `139_org_denials_and_user_usage`, `146_benchmarks_repo_index`, `147_routing_priors` | 0 pending |
| Search members S001–S004 (Neon) | the same 8 pending on each | the same 8 (control set, as `scripts/provision_neon_shards.py` migrates members) | 0 pending, exit 0 each |
| Knowledge shards K001–K084 | the same 8 pending (K001 checked read-only) | **not applied**, waiting for the owner's decision | unchanged |

- `148_routing_priors_search.sql` (`--target search`) does not apply here: there is no single `SEARCH_DATABASE_URL`.
  The search members receive the control set, which already carries 147's columns.
- Only 135 drops anything: four foreign keys, replaced by route-aware triggers. No data changed.

## Test environment

- **Database:** a throwaway PostgreSQL 18.4 cluster on `127.0.0.1:55432` only, created with `initdb` at
  `C:\Users\user\pg-scratch`, plus pgvector; database `kel_v2_isolation`.
- **Migrations:** all 141 applied with `scripts/migrate.py --dsn <loopback>`, 0 pending.
- **Isolation from production:** every database variable named in `backend/.env` and `backend/.neon_shards.env` is
  overridden with the loopback DSN for live runs (`scripts/live_shutdown_check.py` does this itself). The server loads
  `.neon_shards.env` on its own, which is why the override is needed.
- **No Docker** on this machine, so there are no Linux-container runs. Signal tests ran on Windows (see P1-C).

## P1-C: health checks and graceful shutdown

| Item | Result | Evidence |
|---|---|---|
| `/healthz` liveness | done | `app/mcp_server/health.py`, `server.py` route; offline tests |
| `/readyz` readiness: pool, `SELECT 1` within 2 s, newest control migration applied, not draining; 503 names the check, no secrets | done | `tests/test_mcp_health_offline.py`: **12 passed** (2026-10-07). This includes a fake database error carrying a DSN whose text never reaches the body, and the real ASGI app answering without credentials or a database |
| Providers not in readiness | done | body says `"providers": "not_checked"`; `find_ways` degrades to lexical search |
| Probes point correctly | done, **not deployed** | Docker `HEALTHCHECK` targets `/healthz` (`scripts/docker_healthcheck.py`); Railway `healthcheckPath` targets `/readyz` (`railway.mcp.json`) |
| Drain on stop | done | `install_drain_on_signals` chains SIGTERM, SIGINT and SIGBREAK in front of uvicorn's handler; container runs `--timeout-graceful-shutdown 25` |
| Pool close and Sentry/OTel flush on shutdown | done | lifespan `finally`: `pool.close()`, `flush_observability()` |
| **Live shutdown test** | **passed** (Windows, CTRL_BREAK_EVENT) | `scripts/live_shutdown_check.py` on the scratch DB, 2026-10-07 22:47 IST. Details below the table |
| Stale `reserved` ledger rows | done | `org_governance.settle_abandoned[_all]`, plus `admin ledger-settle-abandoned [--apply]`; `tests/test_ledger_abandoned_offline.py`: **3 passed** |
| Migrations stop the container on failure | by design (unchanged) | `sh -c "python scripts/migrate.py && exec uvicorn ..."` |

**The live shutdown test, in detail:**
- `/readyz` returned 200 before the stop.
- A `/readyz` request held on a table lock was in flight when the stop signal arrived. It completed with 200 after 5.0 s.
- The log shows `readiness: draining (signal 21)`, then uvicorn's `Waiting for connections to close`,
  `Application shutdown complete` and `Finished server process`.
- The process exited 4.1 s after the signal.

**Exit code: 3, not 0.** Uvicorn 0.52.1 re-raises the captured stop signal after a clean shutdown
(`uvicorn/server.py`, `capture_signals`), so the status reports the signal: 3 after CTRL_BREAK on Windows, 143 after
SIGTERM on Linux. securityp1.md asked for "exit 0". That is not achievable without overriding uvicorn's signal
handling, and the check therefore passes on 0 or on the re-raised signal **only when** the shutdown log is complete.
**Not run:** SIGTERM in a Linux container (no Docker here). That should run once on Linux before relying on it.

**Abandoned reservations are settled at their worst case, not marked failed.** Whether the provider charged for an
abandoned call is unknown. Marking it `failed` would release the hold and record no cost. Settling it at
`reserved_usd` (`cost_source = 'upper_bound'`, the ledger's existing meaning of "no usage came back") means budgets
never under-count. The cutoff is 30 minutes (provider timeout 120 s, drain 25 s), and anything under 15 minutes is
refused.

## P1-A: multi-tenant isolation

### Suite runs (2026-10-07, throwaway DB `kel_v2_isolation` on 127.0.0.1:55432; `scripts/run_isolation_suites.py`)

`owner` is `kel_owner` (the cluster superuser that owns the tables). `stealth_app` was created with
`scripts/sql/create_app_role.sql`: not superuser, not `BYPASSRLS`, no DDL.

| File | owner: pass / fail / skip | stealth_app: pass / fail / skip |
|---|---|---|
| `test_mcp_tenant_isolation_e2e.py` | 3 / 0 / 0 | 3 / 0 / 0 |
| `test_cross_user_isolation_e2e.py` | 3 / 0 / 0 | 3 / 0 / 0 |
| `test_retrieval_fixture_isolation_e2e.py` | 2 / 0 / 0 | 2 / 0 / 0 |
| `test_agents_file_download_isolation_e2e.py` | 1 / 0 / 0 | 1 / 0 / 0 |
| `test_ingestion_episode_extraction_privacy_e2e.py` | 1 / 0 / 0 | 1 / 0 / 0 |
| `test_product_model_privacy_e2e.py` | 2 / 0 / 0 | 2 / 0 / 0 |
| `test_hardening_h2_rls_backstop.py` | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_access.py` | 9 / 0 / 0 | 9 / 0 / 0 |
| `test_routing_isolation_e2e.py` | 3 / 0 / 0 | 3 / 0 / 0 |
| `test_org_governance_e2e.py` | 32 / 0 / 0 | 26 / **6** / 0 (see note) |
| `evaluation/privacy/` | 2 / 0 / 0 | 2 / 0 / 0 |
| **new** `test_tenant_isolation_p1a_e2e.py` | 7 / 0 / **1** (see note) | 8 / 0 / 0 |
| `test_service_identity_offline.py` (offline, item 8) | 20 / 0 / 0 | n/a |

**Notes on the counts:**
- **The 6 `test_org_governance_e2e.py` failures as `stealth_app` are not leaks.** Those tests are written to run as
  the owner and then switch into their own restricted role (`SET ROLE sl_gov_app`) to exercise row-level security,
  and one tamper test needs owner rights on `audit_events`. `stealth_app` may do neither, so they fail in setup with
  `permission denied to set role` / `must be owner`. As the owner all 32 pass, including their own restricted-role
  RLS checks.
- **The one owner skip in the new file is deliberate.** The RLS test refuses to run as a role that bypasses RLS,
  because it could prove nothing there. As `stealth_app` it runs and passes.

**Before the fixes, three suites never ran at all, and one was stale:**
- `test_mcp_tenant_isolation_e2e.py` and `test_routing_isolation_e2e.py` set `STEALTHLAB_MCP_TOKEN=test-token`
  before importing the server. The server refuses to import when that differs from the token `backend/.env`
  declares, so on any checkout with a `.env` they errored in setup (3 + 3 errors). The fixture now uses the
  declared token.
- `test_retrieval_fixture_isolation_e2e.py` called Vertex for embeddings, which this environment cannot reach
  (403), and it must not call a provider anyway. It now uses a deterministic vector, with the query at the
  fixture's own vector (the strongest possible match). A new control case shows the same procedure, *unflagged*,
  is returned, so the absence is the predicate working.
- `evaluation/privacy`: the stale test (item 7) is fixed.

**Mutation check (2026-10-07).** With the old fail-open `instance_key` guard put back in `plan.load_instance`,
`test_unknown_foreign_and_ownerless_instance_keys_answer_identically` failed as `stealth_app`, and so did the offline
`test_an_instance_issued_to_nobody_stays_with_unidentified_callers_only`. With the fix restored, both pass
(`.scratch` script, file restored and verified).

### The ten gaps of securityp1.md §5.1

| # | Gap | Outcome |
|---|---|---|
| 1 | `instance_key` binding | **Confirmed and fixed.** The cross-user binding held (the routing suite passes), but it **failed open** for an instance with no recorded issuer: any signed-in user could take over a key issued before the binding or to an unidentified caller. Now an ownerless instance stays with unidentified callers only (the single-user server's posture; in shared mode they cannot write). Unknown, foreign and ownerless keys answer the same text (new live test). Commit: core-a P1-A |
| 2 | Double-encoded routing JSON | **Confirmed and fixed.** No SQL reads these columns with `->>` today (the one place parses in Python), so nothing failed open yet. But the writers stored JSON strings, and the planted-row test shows `meta->>'_caller'` returned NULL before and the value after. The writers now pass objects (`store._jsonable`). Migration `149_unwrap_double_encoded_routing_json.sql` unwraps old rows (idempotent, verified on the scratch DB). **Not applied to production yet**, pending the owner's decision |
| 3 | Migration 29 fallback allows unset tenant | **Confirmed and deferred.** 5 tables (`change_set_operations`, `change_sets`, `evidence`, `executions`, `failure_routes`) use `sl_tenant_scope_allows`, which allows when `app.tenant_id` is unset; the 5 migration-136 tables use the strict `sl_org_strict`. Switching needs an audit of every path to `tenant_transaction()` first. Pinned by a new test, recorded in the runbook's Residual risks #8 |
| 4 | Routing tables without RLS | **Confirmed and deferred.** RLS is off on all 10 `routing_*` tables (`pg_class.relrowsecurity = false`, 0 policies). The global tables need none. `routing_decisions` / `routing_observations` carry `visibility` / `owner_id` and rely on application checks. Plan in Residual risks #9 |
| 5 | One shared DB credential | **Confirmed. Fixed on the scratch DB, deferred on Neon.** `scripts/sql/create_app_role.sql` creates `stealth_app` (all privileged flags false, verified), and the suites run as it. Not run on any shared database |
| 6 | Anonymous read path | **Not reproducible as a leak.** The anonymous token carries only `stealthlab:tools` + `retrieval:read`. New offline tests: every write/execute v1 tool is refused, an unclassified tool defaults to refused, a blank `Bearer` is treated as no header, and anonymous scope is public-only. The existing suites cover reads of private Goals |
| 7 | Stale privacy test | **Fixed.** `find_goal` now returns `(page, has_more)`. The test unpacks it, and its assertion is unchanged |
| 8 | API key / service token | **Not reproducible.** Service tokens: `test_service_identity_offline.py` 20 passed (wrong issuer/audience, forged, expired, revoked, scope escalation refused). Legacy admin key: on outside PRODUCTION unless `ADMIN_API_KEY_LEGACY_ENABLED` is set. **Owner action:** set it to `false` on the hosted service if that service runs as STAGING (Residual risks #10) |
| 9 | `call_model` per-user limits | **Not reproducible.** `governing_org` never takes the paying org from the caller unchecked: another org is refused, a non-member is refused, no org context fails closed, and several orgs mean the caller must choose (new offline test). Budgets, per-user daily limits, the kill switch and per-org RLS on the ledger: `test_org_governance_e2e.py` 32 passed (owner) |
| 10 | Timing / error oracles | Error text: **fixed and tested** (see #1). Private-goal existence through `find_ways`: covered by `test_mcp_tenant_isolation_e2e.py` (passes). **Timing was not measured** |

### Scenarios of securityp1.md §5.3

| # | Scenario | Test |
|---|---|---|
| 1 | Private Goal / Procedure invisible to B in `find_ways` | `test_mcp_tenant_isolation_e2e.py` (existing, now runs) |
| 2 | A's `instance_key` refused for B, same error as unknown | `test_routing_isolation_e2e.py` + new `test_unknown_foreign_and_ownerless_instance_keys_answer_identically` |
| 3 | Org X / Y / non-member on org-visible rows | `test_routing_isolation_e2e.py::test_org_visibility_reaches_only_members_of_that_org`; org RLS in `test_org_governance_e2e.py` |
| 4 | Anonymous: reads public only, every write refused | new `test_an_anonymous_caller_may_read_but_every_write_and_execute_tool_is_refused`, `test_a_blank_bearer...`, `test_the_anonymous_scope_is_public_only` |
| 5 | Same checks as `stealth_app` | the stealth_app column above |
| 6 | Forgotten `tenant_transaction` on FORCE RLS | new `test_without_a_tenant_setting_strict_tables_see_nothing_and_fallback_tables_are_the_known_gap` (stealth_app) |
| 7 | Pooled connection reused after tenant A | new `test_a_pooled_connection_carries_no_tenant_after_a_tenant_transaction` |
| 8 | A spends against org Y's budget | new `test_the_billed_organisation_is_never_taken_from_the_caller_unchecked`; budgets in `test_org_governance_e2e.py` |
| 9 | Service token with wrong audience / scope | `test_service_identity_offline.py` (20 passed) |
| 10 | Withdrawn private content returned to nobody | new `test_a_withdrawn_procedure_is_returned_to_nobody` (with a "reachable while live" control) |

## P1-B: SLA

Not started. It needs the owner's decisions (support tiers, on-call) and measurements.
