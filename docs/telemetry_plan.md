# Telemetry plan

Status 2026-10-06. **DONE** = built and tested in the repo (not deployed). **PLAN** = designed, not built. Two audiences:
**customer admin** (their own organisation only, through `/v1/orgs/{org_id}/...`) and **internal** (the platform team, across
organisations; there is no cross-organisation endpoint yet, so these are database queries or a future platform-admin app).

## Privacy and retention rules for every metric

- Record ids, counts, hashes, timings and model names. Never prompts, replies, code or keys.
- Three retention classes: **customer content** (ephemeral, 24 h promise), **operational metadata** (at least 180 days, in
  India, for CERT-In), **billing evidence** (contract and tax period; ask the CA). Each table below states its class.
- Customer views are row-level-security views (`security_invoker`), proven not to leak across organisations.

## Customer admin view

| Metric | Source | Status |
|---|---|---|
| Input, output, processing (cache) cost; unattributed cost | `provider_call_ledger`, `v_org_usage_daily` | **DONE** |
| Latency p50/p95/p99 by model, provider, tool and **tier** (provider time) | `v_org_performance_daily`, `GET /performance` | **DONE** |
| **Router overhead**: p50/p95/p99 of the time our code spends before the provider is called (`gate_ms`), and our share of total time | same view | **DONE** for `call_model` |
| Cache hit rate by requests and by tokens, over calls whose provider reports cache tokens only (NULL means "not reported", never 0%) | same view | **DONE** |
| Tier label (light / standard / flagship) per unit, set on the connection | `UnitSpec.tier` | **DONE** (admins must set it) |
| Spend vs monthly and per-user daily budget | policy + ledger | **DONE** (per-user spend view not built) |
| Spend and position against budget **per person** (`/usage/users`, `/budget`) and a members list to name them (`/members`) | ledger, `v_org_usage_by_user_daily`, `org_memberships` | **DONE** |
| Call explorer: metadata for each call, no content (`/calls`) | ledger | **DONE** |
| **Denials** with reason codes and counts (`/denials`) | `org_denials` (db/139) | **DONE** for policy and budget refusals on `call_model`; auth failures, cross-tenant denials, screen rejections, connection-level and max-cost refusals **PLAN** |
| True merged p50/p95/p99 over a date range, by model, provider, tool or tier (`/performance/summary`) | raw ledger | **DONE** |
| Member invite, role change, remove | needs an invitation and notification design | **PLAN** |
| Server-side alerts (budget threshold, spend spike, error rate) with email or webhook | needs a delivery channel and a scheduler | **PLAN** |
| Savings vs baseline, cost per resolved task, escalation rate, advice followed | gain-share ledger, hook | **PLAN** (next migration) |

**On the "<15 ms router" figure.** Not claimed. `gate_ms` includes a DNS check and a database round trip for the budget
reservation, which is likely tens of milliseconds, so measure first and then set a target. If it is too slow, the levers are a
short-lived cache of the endpoint safety check and a policy cache with invalidation on change. For `find_ways`, the plan's own
time (`plan_ms`) is now stored with each request in `retrieval_decisions.detail` (internal, see below).

**Tiers.** Comparing quick models with flagship models needs the label, so it is data the admin enters. A unit with no tier
is reported as NULL and is not guessed from its price.

## Internal telemetry (our side)

| # | Metric | Why | Source | Class | Status |
|---|---|---|---|---|---|
| 1 | `find_ways` request time, outcome, **plan_ms** (router overhead), shard fan-out, calling client | latency, router cost, which harness | `retrieval_decisions.detail` | operational | **DONE** (plan_ms new); no org id on the row yet |
| 2 | `find_ways` **stage timings** (embed, goal search, judge calls: count and ms, procedure select) | where the time goes | `RetrievalMeta.latency_ms` exists in memory, not returned or stored | operational | **PLAN**: persist under `detail.stages` |
| 3 | **Degraded-path counters** by reason (embedding unavailable gives lexical-only, judge fallback, flat hierarchy) | the no-fallback policy needs to know how often each fires before removing it | `meta.degrade(...)` reasons are collected | operational | **PLAN**: persist |
| 4 | **Our own LLM spend** (judge, screen, extraction, embeddings): tokens, cost, per tenant | COGS and margin on gain-share | `ingest_budget` covers ingestion only | billing | **PLAN**: a platform ledger like the customer one |
| 5 | **Availability and errors** per tool: success / refused / denied / error with code; SLO burn | the 99.9% claim | OpenTelemetry (`mcp.server.operation.duration`, `error.type`; names verified against the MCP conventions, status Development) plus an external probe | operational | **PLAN**: enable the exporter in the deploy; probes via a hosted service |
| 6 | **Outbox and queue health**: `projection_outbox` pending count and age, ingestion backlog, `ingest_ledger` status by pipeline | search freshness, ingestion yield | tables exist | operational | **PLAN**: gauge job |
| 7 | **Database health**: pool in-use and wait time, connection count against the limit, slow queries, Neon compute and storage | capacity, pooler decision | asyncpg pool stats, Neon | operational | **PLAN** |
| 8 | **Governor and limiter**: refusals by reason (too small, repeated, budget), cache hits | abuse and agent loops | in-process only | operational | **PLAN**: persist (also needed to move off per-process state) |
| 9 | **Security events**: auth failures, scope denials, cross-tenant 404s, screen rejections by category (link, hidden text, injection, malicious, NSFW), policy denials by reason, kill-switch use | what a security reviewer asks for | `record_security_event` exists in `audit.py`; most denials are not recorded | operational / audit | **PLAN** |
| 10 | **Adoption funnel**: install, first successful `find_ways`, weekly active users per org, tool mix, `find_ways` then `report_result` conversion | is the product used | `retrieval_decisions` (viewer), ledger, routing observations | operational | **PLAN**: needs an org id on `retrieval_decisions` |
| 11 | **Quality**: resolved vs `no_match` vs ambiguous by org and Goal, verified-solution hit rate, first-rung pass rate, cost per resolved task | explains savings movement | partly there | operational | **PARTIAL** |
| 12 | **Reconciliation**: ledger total against the provider's own invoice or usage export, alert on drift | gain-share evidence | provider billing exports | billing | **PLAN** |
| 13 | **Purge and erasure jobs**: rows purged, oldest age, failures, erasure manifests | proves the retention promise | erasure manifests exist | operational | **PARTIAL** |
| 14 | Data class mix per org | trust reviews | `provider_call_ledger.data_class` | operational | **DONE** |

## Order

1. Items 2 and 3 (they change what we fix first, and they are small).
2. An org id on `retrieval_decisions`, then item 10 and the org-level quality view.
3. Items 5, 8 and 9 with the deploy.
4. The gain-share ledger, then item 12.
5. Item 4 once the customer ledger has real traffic to copy the design against.
