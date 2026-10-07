# Ship-readiness plan: enterprise onboarding gaps

Status as of 2026-10-06. Each row is **DONE** (built and tested, with the files) or **PLAN** (design, order, acceptance test,
and the decision it is waiting on). Nothing here is deployed: "done" means in the repo with tests, not live.

## The rule: no fallback

New code in this plan has no silent degrade path. Concretely, and tested in `test_org_governance_offline.py` /
`test_org_governance_e2e.py`:

| Situation | Behaviour (never a fallback) |
|---|---|
| Organisation has no policy | calls denied (`PolicyMissing`); a new policy must set every field |
| Allowlist is empty | nothing is allowed; no wildcard, no "allow all" value |
| Budget is 0 | zero spend; "unlimited" cannot be expressed |
| Unit has no price | cannot be held against a budget, so refused |
| Tenant setting unset in the database | no rows, no writes (`sl_org_strict`), unlike db/29's allow-when-unset |
| Provider call finished but cost cannot be recorded | the result is withheld and an error is raised |
| Call failed and its hold cannot be released | error raised, not swallowed |
| No usage returned by the provider | cost recorded as the reserved **worst case** (`upper_bound`), never zero |
| Audit write fails | the admin change rolls back with it |
| Erasure with blocked tables | request stays `executing`; it never reports `completed` |
| Several organisations, none named | the call is refused; no organisation is guessed |

### Existing fallbacks (not changed in this pass: each needs a decision)

91 backend files mention "fallback"; most are benign (display names, avatars). The ones that change correctness, tenancy or
cost, with a recommendation:

| Where | What it does today | Recommendation |
|---|---|---|
| `api/deps.py::get_scope` | on a transient identity failure, degrades to an owner-only scope | return 503 (fail closed) |
| `db/29` `sl_tenant_scope_allows` | unset `app.tenant_id` means "allow" | move each table to `sl_org_strict` after the query-path adoption sweep |
| `retrieval_service.search_goals` | embedding provider down gives lexical-only candidates (`meta.degrade`) | decide: error with reason, or keep and label (changes answer quality silently otherwise) |
| `semantic/chain.py`, `SEMANTIC_PROVIDER_FALLBACKS` | tries other judges, skips a refusing provider for 300 s | one named judge per environment; error if it fails |
| `execution/intent_resolution.py` | lexical re-ranker when no semantic judge answered (`lexical_fallback`) | remove or surface as an error |
| `hierarchical_goal_routing.py` | flat candidates when hierarchy has no anchors (`used_flat_fallback`) | keep only as an explicit, reported mode |
| `mcp_server/server.py::_record_find_ways` | cost record failure never fails the request | write to a durable queue; alert on queue failure |
| `services/embeddings.py` provider chain | next provider on failure | single provider per environment |
| `ingest*` | many retry/alternate paths | review per pipeline; ingestion is not in the customer path |

## Roles and admin console

| | |
|---|---|
| **DONE** | Admin REST API `app/api/org_admin.py` (mounted in `main.py`): policy get/put with optimistic version, kill switch, usage, audit export (JSON/CSV, cursor, chain check), legal holds, two-person erasure. Roles come from `org_memberships`; non-members get 404, wrong role 403; the service layer re-checks roles. |
| **PLAN** | (1) The UI: the new `admin/` Next.js app calls these routes. (2) Membership management API (invite, change role, remove) is **not built**. (3) Provider connections page and a DB-backed connection store, keys by reference in a secret manager (Google Secret Manager vs encrypted column is undecided). (4) Admin SSO (see WorkOS note in the chat history: Supabase SAML first). **Acceptance:** an admin can do everything above from the UI; a member cannot; a leak test over every route. |

## Policy controls

