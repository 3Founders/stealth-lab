# Ship Kel stateless, OAuth-gated, and quarantined

Kel should launch as one **stateless** Cloud Run service behind a global load balancer with Cloud Armor, with authorization handled by an external OAuth 2.1 identity provider, Neon Postgres kept (Launch plan for alpha, Scale for beta), a Cloud Run Job for ingestion, and Cloud Batch on Spot VMs for SWE-bench grading. That setup costs roughly **$100/month for a private alpha and $150–190/month for a public beta**, before LLM usage. Three changes block the release. First, the in-memory TasksExtension store that forces `--workers 1` has to move to Postgres or be removed. MCP spec revision 2026-07-28 **drops protocol-level sessions altogether**, and Cloud Run only offers best-effort session affinity, so a sessionful server cannot scale out or roll out safely. Second, writes must go through a spec-compliant OAuth flow that satisfies Claude's stricter-than-spec connector rules. Anonymous reads stay open, but a write has to trigger a real HTTP 401. Third, the product's own content is its biggest security risk. Procedure text that other users' agents execute is a stored prompt-injection channel, so unverified submissions must never reach other tenants. The public record backs all three. Asana's hosted MCP server leaked data across tenants and was **taken offline June 5–17, 2025**. GitHub's MCP server was hijacked through a poisoned public issue, and Invariant Labs said that flaw "cannot be resolved through server-side patches". For compliance, CERT-In's **6-hour incident reporting and 180-day log retention already apply**, full DPDP duties arrive **13 May 2027** (possibly earlier), and GDPR applies from the first EU developer you target. SOC 2 can wait until a buyer asks for it, but the groundwork is nearly free and should start now.

## One Cloud Run service behind a load balancer covers the whole shape

