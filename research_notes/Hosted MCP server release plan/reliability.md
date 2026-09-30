# Reliability engineering for a hosted MCP server that depends on LLM APIs (as of Sept 2026)

Context assumed: a Python Streamable HTTP MCP server on Cloud Run, Neon Postgres + pgvector, a main tool that calls an LLM judge chain (Vertex Gemini + OpenAI-compatible third parties such as General Compute, with fallbacks) and degrades to `not_checked`, and a 2-4 person team with no SRE. Repo note: `CLAUDE.md` says the MCP server must run `--workers 1` because the TasksExtension store is in memory. That affects every Cloud Run scaling decision below.

Source-quality note: all of the Google SRE, Cloud Run, Cloud Monitoring, MCP spec, OTel MCP semconv, Neon, LiteLLM, Portkey and Cloudflare facts come from pages fetched in this session. A few items are marked "(not re-fetched this session)". They come from well-known primary sources and should be spot-checked before anyone quotes them.

---

## 1. SLOs and error budgets for a small team

### Takeaway
Start with two request-based SLIs on a rolling 28-day window: availability of MCP `tools/call`, and latency of the judge-backed tool. Base the targets on measured performance rather than aspiration. For a young service that sits on top of third-party LLMs, 99.5% at alpha/beta is realistic and 99.9% is not. Use the SRE workbook's multiwindow, multi-burn-rate alerts, but at low traffic replace paging with synthetic probes and tickets.

### Cited Findings
- Availability SLI for request-driven services is "the proportion of requests resulting in successful responses (non-5xx)". Latency should use several thresholds, for example "90% of requests faster than 100 ms, and 99% of requests faster than 400 ms". — [SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/)
- Base the first SLOs on observed data. In the workbook's example, a measured 97.123% success rate became a 97% SLO, and latency targets were rounded up from measured p90/p99 (432→450 ms, 891→900 ms). "Your first attempt at an SLI and SLO doesn't have to be correct; the most important goal is to get something in place and measured." — [SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/)
- Recommended window: a **four-week rolling window**, plus weekly summaries and quarterly reports. — [SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/)
- An error-budget policy should be written and approved. It lists the actions taken on exhaustion: prioritize reliability bugs, work only on reliability until the service is back within SLO, and freeze certain changes. It also names who decides and how to escalate. — [SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/)
- Recommended starting multiwindow, multi-burn-rate parameters for a 99.9% SLO. The short window is 1/12 of the long window. — [SRE Workbook: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)

  | Severity | Long window | Short window | Burn rate | Budget consumed |
  |---|---|---|---|---|
  | Page | 1 h | 5 min | 14.4 | 2% |
  | Page | 6 h | 30 min | 6 | 5% |
  | Ticket | 3 d | 6 h | 1 | 10% |
