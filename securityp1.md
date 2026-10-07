# securityp1.md — P1 security and reliability work (handoff)

**For:** the Claude session that will do this work. **Written:** 2026-10-07 by the "legal" session. **Read all of it before touching code.**

Three items from the enterprise-onboarding checklist are handed to you. Another person's session already tested some of this, so **start by finding their results** (Section 3) instead of redoing them.

| Item | Name | Your task |
|---|---|---|
| P1-A | Multi-tenant data isolation | Prove it on a real database, close the gaps in Section 5, add the missing tests |
| P1-B | SLA definition | Turn the draft SLA into numbers backed by measurements |
| P1-C | Health checks and graceful shutdown | Add readiness, make shutdown drain, test it |

Monitoring and alerting (P1-D) is listed at the end as optional. It depends on P1-C.

---

## 1. Ground rules (breaking one invalidates the work)

1. **Never print, paste or log a connection string, key or token.** Host names only. Keys live in `backend/.env` (gitignored). `backend/.env.bak.*` has live keys: never read it into output, never stage it.
2. **No writes to production, no deploys, no migrations against a shared database** without the user's explicit go-ahead in this conversation. Use a **throwaway local Postgres** (`pgvector/pgvector:pg15`, a scratch database such as `kel_v2_isolation`) with loopback DSNs. Override **every** database env var (`DATABASE_URL`, `SEARCH_DATABASE_URL`, the `K###_`/`S###_DATABASE_URL` shard variables, `TEST_DATABASE_URL`) with loopback values, or a test will reach a real database through a shard variable.
3. **Never push** `FundingGrants/`, `.claude/traces/`, `backend/.env`, any `.env`, `*.mov`, or `backend/.neon_shards.env` / any `*neon_shards*.env`.
4. **Commit only paths you changed.** Other sessions have uncommitted work in this tree. Use `git add <your paths>` and `git commit -- <your paths>`; never `git add -A` or `git commit -a`.
5. **Rebase onto `origin/main` before pushing. Never force-push.** Commit prefix `ship:` for deployable changes, `core-a:` for isolation work, `board:` for docs/board entries. Paste the pytest counts into the commit message.
6. **Follow `CLAUDE.md` in the repo root.** In particular: tenant/visibility SQL only through `scope_predicates()` (`backend/app/services/access.py`), writes through `tenant_transaction(...)`, migrations are immutable once applied (add a new file with the next free number), offline tests hand-roll their own `FakePool`/`FakeConn`.
7. **Do not change the frozen spec files** (`schema.md`, `verified_procedural_experience_system_ideal_specification_v4.md`). A discrepancy becomes a board note.
8. **Do not claim what you did not run.** If you could not run a live test, say so in the commit message and the results file.
9. **Do not weaken a test to make it pass.** If a test is wrong, say why, in writing, and fix the test's premise.
10. **No vendor marks in names** (no "Claude", "Codex", "GPT" in product-facing names).

---

## 2. The system in one page

A hosted MCP server (`uvicorn app.mcp_server.server:app`, port 8765, **`--workers 1` is load-bearing**: `TasksExtension` keeps task state in memory, and the `find_ways` governor state is per process).

Seven tools in `backend/app/mcp_server/server.py`: `find_ways`, `recommend_models`, `report_model_run`, `report_result`, `call_model`, `report_discovery`, `submit_way`.