Google now documents Cloud Run as the place to host remote MCP servers. It supports streamable HTTP and SSE transports, not stdio ([Cloud Run: Host MCP servers](https://docs.cloud.google.com/run/docs/host-mcp-servers)). It allows requests of up to 60 minutes ([request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout)) and up to 1,000 concurrent requests per instance ([release notes](https://docs.cloud.google.com/run/docs/release-notes)). Its weak spot for MCP is **session affinity, which is only best-effort**. Google says it "cannot assume that a client will always reconnect to the same instance", and affinity also skews traffic splits ([session affinity](https://docs.cloud.google.com/run/docs/configuring/session-affinity)). A server that holds `Mcp-Session-Id` or task state in process memory therefore drops sessions whenever it scales in, recycles an instance, or shifts canary traffic. The repo's own run comment already calls `--workers 1` "load-bearing" for that reason. GKE Autopilot would give real stickiness, but it adds a cluster to operate, which five tools at low traffic don't justify. App Engine is legacy for new work (both of these are judgement calls, not sourced comparisons).

The recommended topology has a single public hostname. It resolves to a **global external Application Load Balancer** with a Google-managed certificate and a serverless NEG, with a **Cloud Armor** policy attached. The NEG routes to the `kel-mcp` Cloud Run service in one region, and the service's ingress is set to `internal-and-cloud-load-balancing` so the `run.app` URL cannot bypass Armor. The load balancer earns its ~$18/month in two ways. Cloud Run's own custom-domain mapping is still Preview and "not production-ready" ([mapping custom domains](https://docs.cloud.google.com/run/docs/mapping-custom-domains)). And Cloud Run data-transfer charges do not apply to traffic that arrives through a serverless NEG ([serverless NEG concepts](https://docs.cloud.google.com/load-balancing/docs/negs/serverless-neg-concepts)). Behind the service sit five more pieces. **Neon** Postgres + pgvector is reached through its PgBouncer transaction-mode pooled endpoint ([Neon pooling](https://neon.com/docs/connect/connection-pooling)). The **ingestion worker runs as a Cloud Run Job**. **SWE-bench grading runs on Cloud Batch**, which is free on top of the VMs it launches and supports Spot provisioning with `RETRY_TASK` on preemption exit codes ([Batch](https://docs.cloud.google.com/batch/docs/get-started); [task retries](https://docs.cloud.google.com/batch/docs/automate-task-retries)). Secrets live in **Secret Manager**, and telemetry goes to **Cloud Trace/Monitoring/Logging** via OpenTelemetry. The Google-Built OTel Collector can run as a Cloud Run sidecar and export OTLP to `telemetry.googleapis.com` ([Google-Built OTel Collector](https://docs.cloud.google.com/stackdriver/docs/instrumentation/google-built-otel); [Cloud Run collector](https://docs.cloud.google.com/stackdriver/docs/instrumentation/opentelemetry-collector-cloud-run)).

The main alternative is migrating to Cloud SQL, and it is worth weighing. Cloud SQL ships pgvector 0.8.1 and GA Managed Connection Pooling, and it keeps traffic on private IP inside GCP ([Cloud SQL release notes](https://docs.cloud.google.com/sql/docs/postgres/release-notes)). One instance could also replace many Neon projects. Neon still wins here for three reasons. Its copy-on-write **branches make preview environments and restore drills cheap**. The sharded project-per-shard layout already exists. And Neon Scale adds an SLA, IP allow-lists and private networking when needed ([Neon pricing](https://neon.com/pricing)). AlloyDB starts around $48/month for vCPU alone before memory and is overkill at this scale ([Bytebase AlloyDB](https://www.bytebase.com/blog/understanding-google-alloydb-pricing/)). Grading stays on Batch or raw Compute Engine because Cloud Run Jobs cannot run nested Docker. Either way, the **12-vCPU global Compute Engine quota caps grading throughput**, so request a Spot CPU quota increase now. Place the Cloud Run region next to Neon's primary region, since every tool call makes several DB round trips. The notes place Neon in a US region but did not confirm which cloud it runs on, so check the region before choosing.

### Concrete service configuration

The MCP service should run as its own user-managed service account. Env-var secrets should be pinned to explicit versions. Secrets that rotate should be volume-mounted instead, because they are read at access time and follow `latest`, while env-var secrets are resolved once when the instance starts ([Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets)). Instance-based billing (`--no-cpu-throttling`) is the right choice with `min-instances=1`: the instance is billed continuously anyway, and background work keeps its CPU ([autoscaling](https://docs.cloud.google.com/run/docs/about-instance-autoscaling)). Hold `max-instances` at 1 until task state is externalized. After that, raise it to a figure where `max_instances × pool_size` stays under Neon's pooled connection limit and a runaway cannot starve the 20-vCPU regional quota that ingestion jobs share.

```bash
# Runtime identity (never the default compute SA)
gcloud iam service-accounts create kel-mcp
gcloud secrets add-iam-policy-binding neon-shards --member=serviceAccount:kel-mcp@$P.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor
# grant only: aiplatform.user (Vertex judge via ADC), cloudtrace.agent, monitoring.metricWriter, logging.logWriter

# Deploy by digest to a zero-traffic tagged revision, smoke test, then shift.
# Keep --max-instances=1 until the TasksExtension store leaves process memory; then 4-8.
gcloud run deploy kel-mcp --region=$R \
  --image=$R-docker.pkg.dev/$P/kel/mcp@sha256:$DIGEST \
  --service-account=kel-mcp@$P.iam.gserviceaccount.com \
  --cpu=1 --memory=2Gi --concurrency=40 --timeout=300 \
  --min-instances=1 --max-instances=1 \
  --no-cpu-throttling \
  --ingress=internal-and-cloud-load-balancing --allow-unauthenticated \
  --set-secrets=/secrets/neon/shards.json=neon-shards:latest,MCP_TOKEN_PEPPER=mcp-pepper:3 \
  --set-env-vars=STEALTHLAB_PUBLIC_ORIGIN=https://mcp.kel.dev \
  --no-traffic --tag=canary
gcloud run services update-traffic kel-mcp --to-revisions=LATEST=5    # then 25, 50, 100
gcloud run services update-traffic kel-mcp --to-revisions=PREV_REV=100 # rollback

# Ingestion job
gcloud run jobs deploy kel-ingest --cpu=2 --memory=4Gi --task-timeout=3h \
  --service-account=kel-ingest@$P.iam.gserviceaccount.com --set-secrets=...
gcloud scheduler jobs create http kel-ingest-nightly ...   # or event-triggered

# Cloud Armor: WAF + per-IP throttles (rate-limit availability in Standard tier not confirmed; verify)
gcloud compute security-policies create kel-edge
gcloud compute security-policies rules create 1000 --security-policy=kel-edge \
  --expression="evaluatePreconfiguredWaf('sqli-v33-stable',{'sensitivity':1})" --action=deny-403
gcloud compute security-policies rules create 2000 --security-policy=kel-edge \
  --expression="request.path.startsWith('/mcp')" --action=throttle \
  --rate-limit-threshold-count=120 --rate-limit-threshold-interval-sec=60 \
  --conform-action=allow --exceed-action=deny-429 --enforce-on-key=IP
```

The `gcloud run deploy`, tag, and traffic-shift commands follow Google's rollout guide ([rollouts and rollbacks](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)). The Armor thresholds are starting guesses; tune them against real traffic. In the Batch grading job spec, set `provisioningModel: SPOT`, add a `lifecyclePolicies` retry on the preemption exit codes with `maxRetryCount: 3`, and use a **script runnable** on a Docker-capable VM image so the harness can `docker run` each SWE-bench image. Leave image streaming off, because it switches the runtime to containerd ([Batch image streaming](https://docs.cloud.google.com/batch/docs/use-image-streaming)).

CI should run on **GitHub Actions with keyless Workload Identity Federation**. Pin the attribute condition to the stable `repository_id` and `repository_owner_id` claims, not to repo names, which can be renamed or squatted ([google-github-actions/auth](https://github.com/google-github-actions/auth)). The pipeline builds, pushes to Artifact Registry, and deploys by digest. Artifact Registry vulnerability scanning costs **$0.26 per scanned image** ([Artifact Analysis](https://docs.cloud.google.com/artifact-analysis/docs/container-scanning-overview)), so push only on merges to main. Binary Authorization can start as an "images only from our registry" policy and gain attestations when a customer requires them.

## The 2026-07-28 spec makes auth and statelessness launch blockers

The latest MCP revision, **2026-07-28**, defines the server as an OAuth 2.1 resource server. It MUST publish RFC 9728 Protected Resource Metadata (PRM) listing at least one `authorization_servers` entry, and MUST validate that each token was issued for this server as its audience. It "MUST NOT accept or transit any other tokens". Tokens travel only in the `Authorization: Bearer` header and never in the query string ([MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)). The same revision **removed protocol-level sessions and the GET stream endpoint**. A server receiving an old `Mcp-Session-Id` should ignore it and answer GET/DELETE with 405. Every POST must now carry `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name`, and a mismatch between those headers and the request body gets a 400 with `-32020 HeaderMismatch`. The mismatch rule exists to stop "a load balancer routing on the header value while the MCP server executes based on the body value" ([Streamable HTTP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)). Any state handle the server returns must never serve as authentication. It should come from a secure RNG and be keyed as `<user_id>:<handle>`, with the user ID taken from the verified token ([Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)). Dynamic Client Registration (DCR) is now deprecated in favour of Client ID Metadata Documents (CIMD).

The research notes disagree on how current clients behave. The reliability research modelled the 2025-06-18 session lifecycle, and the provider research did not find a revision newer than 2025-11-25. How many clients have adopted 2026-07-28 is unknown. The practical answer covers both cases: **run the Python SDK in stateless mode, move task state to Postgres keyed by user, and negotiate both 2025-11-25 and 2026-07-28**. A stateless server satisfies old clients, which tolerate sessions that are absent or re-initialized, as well as new ones. It also removes the `--workers 1` ceiling and makes canary rollouts meaningful. Anonymous callers have no user to bind a handle to, so any handle they receive must be unguessable and must grant no write capability.

Claude's connector infrastructure is stricter than the spec, and the same infrastructure backs claude.ai, Desktop, mobile, Claude Code and Cowork ([Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)). The design that follows from Claude's rules has six parts:

- **Sign-in trigger.** Claude starts sign-in only on a **401**; it ignores a `WWW-Authenticate` header on a 200. The anonymous-read/authenticated-write split therefore has to be "lazy authentication": read tools answer anonymously, and a write tool call without a valid token gets an HTTP 401 carrying `WWW-Authenticate: Bearer resource_metadata=..., scope="procedures:submit"`. An in-band tool error does not start sign-in. The new `Mcp-Name` header lets the server make that decision early, and HeaderMismatch validation keeps it honest.
- **Authorization server.** Claude uses only the first `authorization_servers` entry. It uses CIMD only when the authorization server advertises `client_id_metadata_document_supported: true` and lists `none` in `token_endpoint_auth_methods_supported`; otherwise it falls back to DCR. It always sends PKCE S256.
- **Redirect URIs.** Register `https://claude.ai/api/mcp/auth_callback` exactly, and also allowlist the `claude.com` variant. Claude Code uses a loopback redirect on an ephemeral port, so match both `localhost` and `127.0.0.1` on any port.
- **Token endpoint.** It must accept form-urlencoded requests, rotate refresh tokens for public clients, and return `invalid_grant` for dead refresh tokens. Discovery, registration and token calls must answer within 10 seconds, and refresh within 30 seconds.
- **Resource URL.** The PRM `resource` must equal the URL exactly as the user types it, including the path.
- **WAF.** Anthropic's egress range is `160.79.104.0/21`, and "a WAF in front of your identity provider can break the flow". Use that range only as an Armor exception on the auth and discovery paths. Never use it as an allowlist for `/mcp`, which Claude Code and Cursor reach from users' own machines.

Anthropic also advises preferring CIMD over DCR at volume, because "DCR causes Claude to register a new client on every fresh connection". OpenAI likewise recommends CIMD and still supports DCR and predefined clients ([OpenAI MCP docs](https://developers.openai.com/api/docs/mcp)), and VS Code supports both ([den.dev](https://den.dev/blog/cimd-vs-code-mcp/)).

A 2–4 person team should not build its own authorization server. **WorkOS AuthKit is free to 1M monthly active users** ([WorkOS changelog](https://workos.com/changelog/introducing-authkit-and-user-management)). Stytch Connected Apps offers "full DCR and CIMD support" free for the first 10,000 active users and agents ([Stytch](https://stytch.com/connected-apps)). Auth0's "Auth for MCP" is GA ([Auth0](https://auth0.com/blog/auth0-auth-for-mcp-servers-generally-available/)). With an external authorization server, the Starlette app only validates JWTs: signature via JWKS, plus `iss`, `aud` equal to the canonical `/mcp` URL, `exp`, `nbf`, and a per-tool scope check. Token settings should be:

- access tokens that live 5–15 minutes;
- rotating refresh tokens with reuse detection;
- three narrow scopes (`procedures:submit`, `runs:report`, and optionally `procedures:read`), with no wildcard and no `offline_access` in `scopes_supported`;
- a 403 `insufficient_scope` step-up challenge when a token lacks the scope for a tool.

These values are inferences grounded in the spec's SHOULD for short-lived tokens and MUST for rotating public-client refresh tokens ([Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)).

Headless clients such as CI, the local executor and Codex's `bearer_token_env_var` need personal agent keys. Give them a detectable prefix (e.g. `kel_…`) and store them hashed, scoped and expiring. Stripe offers a template for retiring broader keys later: from **31 Oct 2026** its MCP server rejects full-access keys with a 401 plus an OAuth discovery challenge, a dated deprecation whose failure a machine can act on ([Stripe MCP](https://docs.stripe.com/mcp)).

One existing piece of the codebase is already correct and one is not. `server.py` configures `TransportSecuritySettings` explicitly with the public origin. That matters because an open Python SDK issue shows the middleware defaults to **no Host/Origin validation** on non-loopback binds ([python-sdk #3562](https://github.com/modelcontextprotocol/python-sdk/issues/3562)), while the spec requires a 403 on an invalid Origin. Keep a regression test on that setting. The custom routes currently return a bare `WWW-Authenticate: Bearer`. That header needs `resource_metadata=` before any Claude client can discover the authorization server.

## Stored procedure text is the attack surface that matters most

OWASP's MCP Top 10 (beta) and LLM Top 10 (2025) together cover every attack class this product faces: tool poisoning, prompt injection via contextual payloads, token mismanagement, scope creep, supply-chain tampering, insufficient authN/authZ, and data and model poisoning ([OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10/); [OWASP LLM Top 10](https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/)). Kel sits at an unusual point in this taxonomy. Its **output is the payload**: agents execute procedure steps, and `submit_way` and `report_discovery` let outside parties author that output. That is a stored, cross-user version of the attack in which a malicious public GitHub issue hijacked agents into leaking private repos ([Invariant Labs](https://invariantlabs.ai/blog/mcp-github-vulnerability)). It is also a "rug pull", where content changes after the user has come to trust it ([Invariant Labs tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)).

GitHub's eventual response is the right model to follow, and its limits matter too. It turned on content sanitization by default (filtering invisible Unicode, HTML and code fences) and added a Lockdown mode that serves public content only from trusted authors ([GitHub changelog 2025-12-10](https://github.blog/changelog/2025-12-10-the-github-mcp-server-adds-support-for-tool-specific-configuration-and-more/)). GitHub itself calls lockdown best-effort, "not an authorization boundary" ([server-configuration.md](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md)). For Kel, the controls are:

- **Quarantine unverified submissions.** They are visible only to the submitting tenant until they pass verification.
- **Screen every write.** Run the existing V0 gate and the redaction chokepoint, then an injection classifier. Flag agent-directed imperatives ("ignore previous"), `curl … | sh` pipelines, and fetch-and-execute URLs.
- **Serve procedures as data.** Delimit them clearly and attach provenance, version, and a **content hash**, so an agent pinned to a version cannot be silently changed.
- **Keep tool descriptions static and versioned.** Never change their meaning through `list_changed`.

Anthropic now makes a prompt-injection policy acknowledgment mandatory for any directory listing ([Claude submission](https://claude.com/docs/connectors/building/submission)).

Tenant isolation is the second risk, and Asana is the precedent. Its new MCP layer had an access-control flaw that exposed one organisation's projects and tasks to others. The server was **offline June 5–17, 2025**, about 1,000 customers were potentially affected, and every MCP connection had to be reset ([BleepingComputer](https://www.bleepingcomputer.com/news/security/asana-warns-mcp-ai-feature-exposed-customer-data-to-other-orgs/); [The Register](https://www.theregister.com/security/2025/06/18/asana-mcp-server-back-online-after-plugging-a-data-leak-hole/1199951)). Reports conflict on the exact exposure window. The lesson is that **MCP handlers must reuse the core scoping path** (`scope_predicates()`, `tenant_transaction()`, RLS) and never a parallel query path. Row-level security (RLS) is only a real backstop under certain conditions. PostgreSQL states that superusers and `BYPASSRLS` roles "always bypass the row security system", table owners bypass it unless `FORCE ROW LEVEL SECURITY` is set, and FK and unique checks bypass RLS entirely and can act as covert channels ([PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)). PgBouncer in transaction mode does not discard session state, so a plain `SET` leaks the tenant to the next borrower ([Seedfast](https://seedfa.st/blog/pgbouncer-transaction-mode)). The repo's transaction-local binding already handles that. The remaining RLS work is:

- **Role and table settings.** A non-owner, `NOBYPASSRLS` app role; FORCE RLS on every tenant table; `security_invoker` views; audited `SECURITY DEFINER` functions.
- **A CI leak suite** that:
  - seeds two tenants and asserts zero cross-tenant rows from every tool;
  - asserts anonymous callers see only commons rows;
  - checks `pg_roles` for bypass flags;
  - checks the catalog for tables with RLS turned off.
- **A launch kill switch** plus a "revoke all tokens" capability, since the Asana remediation needed exactly that.

Two narrower risks also need controls. **SSRF** appears wherever the server or its authorization server fetches a URL, such as CIMD documents. The spec lists the private ranges to block, says to validate every redirect hop and pin DNS, and says to "avoid implementing IP validation manually" ([Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)). The **local executor** is effectively remote code execution by design. Running agent CLIs in auto-approve mode against remotely sourced procedures hands an injected procedure the user's credentials. Claude Code's sandbox confines writes to the working directory. In a linked worktree it also denies writes to the shared `.git`'s `hooks/` and `config`, and strict mode (`allowUnsandboxedCommands: false`) removes the escape hatch. Anthropic still says it "is not a complete isolation boundary". It is also **unsupported on native Windows**, and its default read policy still allows `~/.ssh` and `~/.aws` ([Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)). The executor should therefore:

- run in a disposable container or WSL2/Docker, with default-deny egress;
- carry no ambient credentials and mount only the worktree;
- pin procedure versions by hash;
- never auto-merge, which matches the repo's named-human-approval rule;
- bind its local status surface to loopback only, behind a token.

On supply chain, the ecosystem's incidents make the case directly:

- the malicious postmark-mcp npm clone ([IT Pro](https://www.itpro.com/security/a-malicious-mcp-server-is-silently-stealing-user-emails));
- Smithery's registry leaking a Fly.io token that controlled 3,000+ apps ([GitGuardian](https://blog.gitguardian.com/breaking-mcp-server-hosting/));
- **CVE-2025-6514 in mcp-remote** (CVSS 9.6, fixed in 0.1.16) ([JFrog](https://jfrog.com/blog/2025-6514-critical-mcp-remote-rce-vulnerability/)).

That last one matters if the npm installer bridges Claude Desktop through mcp-remote; pin a patched version. The baseline for a small team is:

- GitHub push protection and secret scanning ($19 per active committer per month on private repos, via Secret Protection) ([GitHub changelog](https://github.blog/changelog/2025-03-04-introducing-github-secret-protection-and-github-code-security/));
- gitleaks as a pre-commit hook;
- hash-locked Python dependencies;
- Actions pinned by SHA, with `permissions: {}` as the default;
- OIDC deploys;
- `actions/attest-build-provenance` for the image, the npm installer and the `stealthlab-connect` wheel, which gives **SLSA Build L2**, or L3 through reusable workflows ([GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)).

The untracked `backend/.env.bak.*` file in the current working tree shows the `.gitignore` pattern should widen to `.env*`.

## Reliability means 99.5%, a judge-coverage SLI and a kill switch

Google's SRE workbook says to set first SLOs from measured performance rather than aspiration, on a **four-week rolling window**, with a written error-budget policy ([SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/)). Over 28 days, a 99.9% SLO leaves about 40 minutes of budget and 99.5% leaves about 3.4 hours. Neon Launch carries no SLA and the judge chain depends on third-party LLMs ([Neon pricing](https://neon.com/pricing)), so **99.0% internally at alpha and 99.5% at public beta** are honest targets. A 99.9% transport SLO becomes credible only after the service is stateless, has multiple instances, and runs on Neon Scale. The key design move is a separate **judge-coverage SLI** of the form "≥95% of tool calls return a checked verdict". A `not_checked` response counts as a success for availability but not for coverage. Without that split, an LLM outage either burns the availability budget unfairly or disappears from view. Neither signal can come from Cloud Run's built-in metrics, because JSON-RPC errors arrive inside HTTP 200 responses. Both need a custom OTel or log-based metric.

Low traffic breaks ratio-based alerting. At 10 requests an hour, one failure produces a 10% error rate. The workbook's remedy is synthetic traffic plus client-side resilience ([SRE Workbook: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)). Run a synthetic MCP probe every 1–5 minutes that does initialize, then `tools/list`, then a cheap `tools/call`, and page after N consecutive failures. Once volume allows, move to Cloud Monitoring burn-rate alerts: a fast burn at 10× over 1–2 hours and a slow burn at 2× over 24 hours ([Cloud Monitoring burn rate](https://docs.cloud.google.com/stackdriver/docs/solutions/slo-monitoring/alerting-on-budget-burn-rate)). Tracing should follow the OTel MCP semantic conventions: span names like `tools/call find_ways`, attributes `mcp.method.name` and `gen_ai.tool.name`, and the metric `mcp.server.operation.duration`. Those conventions are still at **Development** status and have moved to a separate `semantic-conventions-genai` repo, so pin versions. The conventions flag tool arguments and results as sensitive ([OTel MCP semconv](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md)), so never record them in production, and route any free-text log field through `redact_event`. Logging stays inside the 50 GiB/month free tier at this scale ([Cloud Logging pricing](https://cloud.google.com/blog/topics/cost-management/how-to-approach-cloud-logging-pricing-for-cloud-admins)).

LLM-outage resilience comes down to a disciplined wrapper around every judge call:

- **Timeouts and deadline.** A hard per-attempt timeout (20–30 s), inside a whole-call deadline (e.g. 60 s) that is shorter than both Cloud Run's and the client's timeouts.
- **Retries.** At most one retry per provider, with full-jitter backoff. Retry only 429, 5xx and timeouts, and honour `Retry-After`. Retry in the judge layer only, because retries at several layers multiply ([SRE cascading failures](https://sre.google/sre-book/addressing-cascading-failures/)).
- **Circuit breaker.** After 3–5 failures a minute, cool the provider down for 30–60 s.
- **Bulkhead.** Cap in-flight calls per provider so one slow provider cannot eat concurrency slots or the DB pool.
- **Fallback order.** Order providers so no two consecutive ones share a failure domain.
- **Caching.** Cache verdicts in Postgres keyed on (input hash, judge model, prompt version).
- **Kill switch.** A DB-row flag that forces `not_checked` instantly, without a redeploy.

LiteLLM's Router supplies most of this in-process: `num_retries`, `allowed_fails`, `cooldown_time`, per-error `RetryPolicy`, and per-deployment `max_parallel_requests` ([LiteLLM routing](https://docs.litellm.ai/docs/routing)). Hosted gateways such as Portkey or Cloudflare AI Gateway add analytics and caching ([Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/)), but they also add another vendor and network hop in the critical path, so defer them. Record which judge answered on every result. Provenance is the product.

Safe rollout on Cloud Run follows a fixed sequence:

1. Deploy with `--no-traffic --tag`.
2. Run the scripted MCP smoke test against the tag URL.
3. Shift traffic 5% → 25% → 50% → 100%.
4. To roll back, pin the previous revision to 100%.

Two caveats apply: traffic splits persist across deploys until reset with `--to-latest`, and affinity distorts splits ([rollouts](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)). Pair this with the repo's existing additive, idempotent migrations in expand/contract order. Add a CI job that applies pending migrations to a fresh **Neon branch of production** before each deploy.

For backups, Neon's instant restore retains history for 6 hours on Free, up to 7 days on Launch, and up to 30 days on Scale. Restoring works by re-pointing the compute to a point-in-time branch ([Neon branch restore](https://neon.com/docs/introduction/branch-restore)). The Free window is too short to notice most logical corruption. The plan is:

- **Targets.** RPO ≤5 minutes through point-in-time recovery; RTO ≤1 hour.
- **Off-provider copy.** A nightly `pg_dump` to a versioned GCS bucket, against Neon account loss.
- **Monthly drill.** Restore to a branch at T-24h, run the schema-drift and smoke tests, and record the measured RTO. A `pg_dump` restore must rebuild the HNSW indexes, and that rebuild dominates the time.

Load testing should replay the real MCP lifecycle with one session per virtual user. Locust fits the Python stack, and Microsoft has published an MCP-native Locust harness. It notes that agent traffic "arrives in bursts, sustains long sessions, and hits the same handful of tools" ([Microsoft Tech Community](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/load-testing-hosted-mcp-servers-with-locust-and-azure-load-testing/4522691)). Stub the judges with a latency-injecting fake. Run four test shapes: ramp, 10× burst, a 1–2 hour soak (which catches in-memory store leaks), and a chaos run where the fake returns 429s and hangs.

## Market leaders converge on OAuth, read-only URLs and few tools

The large hosted MCP servers (GitHub, Stripe, Linear, Supabase, Atlassian, Notion) have converged on the same shape. Each serves Streamable HTTP at `/mcp` with OAuth, keeps a bearer-token fallback for CI, offers a read-only variant, and keeps the tool count small. Linear serves `/mcp/readonly` and keeps `/sse` only as a deprecated fallback ([Linear MCP](https://linear.app/docs/mcp)). Supabase's `read_only=true` runs queries as a read-only database user, **enforcing read-only mode at the data layer rather than by hiding tools** ([Supabase MCP](https://supabase.com/docs/guides/getting-started/mcp)). Stripe exposes about ten tools, including generic read and write tools over an allowlisted API. For risky writes it returns an approval URL that a human must open, and admins can enable MCP per environment and revoke OAuth sessions from a dashboard ([Stripe MCP](https://docs.stripe.com/mcp)). GitHub ships experimental features behind an `/insiders` path on the same endpoint and uses OAuth scope challenges instead of failing tools up front ([GitHub remote server](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md); [GitHub changelog 2026-01-28](https://github.blog/changelog/2026-01-28-github-mcp-server-new-projects-tools-oauth-scope-filtering-and-new-features/)). Atlassian shows what goes wrong. After a published 30 June 2026 SSE sunset, its old endpoint kept working, and every tool result carried a deprecation banner with no machine-actionable migration path ([atlassian-mcp-server #212](https://github.com/atlassian/atlassian-mcp-server/issues/212)). **Never put operational notices inside tool output**, because agents read that text as context.

For Kel this means:

- ship Streamable HTTP only, with no SSE, since there are no legacy clients;
- offer a `/readonly` URL enforced by the anonymous DB scope;
- serve experiments from a `/beta` path;
- treat tool names and input schemas as a public API: additive changes only, and aliases plus dated removals when something must go.

Few providers publish MCP rate limits or SLAs; Notion's reported 180 requests/min per user comes from a secondary source ([StackOne](https://www.stackone.com/blog/notion-mcp-deep-dive/)). Publishing Kel's own limits is a cheap way to stand out.

Distribution runs through four channels:

- **Official MCP Registry.** It holds metadata only and is still in preview, with possible "breaking changes or data resets" ([MCP Registry quickstart](https://modelcontextprotocol.io/registry/quickstart)). Publish a `server.json` under a DNS-verified namespace such as `dev.kel/kel` with a `remotes` entry of type `streamable-http` ([remote servers](https://modelcontextprotocol.io/registry/remote-servers)). Several third-party directories reportedly ingest from it.
- **Anthropic Connectors Directory.** It accepts submissions from any paid Claude plan. It requires HTTPS, OAuth for authenticated services, and a `title` plus `readOnlyHint` or `destructiveHint` on every tool. It also requires a privacy policy, a support contact, a reviewer test account with populated data, and seven policy acknowledgments. Submissions are auto-scanned and listed as Community by default ([Claude submission](https://claude.com/docs/connectors/building/submission)). After listing, tool changes ship by deploying, with no resubmission, and the listing gets a health badge and usage metrics ([after publishing](https://claude.com/docs/connectors/building/after-publishing)). Annotate `find_ways` and `recommend_models` as read-only. Mark the three write tools as non-destructive, since they append rather than delete, which matches the repo's invalidate-and-append semantics.
- **The npm installer.** It duplicates Stripe's `stripe agent setup`. It should prefer each client's own CLI (`claude mcp add --transport http`, `codex mcp add --url`) over rewriting config files, and should also emit a Cursor deeplink (`cursor://anysphere.cursor-deeplink/mcp/install?name=…&config=<base64>`, per [Cursor install links](https://cursor.com/docs/context/mcp/install-links)) and a VS Code redirect link. Cursor deeplinks reportedly fail on some Linux platforms ([Cursor forum](https://forum.cursor.com/t/cant-install-mcp-servers-with-deeplinks-on-debian/114195)), so keep a manual fallback.
- **A Claude Code plugin.** Local components now ship only inside plugins, since MCPB desktop-extension listings are deprecated. Package the MCP config and the survey-repo skill as a plugin, bump its `version` on each release, and watch its error-rate panel.

## CERT-In applies now, DPDP in 2027, GDPR from the first EU user

The obligation already in force is the one most founders miss. Since June 2022, **CERT-In Directions require reporting specified cyber incidents within 6 hours and keeping ICT logs for a rolling 180 days within Indian jurisdiction** ([CERT-In Directions](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf)). A US-hosted stack should therefore ship a copy of access and security logs to an `asia-south1` bucket; get counsel to confirm whether that satisfies the directions. The DPDP Rules were notified in November 2025, with substantive duties from **13 May 2027**: itemised plain-language notice, affirmative consent, security safeguards, logs kept at least a year, notice to users and the Board without delay plus a detailed report within 72 hours, erasure, and treating under-18s as children ([DLA Piper: India](https://www.dlapiperdataprotection.com/?t=law&c=IN)). In January 2026 MeitY proposed compressing that window to November 2026 ([Business Standard](https://www.business-standard.com/technology/tech-news/meity-may-cut-compliance-timeline-for-key-dpdp-rules-to-12-months-126012201293_1.html)). The research could not confirm whether the change was adopted, which is a point for counsel. Penalties reach **INR 250 crore** for failing to take reasonable security safeguards. Cross-border transfer follows a negative-list model, so US hosting is fine unless the US is restricted. Building to DPDP now is cheaper than retrofitting, and an 18+ ToS avoids the parental-consent regime.

GDPR almost certainly applies under Art. 3(2) once EU developers are targeted ([EDPB 3/2018](https://www.edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_3_2018_territorial_scope_after_public_consultation_en_1.pdf)). The "occasional processing" exemption is read narrowly, so a continuous SaaS needs an **Art. 27 EU representative**, which outsourced services provide. EU users sending data directly to an Indian company is not itself a Chapter V transfer. Kel's onward flows to Neon, Google and US LLM APIs are transfers, though ([EDPB 05/2021](https://www.edpb.europa.eu/system/files/2023-02/edpb_guidelines_05-2021_interplay_between_the_application_of_art3-chapter_v_of_the_gdpr_v2_en_0.pdf)). Rely on vendors' Data Privacy Framework (DPF) certification and SCCs. The DPF survived the General Court in September 2025, but an appeal is pending at the CJEU ([WilmerHale](https://www.wilmerhale.com/en/insights/blogs/wilmerhale-privacy-and-cybersecurity-law/20251201-european-court-of-justice-to-review-challenge-to-eu-us-data-privacy-framework)), so pick vendors whose DPAs already include SCCs. Neon provides a DPA and a subprocessor list ([Neon DPA](https://neon.com/dpa)). The privacy policy must describe LLM handling accurately. Vertex AI caches content for up to 24 hours by default and may log prompts for abuse detection. Zero data retention requires disabling the cache and obtaining an abuse-monitoring exception ([Vertex data governance](https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance?authuser=0)). Third-party OpenAI-compatible providers are the weakest link: send them only redacted text, and exclude any provider without a DPA from business traffic.

In the US, CCPA's **$26.625M revenue threshold** puts it out of reach at launch ([Clym](https://www.clym.io/blog/ccpa-applicability-guide)). The live US risk is the FTC. Its August 2025 Workado order found a claimed "98 percent accurate" AI detector scored about 53% in the FTC's testing. The order bars efficacy claims unless "competent and reliable evidence" backs them at the time they are made ([FTC](https://www.ftc.gov/news-events/news/press-releases/2025/08/ftc-approves-final-order-against-workado-llc-which-misrepresented-accuracy-its-artificial)). Every SWE-bench number Kel publishes therefore needs a retained run artefact, and a benchmark gain must never be generalised into "makes your agent X% better". Under the EU AI Act, Kel is not a general-purpose AI model provider, and LLM-judging of submissions is not an Annex III high-risk use. Article 50 transparency duties started 2 Aug 2026, and the Digital Omnibus set **2 Dec 2026** as the Art. 50(2) marking deadline for systems already on the market ([Mayer Brown](https://www.mayerbrown.com/en/insights/publications/2026/07/eu-ai-act-news-digital-omnibus-on-ai-new-guidance-on-risk-classification-gpai-and-transparency-obligations); [Article 50](https://artificialintelligenceact.eu/article/50/)). A `"generated_by": "ai"` field on judged outputs is a cheap, defensible posture. It is uncertain whether marking is expected for output consumed by agents.

### What organisations will ask of the vendor

A buyer evaluating Kel will ask for several things:

- a completed security questionnaire, which becomes a SOC 2 or ISO 27001 request once deals reach mid-market;
- a DPA with SCCs and a subprocessor list with change notification;
- evidence of tenant isolation, which after Asana will be pointed;
- admin controls: SSO, org-wide enable/disable, and listing and revoking sessions, all of which Stripe already offers;
- a data-layer read-only mode;
- tool-call audit logs;
- zero data retention with LLM subprocessors, and data-residency answers;
- a status page and a written incident-notification commitment.

SOC 2 is never legally required. For a 10–50 person company, a Type I costs roughly **$28–58k all-in over 14–22 weeks**, with the auditor alone at $5–12k ([Atlant Security](https://atlantsecurity.com/blog/soc-2-type-1-timeline-cost-startup-2026)). Automation platforms run about $6–15k/year and do not shorten the audit. Defer the audit until a deal requires it, but start the evidence habits now: MFA and SSO everywhere, quarterly access reviews, logs kept a year or more, tested restores, and a written incident plan with 6-hour and 72-hour clocks. Those habits let a Type II observation window start the day a buyer asks. Enterprise SSO through WorkOS starts at about $125/month per connection ([WorkOS pricing](https://workos.com/pricing)), so price it into team plans.

## Launch checklist by stage

| Stage | Must have | Why it gates |
|---|---|---|
| **Before private alpha** | Task store moved to Postgres keyed `<user_id>:<handle>` (or `max-instances=1` documented as the capacity limit); stateless-mode Streamable HTTP; explicit Host/Origin allowlist test | Spec 2026-07-28 removes sessions; SDK default ships no Origin validation |
| | External authorization server (WorkOS/Stytch/Auth0) with PRM, HTTP 401 + `resource_metadata` on write tools, CIMD + DCR, S256, Claude callbacks, loopback any port, JWT `aud`/`scope` checks | Claude will not start sign-in any other way |
| | Unverified submissions quarantined to their tenant; write-path redaction + injection screen; content hashes on served procedures | Stored prompt injection is the core product risk |
| | CI cross-tenant leak suite; app role `NOBYPASSRLS` + FORCE RLS; kill switch and revoke-all-tokens | Asana precedent |
| | Judge wrapper: timeouts, one retry, breaker, bulkhead, `not_checked` kill-switch flag | LLM outages must not become MCP outages |
| | Dedicated service accounts, Secret Manager, WIF deploys by digest, push protection + gitleaks | No long-lived keys in the pipeline |
| | Neon Launch (7-day PITR), nightly `pg_dump` to GCS, one restore performed | Free plan's 6 h window is too short |
| | Synthetic MCP probe + alerting to Slack/email; OTel traces with no payloads | Low traffic makes ratio alerts useless |
| | ToS/AUP (18+), accurate privacy policy, grievance contact, CERT-In 6 h incident path, vendor inventory, telemetry opt-in; no unsubstantiated benchmark claims | CERT-In and FTC apply today |
| **Before public beta** | Global ALB + Cloud Armor (WAF + per-IP throttles; Anthropic range exempted on auth paths); ingress locked to LB | Anonymous reads are a cheap DoS/cost vector |
| | Multi-instance service; canary rollouts with MCP smoke test; migrations tested on a Neon branch | Safe deploys need statelessness |
| | 99.5%/28-day availability SLO + judge-coverage SLI; burn-rate alerts; weekly rotating on-call; status page with a "judge verification" component; runbooks (LLM outage, DB restore, bad deploy, leaked key) | First external users |
| | Neon Scale (30-day PITR, SLA); monthly restore drill with measured RTO | Uptime SLA needs a DB SLA |
| | Load test (ramp, burst, soak, chaos) with stubbed judges | Find limits before users do |
| | Registry `server.json` under DNS namespace; Connectors Directory submission (annotations, reviewer account); plugin + deeplinks; published rate limits; `/readonly` and `/beta` paths | Distribution |
| | EU Art. 27 representative; subprocessor page; DPA template with SCCs; erasure/export flow; 72 h breach runbook; India log copy; AI-generated marker; Vertex zero data retention; lawyer review of ToS/Privacy/DPA | GDPR, DPDP, AI Act Art. 50 |
| | Build provenance attestations (SLSA L2) for image, npm installer and wheel; local executor defaults to container/WSL2 with egress deny | Supply chain, executor RCE |
| **Later (on pull)** | SOC 2 Type I → II or ISO 27001; pen test; Binary Authorization attestations; SSO for teams; multi-region NEGs; LLM gateway; Cloud SQL/AlloyDB re-evaluation; DPIA if shared-corpus reuse grows; re-check DPF ruling and DPDP dates | Buyer- or scale-driven |

## Low-traffic monthly cost estimates

These use list prices from the research notes, a 730-hour month, and a Tier-1 US region. Several unit prices came from search snippets of JavaScript-rendered pages, so confirm them in the [GCP pricing calculator](https://cloud.google.com/products/calculator). LLM judge spend and grading VMs scale with usage and are excluded.

| Item | Basis | Alpha | Beta |
|---|---|---|---|
| Cloud Run MCP service | 1 vCPU/2 GiB, min 1, instance-based at $0.000018/vCPU-s + $0.000002/GiB-s, minus free tier ([Cloud Run pricing](https://cloud.google.com/run/pricing)) | ~$53 | ~$53–110 (1–2 instances) |
| Cloud Run Job (ingestion) | 2 vCPU/4 GiB, 3 h/day | ~$14 | ~$14 |
| Global ALB | $0.025/h forwarding rule ([LB pricing](https://cloud.google.com/load-balancing/pricing)) | — (use `run.app`) | ~$18 |
| Cloud Armor Standard | $5/policy + $1/rule + $0.75/M requests ([Armor pricing](https://cloud.google.com/armor/pricing)) | — | ~$11–14 |
| Neon | Launch 0.25 CU always-on at $0.106/CU-h + 10 GB at $0.35; Scale at $0.222/CU-h ([Neon](https://neon.com/pricing)) | ~$20–25 | ~$45 |
| Artifact Registry + scanning | $0.26/scanned image, ~30 pushes | ~$8 | ~$8–16 |
| Secret Manager, Logging, Trace | $0.06/secret version; logs under 50 GiB free | ~$1–5 | ~$1–5 |
| GCS backups + India log copy | a few GB | ~$1 | ~$1–2 |
| Identity provider | WorkOS AuthKit free to 1M MAU | $0 | $0 |
| **Total (infra)** | | **~$100** | **~$150–190** (to ~$230 with two always-on instances) |
| Batch grading | Spot VMs only while grading; orchestration free | usage | usage |

The one-off and non-infrastructure costs sit outside this table: GitHub Secret Protection on private repos at $19 per committer per month, an EU representative, the lawyer review, and a status-page subscription. The research did not price the last three.

## Conclusion

Kel's release plan is unusual in one way: the product's reliability and its security depend on the same design decision. Making the server stateless, with state held in Postgres and bound to users, is what the 2026-07-28 spec requires. It is also what lets Cloud Run scale and canary safely, and what closes the session-hijacking class. Keeping unverified submissions inside their own tenant is the injection defence, and it is also what makes the "verified" in "verified procedural memory" true. The judge-coverage SLI does double duty as an honesty mechanism, because it turns `not_checked` from a silent fallback into a published number. That matters to the FTC and to enterprise buyers alike. The spend that matters at this stage is engineering time on those three changes, not infrastructure. Hosting costs about the same as one SaaS seat, while one cross-tenant leak cost Asana its MCP product for twelve days.

The main open risks are external. Clients may adopt the stateless 2026-07-28 transport unevenly. The DPDP deadline may move earlier. The DPF may fall at the CJEU. No authority has said whether text consumed by agents needs AI Act marking. Each of these deserves a named owner and a calendar check, not an up-front build. The one early legal spend with the highest leverage is a counsel opinion on three points: CERT-In log localisation, cross-user reuse of submitted discoveries, and the current DPDP date.