| | |
|---|---|
| **DONE** | `org_policies` (db/136): kill switch with required reason, provider/model/tool/data-class allowlists, monthly and per-user daily budgets, optimistic version. Enforced on every `call_model` in a shared deployment (`providers/service.call_unit` → `org_governance.reserve_call`), re-read under a row lock so changes apply to the next call. Spend is held before the call and settled once after; concurrency-tested (8 parallel calls, budget for 3, exactly 3 succeed). |
| **PLAN** | (a) **Kill switch and tool allowlist for every MCP tool**, not only `call_model`: a middleware in `_enforce_tool_scope` that resolves the caller's organisation and reads the policy (cache with a short TTL; a read failure denies). (b) **Approval for risky actions**: classify each procedure step and each agent unit by effect (read / draft / write / external communication / destructive / privileged), store an approval request, and let the user or an admin approve it (MCP elicitation where the client supports it, otherwise the admin app). `unknown` effect requires approval. Design pattern studied in supermemoryai/company-brain (`approval-classifier.ts`, `tool-policy.ts`, `lease/*`). (c) **Borrowed access** for shared org keys: read-only vs read-write grants with expiry. **Acceptance:** each is a proving test that fails on a bypass attempt. |

## Audit log access

| | |
|---|---|
| **DONE** | `audit_events` is now append-only and hash-chained per tenant (db/136); `sl_audit_chain_check()` finds the first altered row, proven by a tamper test. Export with an explicit date range, cursor paging and the chain result. Every admin action (policy, kill switch, hold, erasure) writes its audit row in the same transaction. |
| **PLAN** | SIEM streaming (an outbox plus a signed webhook or S3 delivery, with retry and a dead-letter table), and audit coverage for authentication failures, tool denials, cross-tenant denials and key changes. **Acceptance:** an event reaches a test sink exactly once after a sink outage. |

## Offboarding and deletion

| | |
|---|---|
| **DONE** | `org_legal_holds` and `org_erasure_requests` (request by one owner, approval by a different owner, enforced by a CHECK). `sl_erasure_authorized()` allows a delete only inside a transaction naming an approved request, and never under an active hold (checked at approval, at execution, and by the database). Execution deletes the governance tables, verifies zero remain, and records a manifest. |
| **PLAN (blocking)** | The core tables cannot yet be erased, and the manifest says so: `procedures` has `tg_procedures_refuse_delete`; `evidence`, `change_sets`, `change_set_operations`, `failure_routes` and `executions` have append-only or frozen triggers; claims and procedure versions live on knowledge shards; routing logs are keyed by `owner_id` in per-Goal search pools; `goals` are shared. Work: (1) give each trigger an erasure branch using `sl_erasure_authorized(tenant_id)`; (2) shard fan-out for the delete; (3) decide ownership of shared Goals; (4) the user-to-organisation mapping for `owner_id` tables; (5) **data export before delete** (not built); (6) the 24-hour purge job for ephemeral data. 27 tables carry `tenant_id`, 18 only `owner_id`, 2 an organisation column. **Acceptance:** an end-to-end erasure of a seeded organisation across control and shards ends `completed` with every table at zero. Until then, do not promise erasure in a contract. |

## Availability

| | |
|---|---|
| **PLAN** | (1) Multi-instance: governor counters to the database (or accept looser limits and document), remove or relocate `TasksExtension` and its single-worker boot check, Neon `-pooler` host (the pool already disables prepared statements for it), check that several search-projection drains converge (they use advisory locks and `SKIP LOCKED`). (2) `/healthz` and `/readyz` that check the database. (3) Deploy (Cloud Run or equivalent) with a minimum of two instances. (4) Hosted status page fed by external probes. (5) Incident process and on-call. (6) A tested restore. **Acceptance:** kill one instance under load with no failed requests; a restore drill timed. |

## Secure development

| | |
|---|---|
| **DONE** | `.github/dependabot.yml` (pip, npm x3, actions); `.github/workflows/security.yml` (secret scan on pull requests, weekly and manual dependency audit and SBOM); `docs/security/vulnerability_policy.md` (proposed fix times, needs founder sign-off); `prod_frontend/public/.well-known/security.txt`. The workflows have not run yet. |
| **PLAN** | Triage the open Dependabot alerts; **rotate every secret shared in chat and delete the `.env.bak.*` files** (needs you: deleting live-key files is not reversible); branch protection and required review; signed images (cosign keyless) once images are built in CI; yearly penetration test; rewrite `SECURITY.md`, which still describes the v0.1 single-user local posture. |