| Piece | Where |
|---|---|
| Auth: Supabase OAuth 2.1, OIDC verifier, shared token only in `DEPLOYMENT_MODE=single_user` | `backend/app/mcp_server/oauth_resource.py`, `server.py` (`OidcAwareTokenVerifier`) |
| **Anonymous read path** (2026-09-23): a middleware injects a read-only token when no `Authorization` header is sent | `backend/app/mcp_server/anonymous_read.py` |
| Tenant and visibility SQL | `backend/app/services/access.py` (`scope_predicates()`, `tenant_transaction()`) |
| Row-level security backstop (migration 29), binds `app.tenant_id` per transaction | `backend/db/29_rls_backstop.sql` |
| Org governance, `FORCE ROW LEVEL SECURITY` on its tables, budgets, kill switch, `provider_call_ledger` | `backend/db/136_org_governance.sql` |
| Routing tables (`routing_decisions`, `routing_observations`, `routing_posteriors`), carry `visibility` and `owner_id` | `backend/db/120_model_routing.sql`, `121_model_routing_search.sql`, `backend/app/routing/store.py` |
| Provider calls (`call_model`), BYOK and platform modes, SSRF guard, ledger | `backend/app/providers/` |
| Health route `GET /` returns `{"service":"stealthlab-mcp","status":"ok","mcp_endpoint":"/mcp"}` (static, unauthenticated) | `server.py` `root_health` (~line 790) |
| Docker HEALTHCHECK runs `scripts/docker_healthcheck.py` (GET `/` on `$PORT`) | `backend/Dockerfile.mcp-server`, `backend/scripts/docker_healthcheck.py` |
| Railway: 1 replica, `healthcheckPath: "/"`, restart on failure, 5 retries | `railway.mcp.json` |
| Container command: `python scripts/migrate.py && exec uvicorn ... --workers 1 --proxy-headers` | `backend/Dockerfile.mcp-server` |
| Observability: Sentry and OpenTelemetry, both **off unless configured** | `backend/app/observability.py` |
| Ops alerts for ingestion shards only (cron every 10 minutes) | `.github/workflows/ops-alerts.yml` |
| Security runbook, role plan, incident steps | `docs/security_runbook.md` |
| Draft SLA, failure behaviour, claims register | `final_prod_docs/04_service_levels_and_failure_behavior.md`, `final_prod_docs/customer/sla_policy.md`, `final_prod_docs/06_claims_register.md`, `legal/b2b/msa.md` Exhibit A |

---

## 3. Step 0 — find what the other session already has (do this first)

The user said another session tested this and may hold data. Before you write anything:

1. `git fetch origin && git log origin/main --oneline -30` and `git log --all --oneline -- 'docs/*isolation*' 'docs/*health*' 'docs/*sla*'`.
2. Look for results the user or the other person can hand you: probe logs, a latency file, load-test output, isolation test runs, a status or uptime record. Check `.scratch/`, `docs/`, `experiments/`, and any file whose name contains `isolation`, `probe`, `load`, `uptime`, `health`, `latency`.
3. **Ask the user** (one short message) for anything not in the repo: live test output, probe data, the environment it ran in. Do not guess numbers.
4. Record what you found and what you did not find at the top of `final_prod_docs/p1_results.md` (create it). Every later number in that file states its source and date.

---

## 4. Setting up a safe test environment

```text
1. Start a throwaway Postgres 15 with pgvector on loopback (port other than a shared one).
2. Create a scratch database (e.g. kel_v2_isolation).
3. From backend/, run migrations against it only:
     DATABASE_URL=<loopback scratch DSN>  python scripts/migrate.py
4. For every test run set:
     STEALTHLAB_ENV=TEST
     TEST_DATABASE_URL=<loopback scratch DSN>
     and UNSET or override SEARCH_DATABASE_URL and every K###/S###_DATABASE_URL.
5. Offline suite = run with DATABASE_URL unset (the *_e2e.py files skip themselves).
```

Notes learned the hard way:
- `backend/.env` has `DEPLOYMENT_MODE=shared` and `STEALTHLAB_ENV=STAGING`. Four offline tests depend on that and pass with `STEALTHLAB_ENV=TEST` (56 passed). Set it explicitly.
- Running the local MCP server: use `DEPLOYMENT_MODE=single_user`. Boot fails with a RuntimeError if `STEALTHLAB_MCP_TOKEN` in the environment differs from the one in `.env`; read the token into a variable without printing it.
- Vertex ADC currently returns `access_denied: Account Restricted`, so embeddings fail and `find_ways` falls back to lexical search. This is a known environment issue, not a bug in your change.
- A Postgres on the author's machine listens on `0.0.0.0:5432`. Bind your scratch instance to loopback only.
- Two shell commands (`netstat`, `find`) hung before. Prefer the dedicated search tools or PowerShell for those.