- Low-traffic problem: at 10 requests/hour, "a single failed request results in an hourly error rate of 10%". Suggested mitigations are synthetic traffic, aggregating related low-traffic services, client-side resilience (retries, fallback paths), and recalibrating the SLO. — [SRE Workbook: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- Cloud Monitoring SLO alerting uses the `select_slo_burn_rate` time-series selector. A burn rate above 1.0 means the budget will be exhausted if the rate is sustained. Google recommends two policies:
  - **fast burn**: threshold 10x baseline, lookback 1-2 h
  - **slow burn**: threshold 2x baseline, lookback 24 h

  Constraint: "It is not currently possible to base alerts on the error budget consumption rate of an SLO using a compliance period of greater than 24 hours." — [Cloud Monitoring: Alerting on budget burn rate](https://docs.cloud.google.com/stackdriver/docs/solutions/slo-monitoring/alerting-on-budget-burn-rate)

### Inferences
- **Error budget per target, over 28 days (arithmetic):**
  - 99.0% ≈ 6.7 h
  - 99.5% ≈ 3.4 h
  - 99.9% ≈ 40 min

  Cloud Run, Neon and each LLM provider all add their own failure modes. Neon's Free and Launch plans carry no SLA (see §4). So 99.9% end to end is not credible before the service has redundancy and a paid DB tier.
- **Proposed SLO set by stage:**
  - **Private alpha.** No paging. Set internal SLOs of 99.0% availability, measured at the MCP endpoint (non-5xx, and JSON-RPC responses without an internal-error code), and p95 `tools/call` < 30 s. Review weekly.
  - **Public beta.** 99.5% availability over 28 days. Latency SLI: 95% of `tools/call` < 20 s and 99% < 60 s, with the thresholds set from measured judge latency. Page only on the 1 h/5 min 14.4x rule, and file tickets for slow burn.
  - **GA / paid.** Consider 99.9% for the *transport* SLI (the server responded) while keeping a separate *quality* SLI at a lower target.
- **Separate "judge coverage" from availability.** A `not_checked` degraded response is a success for availability but should count against a distinct **judge-coverage SLI**, for example "≥ 95% of tool calls return a checked verdict". Otherwise LLM outages either burn the availability budget unfairly or disappear from view entirely.
- **Low-traffic alerting:**
  - Run a synthetic MCP probe (initialize → tools/list → a cheap tools/call) every 1-5 minutes from Cloud Monitoring uptime checks or a Cloud Scheduler job. That gives a denominator large enough to make burn-rate alerts meaningful.
  - Page on N consecutive probe failures rather than on raw ratios.

### Gaps
- Uptime-check pricing and exact configuration were not fetched.
- Whether Cloud Monitoring's auto-detected Cloud Run SLIs can distinguish JSON-RPC errors carried inside HTTP 200 responses was not verified. They almost certainly cannot, which means MCP-level errors need a custom log-based or OTel metric.

---

## 2. Safe releases on Cloud Run, staging, flags, migrations

### Takeaway
Use Cloud Run revisions as the deploy unit:
1. Deploy with `--no-traffic --tag`.
2. Smoke-test the tag URL with a scripted MCP session.
3. Shift traffic 5% → 25% → 50% → 100%.
4. Roll back by pinning 100% to the previous revision.

Pair this with Neon branches for staging and preview DBs, and with expand/contract migrations. The repo's rule of additive, idempotent, immutable migrations already fits that pattern.

### Cited Findings
- Deploy without traffic and with a tag: `gcloud run deploy myservice --image IMAGE_URL --no-traffic --tag TAG_NAME`. Tagged revisions get a direct URL of the form `https://TAG_NAME---SERVICE_NAME-HASH.a.run.app`. — [Cloud Run: rollouts, rollbacks, traffic migration](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
- Gradual rollout uses `gcloud run services update-traffic SERVICE --to-revisions LATEST=5`, then 25, 50 and so on. Multi-way splits are supported, for example `REV1=25,REV2=25,REV3=50`. Rollback is `--to-revisions PREV_REVISION=100`. — [same](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
- Caveats:
  - Traffic splitting behaves differently when session affinity is enabled.
  - Traffic migration isn't instantaneous; in-flight requests finish on their original revision.
  - Tags don't allocate service-level min instances.
  - The split persists across deploys unless it is reset with `--to-latest`.

  — [same](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
- Neon restore creates point-in-time branches from the shared WAL, and branches are the unit of copy-on-write DB cloning. — [Neon: branch restore](https://neon.com/docs/introduction/branch-restore)

### Inferences
- **MCP-specific rollout hazard.** Streamable HTTP sessions are identified by `Mcp-Session-Id`, and the server must return 404 for unknown sessions, after which the client must re-initialize (see §5).
  - If sessions live in process memory, as with this repo's `--workers 1` TasksExtension store, a traffic shift or scale-in drops sessions. Clients then re-initialize, and any in-flight async tasks are lost.
  - Before public beta, either make the server stateless (no session ID, or session state in Postgres/Redis), or accept that each deploy forces reconnects and document it.
  - A 5% canary on a sessionful server is noisy because sessions are not sticky across revisions unless session affinity is on. Session affinity is best-effort and changes split behaviour, per the caveat above.
- **Preview environments.**
  - One Neon branch per PR (or a long-lived `staging` branch reset from `main` nightly).
  - One Cloud Run tagged revision per PR, pointing at that branch.
  - Branches are cheap copy-on-write clones, which makes "test the migration on a copy of prod" routine.
- **Migrations.** Keep the expand → deploy code that tolerates both schemas → contract sequence. Contract steps (drops, NOT NULL) go in a later release, after all traffic is on the new revision. That preserves one-command rollback, because the old revision still works against the expanded schema. The repo's existing rules (numbered, additive, idempotent, checksum-immutable) are the right foundation. Add a CI job that applies pending migrations to a fresh Neon branch of prod before deploy.
- **Feature flags.** At 2-4 people, env-var or DB-row flags are sufficient. The key flags are:
  - judge-chain membership and order
  - a kill switch to force `not_checked` without calling any LLM
  - per-tenant rate limits

  Changing an env var on Cloud Run creates a new revision; a DB-row flag avoids a redeploy. A SaaS flag service isn't needed until there are many tenants or experiments.

### Gaps
- No primary source was fetched on Cloud Run's automated gradual rollout via Cloud Deploy canary. That is an option if manual `update-traffic` becomes tedious.
- Neon's own "branching for preview environments" guide was not fetched. The branch-per-PR recommendation is an inference from the branching mechanics.

---

## 3. Resilience to LLM provider outages and rate limits

### Takeaway
Wrap every judge call in the following:
1. A hard per-call timeout.
2. At most 1-2 retries with exponential backoff, full jitter and a retry budget.
3. A per-provider circuit breaker (cooldown).
4. An ordered fallback chain across *different* providers.
5. A per-provider concurrency cap (bulkhead).
6. A final graceful degradation to `not_checked`.

LiteLLM's Router provides most of this in-process for free. A hosted gateway (Portkey, Cloudflare AI Gateway) mainly adds logging/analytics and caching, and is optional at this scale.

### Cited Findings
- LiteLLM Router reliability knobs:
  - `num_retries`
  - `retry_after`
  - `allowed_fails` (failures per minute before cooldown; default 3)
  - `cooldown_time` (default 5 s)
  - per-deployment `rpm` / `tpm` / `max_parallel_requests`
  - per-error `RetryPolicy` (e.g. `AuthenticationErrorRetries=0`, `NotFoundErrorRetries=0`, `DefaultRetries=2`)
  - `AllowedFailsPolicy` (e.g. `RateLimitErrorAllowedFails=100`)
  - routing strategies `simple-shuffle` (recommended), `latency-based-routing`, `usage-based-routing-v2`, `least-busy`, `cost-based-routing`
  - `enable_pre_call_checks`

  — [LiteLLM Routing docs](https://docs.litellm.ai/docs/routing)
- Example LiteLLM `router_settings` from the docs: `allowed_fails: 3`, `cooldown_time: 30`, `num_retries: 3`, `retry_policy: {NotFoundErrorRetries: 0, DefaultRetries: 2}`. — [LiteLLM Routing docs](https://docs.litellm.ai/docs/routing)
- Portkey fallbacks are declared as `{"strategy":{"mode":"fallback","on_status_codes":[429,503]},"targets":[...]}`. By default any non-2xx response triggers a fallback, and fallbacks can be nested inside load balancers or conditional routers. — [Portkey fallbacks](https://portkey.ai/docs/product/ai-gateway/fallbacks)
- Cloudflare AI Gateway offers caching, rate limiting, request retry and model fallback, logging and analytics. It is "available on all plans", and the listed providers include Anthropic, Google Gemini, OpenAI, Workers AI and Replicate. — [Cloudflare AI Gateway docs](https://developers.cloudflare.com/ai-gateway/)
- Retry budget / cascading failure guidance (not re-fetched this session):
  - Limit retries per request (e.g. 3 attempts).
  - Keep a per-client retry budget (retries ≤ ~10% of requests).
  - Use randomized exponential backoff.
  - Retry at only one layer of the stack, to avoid multiplicative amplification.

  — [Google SRE Book: Addressing Cascading Failures](https://sre.google/sre-book/addressing-cascading-failures/)
- Provider status: third-party aggregators claim that Anthropic and Google have fewer API outages than OpenAI, which reportedly has partial or full incidents several times a month, and they list OpenAI incidents on Sept 11 and Sept 14, 2026. **These are low-trust aggregator claims.** Use each provider's own status page for authoritative history. — [Webalert blog](https://web-alert.io/blog/ai-llm-api-monitoring-openai-anthropic-uptime); [Requesty status tool](https://www.requesty.ai/tools/llm-provider-status); [Bifrost provider status](https://www.getmaxim.ai/bifrost/provider-status)
- Vertex AI documents a dedicated retry strategy and a 429 troubleshooting page. Consumption options include Standard, Priority and Flex PayGo as well as Provisioned Throughput. — [Vertex/Gemini Enterprise Agent Platform docs index](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/retry-strategy). The exact backoff numbers could not be extracted (see Gaps).

### Inferences
- **Recommended judge-chain policy for this service:**
  - **Timeouts.** Set a per-attempt timeout for each judge. Judge-classification calls: connect 5 s, total 20-30 s. The whole tool call has a deadline (e.g. 60 s) that is always shorter than the Cloud Run request timeout and the MCP client's timeout. Stop starting new fallbacks once the remaining deadline is under one attempt's timeout, and return `not_checked`.
  - **Retries.**
    - Retry 429, 500, 502, 503, 504 and timeouts at most once per provider, with full-jitter backoff (e.g. base 1 s, cap 8 s) and `Retry-After` honoured.
    - Never retry 400/401/403/404.
    - Retry only in the judge layer; the MCP client and Cloud Run should not retry too.
  - **Circuit breaker.** After ~3-5 failures per minute, cool the provider down for 30-60 s; this is LiteLLM's `allowed_fails` / `cooldown_time`. During cooldown, go straight to the next provider.
  - **Bulkhead.** Cap concurrent in-flight calls per provider, for example with `max_parallel_requests` or an asyncio semaphore. One slow provider must not exhaust Cloud Run concurrency slots or the DB pool.
  - **Fallback diversity.** Order the chain so consecutive entries don't share a failure domain, e.g. Vertex Gemini (global endpoint) → General Compute (OpenAI-compatible) → a third provider. Record which judge answered on every result, because provenance matters for this product.
  - **Caching.** Cache judge verdicts by a hash of (normalized input, judge model, prompt version) in Postgres. It improves both cost and availability, and deterministic judges make this safe.
  - **Kill switch.** Add a flag that forces `not_checked` instantly during a provider meltdown or a cost blow-up.
- **Is a gateway worth it?**
  - **Alpha/beta:** no separate hosted gateway. Use LiteLLM *as a library* (Router) or a small hand-rolled wrapper. Either avoids another network hop and another vendor in the critical path, and both of those also have outages.
  - **Later:** once many tenants or keys need central spend tracking, logs and caching, reconsider Portkey or Cloudflare AI Gateway. Or self-host the LiteLLM proxy, which adds a service the team has to run.
- **Metrics to emit.** Per provider: success rate, latency, 429 rate, cooldown events, fallback depth. Track `not_checked` rate as the judge-coverage SLI from §1.

### Gaps
- The exact Vertex Gemini retry numbers (initial delay, multiplier, max attempts) could not be extracted; the doc pages rendered only as an index. Fetch https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/retry-strategy directly.
- General Compute's published SLA and status page were not found or checked.
- No primary, statistically sound source was found for comparative LLM API uptime in 2026. The aggregator claims are unverified.
- Portkey's circuit-breaker and retry parameters and its open-source gateway status were not shown on the fetched page.

---

## 4. Backups and restore testing

### Takeaway
Neon's PITR ("instant restore") gives a history window of 6 h on Free, up to 7 days on Launch and up to 30 days on Scale. Only Scale carries an uptime SLA. For a startup: target RPO ≤ 5 min via PITR and RTO ≤ 1 h. Add scheduled snapshots and an off-provider logical dump (e.g. nightly `pg_dump` to GCS). Test restores at least monthly, and before any risky migration.

### Cited Findings
- History window: Free "6 hours (1 GB limit)", Launch "up to 7 days", Scale "up to 30 days".
- Snapshots:
  - Free: 1 manual.
  - Launch/Scale: 100 manual, plus scheduled snapshots at $0.09/GB-month.
- Uptime SLA: none on Free or Launch; Scale has one, but the percentage was not shown.

— [Neon pricing](https://neon.com/pricing)
- How restore works:
  1. Neon creates a point-in-time branch at the LSN that matches the chosen timestamp.
  2. It moves the compute to that branch (connection string preserved) and renames the branch to the original name.
  3. The old branch becomes `{branch}_old_{timestamp}`.

  "Instant restore is only supported for root branches." — [Neon branch restore](https://neon.com/docs/introduction/branch-restore)
- Time Travel Assist lets you run read-only queries at any historical point to confirm the restore target before restoring. PITR is *not* supported on branches created from a snapshot restore. Backup branches cost storage, not compute. — [Neon branch restore](https://neon.com/docs/introduction/branch-restore)

### Inferences
- **Stage plan:**
  - **Private alpha.** At least the Launch plan, for the 7-day window, since Free's 6 h is too short to notice most logical corruption. Take a weekly manual snapshot. Run a nightly `pg_dump` to a GCS bucket with object versioning, which guards against Neon account or provider loss.
  - **Public beta.** The Scale plan gives 30 days of history and an SLA. Add scheduled snapshots. Run a monthly restore drill: restore to a new branch at T-24h, run schema-drift and row-count checks and a smoke test of the MCP server against it, then record the time taken as the measured RTO.
  - **Later.** Restore drills each quarter at minimum and after every major migration. Cross-region replica or logical backup, depending on customer requirements.
- **pgvector indexes.** A restored branch includes indexes because it is storage-level. A `pg_dump` restore must rebuild HNSW/IVFFlat indexes, which lengthens RTO. Measure this during the drill.
- **Cloud SQL alternative (not fetched this session).** Cloud SQL has automated daily backups and PITR via log retention. Neon's branch model makes restore drills much cheaper and is the better fit here.

### Gaps
- The Neon Business/Enterprise tier details and the Scale SLA percentage were not shown on the fetched pricing page.
- No authoritative source was fetched for startup RPO/RTO norms. The 5 min / 1 h targets are an inference.

---

## 5. Load testing MCP servers and Cloud Run concurrency

### Takeaway
Load-test the real MCP lifecycle (initialize → notifications/initialized → tools/list → tools/call → DELETE), one session per virtual user. Locust (Python, which fits this stack) or k6 both work. Stub the LLM judges with a latency-injecting fake so the server itself is measured, then run a smaller test against real providers to find their 429 thresholds. Because the workload is I/O-bound on LLM calls, Cloud Run concurrency can be high, but `--workers 1` plus in-memory session state is the binding constraint.

### Cited Findings
- Streamable HTTP rules:
  - One MCP endpoint serves POST and GET.
  - Every client JSON-RPC message is a new POST with `Accept: application/json, text/event-stream`.
  - The server may reply with JSON or open an SSE stream.
  - A disconnect "SHOULD NOT be interpreted as the client cancelling"; cancellation is an explicit `CancelledNotification`.

  — [MCP spec 2025-06-18: Transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- Sessions:
  - The server may assign an `Mcp-Session-Id` on the InitializeResult response, and clients must echo it.
  - After terminating a session the server must return 404, and the client must then re-initialize.
  - Clients should send DELETE to end a session.
  - Streams can be resumed with SSE event IDs and `Last-Event-ID`.
  - Clients must send `MCP-Protocol-Version` on subsequent requests; the server returns 400 for an unsupported version.

  — [MCP spec 2025-06-18: Transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- Servers must validate `Origin` and should require authentication. — [same](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- Microsoft has published a portable Locust harness that "talks MCP natively" and runs locally or in Azure Load Testing. It covers the handshake, tool discovery, tool invocation and graceful session termination. The article notes that agent traffic "arrives in bursts, sustains long sessions, and hits the same handful of tools at a parallelism a typical API client never produces". One 15-minute run against four MCP endpoints generated 2,293 requests. — [Microsoft Community Hub (via search snippet; full page did not render)](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/load-testing-hosted-mcp-servers-with-locust-and-azure-load-testing/4522691)
- Community k6 guidance for MCP: session handshake, parsing SSE bodies, one session per VU. — [oddyssey issue #425](https://github.com/using-system/oddyssey/issues/425); [aiquila k6 MCP smoke test issue](https://github.com/elgorro/aiquila/issues/585)
- Cloud Run concurrency:
  - Default is 80 x vCPU (CLI/Terraform) or 80 (console); maximum is 1,000 per instance.
  - Higher concurrency suits "applications efficient at parallel I/O-bound tasks".
  - Single-threaded apps on multi-vCPU instances should lower concurrency to avoid vCPU hotspots.

  — [Cloud Run: About concurrency](https://docs.cloud.google.com/run/docs/about-concurrency)
- Cloud Run request timeout can be set up to 60 minutes; the default is 5 min. Session affinity is best-effort (not re-fetched this session). — [Cloud Run docs](https://docs.cloud.google.com/run/docs/configuring/request-timeout)

### Inferences
- **What to measure:**
  - p50/p95/p99 latency per MCP method
  - initialize success rate
  - 404-session-lost rate
  - error rate by JSON-RPC code
  - SSE stream duration
  - Cloud Run instance count, cold-start latency and container CPU/memory
  - Neon connections in use and pool wait time
  - per-provider LLM 429s and latency
  - `not_checked` rate under load
  - cost per 1,000 tool calls
- **Test shapes:**
  - A steady ramp to the target sessions.
  - A burst test, e.g. 10x in 30 s, for agent-style fan-out.
  - A soak test of 1-2 h to catch leaks in the in-memory session/task store.
  - A chaos run in which the fake judge returns 429/503 or hangs, to verify timeouts, cooldowns and `not_checked`.
- **Cloud Run settings to start from:**
  - An async (uvicorn) single worker can handle high concurrency for I/O waits. Start with `--concurrency 40-80`, 1 vCPU and 1-2 GiB.
  - `--min-instances 1` avoids cold starts on the synthetic probe and first users.
  - Set `--max-instances` so Neon's connection limit isn't exceeded: max_instances × pool_size < Neon pooled-connection limit. Use Neon's pooled (PgBouncer) endpoint.
  - Request timeout: a little above the tool-call deadline.
  - With in-memory sessions, enable session affinity *and* accept losses on scale-in, or pin to max-instances 1 during alpha, which is simplest and honest about capacity.
- **Scaling beyond one instance.** The robust path is stateless Streamable HTTP, or externalizing session and task state, so any instance can serve any request.

### Gaps
- The full Microsoft Locust article (latency numbers, failure modes) did not render.
- No MCP-specific load tool with meaningful adoption was verified beyond community k6/Locust scripts.
- Neon connection limits per compute size were not fetched.

---

## 6. Observability for MCP, and incident response for tiny teams

### Takeaway
Instrument with OpenTelemetry using the GenAI and MCP semantic conventions. These are still at **Development** status and have moved to a dedicated `semantic-conventions-genai` repo, so pin versions and expect renames. Do not record tool arguments/results or prompt/completion content by default. For incident response, one rotating on-call with runbooks, a hosted status page and blameless postmortems is sufficient.

### Cited Findings
- The OTel GenAI semconv pages on opentelemetry.io now say they have "moved and [are] no longer maintained in this repository". The new home is https://github.com/open-telemetry/semantic-conventions-genai. — [opentelemetry.io GenAI MCP page](https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/)
- MCP semconv status is **Development**.
  - Span name: `{mcp.method.name} {target}`, e.g. `tools/call get-weather`.
  - Attributes:
    - `mcp.method.name` (required)
    - `mcp.session.id`, `mcp.protocol.version` (recommended)
    - `mcp.resource.uri` (conditionally required)
    - `gen_ai.tool.name`, `gen_ai.operation.name=execute_tool`, `gen_ai.prompt.name`
    - `network.transport`, `client.address` / `client.port`
  - Metrics: `mcp.server.operation.duration`, `mcp.server.session.duration`, and their client equivalents.

  — [OTel semantic-conventions-genai: mcp.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md)
- `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` and `gen_ai.prompt.variable` are explicitly flagged as potentially sensitive. — [same](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md)

### Inferences
- **Tracing layout:**
  - A server span per MCP request (`tools/call <tool>`).
  - Child spans per judge attempt, following the GenAI client-span conventions: `gen_ai.provider.name`, `gen_ai.request.model`, token usage.
  - Attributes for fallback depth, cooldown hits and final verdict status (`checked` / `not_checked`).
  - A DB span per query.
- **Export.** Send to Cloud Trace via the OTel Collector or the Google exporter, and derive the SLI metrics from `mcp.server.operation.duration` plus a custom `status` attribute.
- **Privacy:**
  - Hash `mcp.session.id` or keep it as-is. It isn't user content, but treat it as an auth-adjacent secret if it grants access.
  - Never set the arguments/result attributes in production.
  - Route any free-text log field through the existing `trace_redaction.redact_event` chokepoint.
  - Log tenant ID and request ID, not payloads.
  - Keep OTel content-capture env flags off by default.
- **Incident response for 2-4 people:**
  - **Alpha.** Founders get alerts in Slack or email. No formal on-call.
  - **Beta.**
    - A weekly rotating primary with a named secondary.
    - Page only on the §1 fast-burn and synthetic-probe rules.
    - A hosted status page (e.g. Better Stack, Instatus, Atlassian Statuspage), with components: MCP endpoint, judge verification, database.
    - Short runbooks for:
      - LLM provider outage: flip the kill switch or reorder the chain.
      - Neon outage or restore: the PITR steps above.
      - Bad deploy: `update-traffic PREV=100`.
      - Rate-limit exhaustion.
      - Leaked key: rotate the Secret Manager version.
    - Blameless postmortems for any SLO-impacting incident, using the SRE postmortem template.
  - **Later.** Formal error-budget policy enforcement (§1), plus a paging tool (PagerDuty, Opsgenie, Better Stack).
- **Status page honesty.** Show the judge-verification component as "degraded" when `not_checked` is elevated, even if the endpoint is up.

### Gaps
- No MCP-specific OTel instrumentation library for the Python MCP SDK was verified (e.g. whether OpenLLMetry or the official SDK emits these spans natively in 2026).
- The GenAI semconv stability timeline (when or whether it goes Stable) was not found.
- The SRE postmortem culture chapter and status-page vendor pricing were not fetched in this session.