## Tenant safety

| | |
|---|---|
| **DONE** | Cross-tenant e2e tests for the routing surface and the governance tables (strict row-level security proven under a plain role, the usage view proven not to leak). Submitted ways are screened for links, hidden characters, injection phrases and by a fail-closed model; `find_ways` replies carry `content_trust`; `instance_key` is bound to its caller. |
| **PLAN** | **Quarantine decision.** Recommended: a new way is visible only to its submitter's organisation until it has N independent verified uses or a reviewer approves it. This is a product decision because it slows the shared library. Then a test that an unapproved way never appears for another tenant. |

## Compliance evidence

**PLAN.** SOC 2 Type I first: written policies (access, change management, vendor management, incident response, backup), quarterly access review, evidence collection tooling, then a Type II observation window. Audit-log retention (SOC 2 typically 12 months; CERT-In needs 180 days in India) must be reconciled with the 24-hour purge promise by separating data classes (see telemetry notes). A trust page and standard questionnaire answers (CAIQ-lite) come after the policies exist. ISO 27001/42001 only if a customer requires them.

## AI-specific

**DONE (draft).** `docs/trust/ai_system.md`: the system description, data flows per tool, and model inventory taken from the repo. Provider retention, training and residency statements are marked **unverified**: confirm each against the provider's current terms before publishing. **PLAN:** publish after confirmation; add a per-org view of which providers have seen its data (the ledger already records provider, model and data class per call).

## Support

**DONE.** `docs/support/onboarding.md` (IDE setup for Claude Code, Cursor, Codex and opencode, plus what to send when something fails). **PLAN:** support contact, response times and escalation need business inputs (not invented here).

## Procurement

**PLAN.** Blocked on incorporation: CIN, PAN, GST registration, bank account, MSA/DPA templates reviewed by counsel, insurance quotes (cyber and E&O), two reference customers from the pilot, pricing and invoicing (GST invoices).

## Metrics (gain-share and the product-health groups)

| Group | Where it is measured | State |
|---|---|---|
| Cost, cache, tokens per call | `provider_call_ledger`: fresh input, cache read, cache write and output kept apart, `cost_source` (provider / declared / upper_bound), `tool`, `instance_key`; `v_org_usage_daily` | **DONE** for `call_model` |
| Latency percentiles by model and tier, router overhead, cache hit rates | `v_org_performance_daily` (`gate_ms`, provider latency, `tier`, cache tokens); `plan_ms` in `retrieval_decisions`; see `docs/telemetry_plan.md` | **DONE** for `call_model` |
| Cost drivers (host turns, tool calls, attempts per task, tokens added by `find_ways`) | needs the host's own telemetry (Claude Code and Cursor OpenTelemetry, or the hook), plus `find_ways` result size at the server | **PLAN** |
| Savings breakdown, coverage (intent-to-treat), time | needs the gain-share ledger: arm (treated or control), task id and stratum, rework flag, price-effect separated by a fixed rate card | **PLAN** (next migration) |
| Product health | `retrieval_decisions` already has `find_ways` outcome and time; advice-followed needs the hook | **PARTIAL** |
| Trust (denials, screen rejections, data-class violations) | denials are raised but not stored as events | **PLAN** (security event stream) |

## Decisions needed from you

1. Fallbacks: for each row in the table above, remove it or keep it labelled.
2. Quarantine of new shared ways (recommendation above).
3. Vulnerability fix times (proposed: critical 7 days, high 30, medium 90) and the security contact (GitHub private advisories are used until you give an address).
4. Where provider keys live: Google Secret Manager or an encrypted column.
5. Whether to delete the `.env.bak.*` files now, after rotating their keys.