Baseline to beat (author's last run, before this handoff): backend offline suite 765 passed / 11 failed (7 fail on a clean HEAD; 4 depend on the local `.env`). Live isolation on the scratch DB: 10 passed / 1 failed (the stale test in Section 5, item 7).

---

## 5. P1-A — Multi-tenant data isolation

**Goal:** a signed-in user, an organisation member, an anonymous caller, and a service identity can each read and write **only** what the rules allow, on a real database, through every public tool and every HTTP route. Evidence is a test run with counts, not a code reading.

### 5.1 Known gaps to close or disprove (verify each; do not trust this list)

1. **`instance_key` binding.** The fix (a handle works only for the caller who received it: `plan.py` `CALLER_KEY` and `store.instance_issuer`, which parses the double-encoded JSON in Python) is on `origin/main` as of 2026-10-07 (checked: both symbols exist there). **Verify it**, do not trust it: run `backend/tests/test_routing_isolation_e2e.py` on the scratch DB as `stealth_app`, confirm user B cannot call `report_result`/`report_model_run` with user A's `instance_key`, and confirm the same error text as for an unknown key. Also check the fail-open case: an `instance_key` with no stored `_caller` (decisions written before the fix, or by another writer) must be refused or explicitly handled, not accepted.
2. **Routing JSON is double-encoded.** The pool's jsonb codec plus `json.dumps` store a JSON string inside jsonb. SQL `->>` then returns NULL and any SQL-side check **fails open**. Readers must unwrap through `store._json_value` (up to two layers). Find every SQL expression that reads `routing_decisions.constraints`, `candidates`, `ladder`, `predicted` with `->>` or `->`, and every writer that still double-encodes. Either fix the writers (a new migration or codec change, with a test) or make every reader unwrap. Do not leave a mix.
3. **Migration 29's backstop allows when `app.tenant_id` is unset.** `sl_tenant_scope_allows()` falls back to allow (see the comment at the top of `backend/db/136_org_governance.sql`). A code path that forgets `tenant_transaction()` is therefore not blocked by row-level security. Migration 136's newer helper requires the setting. List every table and decide: should the 29-style fallback stay? Write the answer as a board note; do not edit migration 29 (migrations are immutable), add a new migration if a change is justified.
4. **Do the routing tables have row-level security at all?** `120_model_routing.sql` and `121_model_routing_search.sql` carry `visibility` and `owner_id` columns, but check whether `ENABLE`/`FORCE ROW LEVEL SECURITY` and policies exist for them (and for `routing_posteriors`, which is keyed by goal/procedure, not tenant). If not, application checks are the only layer. Report the facts first.
5. **One shared database credential.** `docs/security_runbook.md` lists the planned roles (`stealth_app`, `stealth_worker`, `stealth_migrate`, `stealth_readonly`, `stealth_maint`) as "documented, not created". `FORCE RLS` does nothing if the connecting role owns the table or has `BYPASSRLS`. On the scratch database, **create the `stealth_app` role as documented, run the isolation suites as that role, and prove the suites fail as the table owner** (so you know the tests can detect a leak). Ship the role-creation SQL as a script or doc; do not run it against a shared database.
6. **Anonymous read path.** `anonymous_read.py` lets a caller with no `Authorization` header reach the server as a read-only caller. Test that an anonymous caller: sees only public rows; cannot call any write tool (`report_result`, `report_model_run`, `submit_way`, `report_discovery`, `call_model`); cannot use another user's `instance_key`; and that a blank `Bearer` header is treated like no header. Check `_enforce_tool_scope`.
7. **Stale test.** `backend/tests/evaluation/privacy/test_cross_user_privacy_e2e.py:247` (`test_published_solution_discoverable_by_b_and_bs_execution_is_independent_evidence`) fails with `list indices must be integers`. It is a pre-existing stale test (the response shape changed). Fix the test's expectations to the current shape; do not delete the case.
8. **API-key / service-token path.** The checklist item says "Tenant A's API key cannot query Tenant B". Find every non-OAuth credential type (shared operator token, service identities in migration 99, any admin key) and test each: scoped to its own tenant or refused. `ADMIN_API_KEY_LEGACY_ENABLED` should be false in production.
9. **`call_model` per-user limits.** Migration 136 adds org budgets, per-user daily budgets and a kill switch, and a ledger. Test that user A cannot spend from org B's budget, cannot read org B's ledger rows, and that a missing org context fails closed.
10. **Cross-tenant timing and error oracles.** An invalid `instance_key` and another user's `instance_key` must return the same error text. Check `find_ways` for private-goal existence leaks (a private goal's id should look the same as a nonexistent one).

### 5.2 Existing tests to run first (all `*_e2e.py` need `TEST_DATABASE_URL`)

`backend/tests/test_mcp_tenant_isolation_e2e.py` · `test_cross_user_isolation_e2e.py` · `test_retrieval_fixture_isolation_e2e.py` · `test_agents_file_download_isolation_e2e.py` · `test_ingestion_episode_extraction_privacy_e2e.py` · `test_product_model_privacy_e2e.py` · `test_hardening_h2_rls_backstop.py` · `test_access.py` · `test_routing_isolation_e2e.py` (if present on main) · `backend/tests/evaluation/privacy/`.

Run each as the table-owner role **and** as `stealth_app`. Record pass/fail/skip counts per file in `final_prod_docs/p1_results.md`. A skip is not a pass.

### 5.3 Tests to add (each with the scenario and the expected result)

| # | Scenario | Expected |
|---|---|---|
| 1 | A private goal/procedure owned by A; B calls `find_ways` with matching text | Not returned, no hint it exists |
| 2 | A's `instance_key`; B calls `report_result` and `report_model_run` with it | Refused with the same error as an unknown key |
| 3 | Org X member vs org Y member vs non-member on org-visible rows | Only X members see X rows |
| 4 | Anonymous caller, all seven tools | Reads public only; every write refused |
| 5 | Same checks with the DB role `stealth_app`, not the owner | Same results |
| 6 | A transaction that forgets `tenant_transaction` (simulate) on a table with FORCE RLS | Blocked (or documented as allowed under the 29 fallback) |
| 7 | A pooled connection reused after tenant A's transaction | `app.tenant_id` is empty for the next borrower |
| 8 | Org budgets: A spends against org Y's `organization_id` | Refused; ledger row not created |
| 9 | Service-identity token with the wrong audience/scope | Refused |
| 10 | Deleted/withdrawn private content | Not returned to anyone, including the old owner's other devices only if permitted |

### 5.4 Acceptance for P1-A (numbers, not narrative)

- [ ] Every `*_e2e.py` isolation file listed in 5.2 **ran** (not skipped) on the scratch DB as `stealth_app`; counts recorded.
- [ ] Each of the ten gaps in 5.1 has one line: *confirmed and fixed (commit)*, *confirmed and deferred (board note and reason)*, or *not reproducible (how you checked)*.
- [ ] The ten new tests in 5.3 exist and pass as `stealth_app`, and the cross-tenant ones **fail** when the guard is removed (show one mutation check).
- [ ] The stale test is fixed.
- [ ] `docs/security_runbook.md` "Residual risks" list is updated with the real state.
- [ ] No test was run against a shared database.

---

## 6. P1-B — SLA definition

**Goal:** an SLA the company can sign. Today the draft is `final_prod_docs/customer/sla_policy.md` and `legal/b2b/msa.md` Exhibit A. It is a draft with blanks, and it must not promise what one process on one host cannot do.

### 6.1 What exists and what it assumes

- Target in the draft: **99.9% monthly availability from the Go-Live Date**, external probes every 60 s from at least two locations, credits 5% / 15% / 30% below 99.9 / 99.0 / 95.0.
- Draft response times: Sev 1 within 1 hour, Sev 2 within 4 hours, Sev 3 within 1 business day. The checklist the user was given proposes P1 15 minutes, P2 2 hours, P3 8 hours, P4 24 hours. **Do not adopt the 15-minute figure unless a staffed on-call exists.** Decide with the user.
- Allowed downtime: 99.9% ≈ 43.8 minutes per 30-day month. A single instance that restarts on each deploy (migration runs first in the container command) can consume most of that budget in one deploy.

### 6.2 Tasks

1. **Collect real data** (Section 3). If none exists, define the measurement and start collecting: an external probe (any free/low-cost uptime monitor) on `/` and, once P1-C is done, on `/readyz`; plus an **authenticated synthetic tool call** (for example `find_ways` with a fixed query) every 5 minutes recording status and latency. Write results to `final_prod_docs/p1_results.md` with timestamps.
2. **Measure latency per tool** from the probe or a local run: p50/p95/p99 for `find_ways` (default and `detail="summary"`), `recommend_models`, `report_result`, and `call_model` overhead over a stub provider. Report the number of samples. `find_ways` makes an embedding call plus several judge calls (goal judge batch, hierarchy judge batch, procedure judge per tree; repo-facts selector if `repo_claims`). Profile the stages (a timing wrapper is enough) and name the slowest stage.
3. **Load test** at the contracted size. The business target is about 1,000 users. Estimate concurrent requests (state your assumption), then run a ramp against a local or staging instance with a stub provider: report throughput, error rate and p95 at 1×, 2× and 5× that load, and the point where it breaks (database pool, event loop, per-process governor). Do not load-test a shared database or a paid model provider.
4. **Decide the budget.** From the data, state what availability one instance achieves and what two instances would. List what blocks 99.9%: in-memory `TasksExtension`, per-process `find_ways` governor, migrations at container start, single replica (`numReplicas: 1`), and the non-pooled `DATABASE_URL`.
5. **Rewrite the SLA numbers** in `final_prod_docs/customer/sla_policy.md` and Exhibit A of `legal/b2b/msa.md`: replace each `[●]` with a measured number or a deliberate decision, with the source. Keep these rules: no credit-bearing latency commitment unless measured over 30 days; exclusions listed; credits are the sole remedy; the policy applies only from the Go-Live Date.
6. **Define support tiers** with the user: severities, first-response times, hours, channel, and who is on call. Match them to reality (one founder cannot give 24×7 15-minute response).
7. **Recovery numbers.** Propose RPO and RTO from what Neon point-in-time restore provides (check the plan's window in Neon's own documentation; do not guess), and **run one restore test on the scratch database** (dump, drop, restore, verify row counts). Record the time taken.
8. **Update `final_prod_docs/06_claims_register.md`** rows for uptime, latency and failover with what you can now say.

### 6.3 Acceptance for P1-B

- [ ] Every blank in the SLA policy and Exhibit A is a number or an explicit decision with a source and a date.
- [ ] A latency table (p50/p95/p99 and sample counts) for each tool exists in `p1_results.md`.
- [ ] A load-test result with the breaking point is recorded.
- [ ] A "what blocks 99.9%" list with owners exists, and the SLA header says it is not offerable until those are done.
- [ ] One restore test result (time and verification) is recorded.
- [ ] The text does not promise anything the data does not support. If the data say 99.5%, the SLA says 99.5% or says "not yet offered".

---

## 7. P1-C — Health checks and graceful shutdown

**Today:** `GET /` returns a static `ok` with no dependency check. Docker's `HEALTHCHECK` and Railway both use it, so a server whose database is down still reports healthy. There is no readiness endpoint. I found no explicit SIGTERM/SIGINT handling in `backend/app/mcp_server/`; uvicorn's default handles SIGTERM and runs the `lifespan` shutdown, but no test shows in-flight requests complete or the pool closes cleanly. **Verify, do not assume.**

### 7.1 Tasks

1. **Liveness** `GET /healthz`: always 200 if the event loop answers. No dependency checks. Keep `GET /` unchanged (the current Docker healthcheck depends on it).
2. **Readiness** `GET /readyz`: 200 only if: the connection pool exists; `SELECT 1` succeeds within a short timeout (for example 2 s) on the primary database; migrations are at the expected version; and, if sharding is configured, the control database answers. Return 503 with a small JSON listing which check failed (no connection strings, no stack traces). Unauthenticated, rate-limited, minimal. Do not call model providers from it.
3. **Do not make readiness depend on Vertex/embedding providers.** `find_ways` already degrades to lexical search; report the provider state as `degraded` in the JSON but keep the status 200.
4. **Point the probes correctly:** Docker `HEALTHCHECK` → `/healthz` (liveness, so a database blip does not restart the container); Railway/host health check → `/readyz` (so traffic is withheld while not ready). Update `scripts/docker_healthcheck.py`, `railway.mcp.json` and `docs/deploy/hosted-mcp.md` accordingly. **Do not deploy.** Note the change for the user.
5. **Graceful shutdown:** on SIGTERM, stop accepting new requests, flip `/readyz` to 503 immediately, let in-flight requests finish up to a timeout (use uvicorn's `--timeout-graceful-shutdown`, set in the container command), then close the pool and flush Sentry (`sentry_sdk.flush`) and OpenTelemetry. Make sure a write tool call in flight (for example `report_result` or a `call_model` that has reserved budget) either completes or leaves the ledger row in a recoverable state: check `provider_call_ledger` `reserved` rows that never settle, and add a janitor query or test for stale `reserved` rows if none exists.
6. **Startup order:** the container runs `python scripts/migrate.py && exec uvicorn ...`. Confirm a failed migration stops the container (it does by design) and that `/readyz` is 503 until migrations finish if you move migrations out.
7. **Tests (offline, no database):** `/healthz` returns 200; `/readyz` returns 503 when the pool is missing or `SELECT 1` raises (use a `FakePool`); the 503 body contains no connection string; `/readyz` flips to 503 once shutdown has begun. **Live test (scratch DB):** start the server, send a slow request, send SIGTERM, and show the request completes and the process exits 0 within the timeout. On Windows, signals differ; if you cannot send SIGTERM, run this test in a Linux container and say so.
8. **Document** the endpoints in `backend/README_MCP_SERVER.md` and the runbook.

### 7.2 Acceptance for P1-C

- [ ] `/healthz` and `/readyz` exist with offline tests; counts recorded.
- [ ] A live shutdown test shows an in-flight request completes and exit code is 0; the result is in `p1_results.md`.
- [ ] Docker and the host config use the right endpoints; nothing was deployed.
- [ ] Stale `reserved` ledger rows have a test or a documented recovery step.
- [ ] `/readyz` never exposes secrets.

---

## 8. P1-D (optional, only after P1-C) — monitoring and alerting

The service has Sentry and OpenTelemetry code, off unless configured, and an ingestion-only alert workflow. For the MCP server: error rate, p95 latency per tool, readiness failures, `call_model` spend per org against budget, and `provider_call_ledger` failures. Propose a minimal set of alerts (Slack webhook is enough) and a status page; ask the user before creating any external account or committing a webhook URL (webhooks are secrets; keep them in env/secrets, never in the repo).

---

## 9. Deliverables

1. Code and test changes, committed in small commits with the counts in each message.
2. `final_prod_docs/p1_results.md`: Step 0 findings; per-test run table (file, role, passed/failed/skipped); gap-by-gap outcome (Section 5.1); latency and load table; restore test; shutdown test; open items. Every number has a source and a date.
3. Updated: `docs/security_runbook.md` (residual risks), `final_prod_docs/customer/sla_policy.md`, `legal/b2b/msa.md` Exhibit A (numbers only; leave legal wording to the legal session), `final_prod_docs/06_claims_register.md`, `docs/deploy/hosted-mcp.md`.
4. A short message to the user: what is done, what failed, what needs their decision, what needs counsel (anything that changes a promise in a contract).

## 10. Coordination

- **Legal session:** owns `legal/` and `final_prod_docs/`. You may edit the SLA numbers and `p1_results.md`; send wording changes to the user.
- **Telemetry/ingestion session:** owns the event spec, rollup job and SQL views. Do not touch them. If your health or SLA work needs a metric they define, ask the user to relay.
- **Other sessions' uncommitted work.** `git status` on the author's checkout shows many modified files that are not yours (for example `backend/app/mcp_server/server.py`, `backend/app/config.py`, `backend/app/services/retrieval_service.py`, `packaging/npm/lib/*`). Run `git status` yourself, leave those alone, and stage only your own paths. If you edit `server.py` for `/healthz`, keep the change small and in its own commit to ease merging, and `git pull --rebase` first.
- `proj_status.md` is dated 2026-09-02; do not trust its numbers. Use `git log` and your own test runs.

## 11. Definition of done for the whole handoff

Each acceptance list above is ticked with evidence in `final_prod_docs/p1_results.md`, the suites you ran are named with counts, nothing touched a shared database, nothing was deployed, and the user has a one-screen summary of what is still open.
