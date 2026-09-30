# GCP managed-service architecture for a production Streamable-HTTP MCP server (as of Sept 2026)

Scope: Python Starlette/uvicorn MCP server (Streamable HTTP on /mcp, optional SSE), 5 tools, anonymous reads + token-auth writes; Postgres+pgvector (currently Neon, sharded across many projects); ingestion worker as Cloud Run Jobs; LLM judge calls (Vertex Gemini + OpenAI-compatible third parties); Docker-based SWE-bench grading batch. Quotas: CE 12 vCPU global; Cloud Run 20 vCPU / 40 GiB per region; 3 regions.

Research date: 2026-09-27. Pages on docs.cloud.google.com (cloud.google.com/run/docs now 301-redirects there) were marked "last updated 2026-09-24" where noted. Several pricing pages (cloud.google.com/*/pricing) are JS-rendered and could not be fetched directly; those prices come from search snippets that quote the official page or from third-party trackers, and are flagged accordingly.

## Q1. Compute: Cloud Run vs GKE Autopilot vs App Engine for a Streamable HTTP MCP server

### Takeaway
Cloud Run is the Google-documented home for remote MCP servers: it officially supports streamable HTTP and SSE MCP transports (not stdio), up to 60-minute requests, up to 1,000 concurrent requests per instance, HTTP/2 and WebSockets. Its weak points for MCP are best-effort session affinity and the 60-minute ceiling on any single stream, so the server should be (or be made) stateless or keep session state in Postgres.

### Cited Findings
- Google's official "Host MCP servers on Cloud Run" guide (last updated 2026-09-24): "Cloud Run supports hosting MCP servers with streamable HTTP transport, but not MCP servers with stdio transport"; SSE and streamable HTTP are the supported transports. Deploy via `gcloud run deploy --image IMAGE_URL --port PORT` or `gcloud run deploy --source .` (Python/Node supported for source deploys). — [Cloud Run: Host MCP servers](https://docs.cloud.google.com/run/docs/host-mcp-servers)
- Same guide, auth recommendations: for local MCP clients, either (1) require IAM `run.invoker` and run the **Cloud Run proxy** locally ("securely expose the remote MCP server to your client using your own credentials"), or (2) mint an **OIDC ID token** whose audience is the service URL. For MCP clients running on Cloud Run: sidecar (`http://localhost:PORT`), service-to-service auth, or Cloud Service Mesh. The guide does not cover session affinity, timeouts, or scaling specifically for MCP. — [Cloud Run: Host MCP servers](https://docs.cloud.google.com/run/docs/host-mcp-servers)
- Request timeout: default 300 s, max 3,600 s (60 min). For timeouts >15 min Google recommends retries and tolerating client reconnects, as connection loss becomes more likely. — [Cloud Run request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout)
- WebSockets/streams are HTTP requests and remain subject to the request timeout; don't enable HTTP/2 end-to-end when using WebSockets. — [Using WebSockets on Cloud Run](https://docs.cloud.google.com/run/docs/triggering/websockets)
- WebSockets, HTTP/2 and gRPC bidirectional streaming are GA on Cloud Run (older blog, circa 2021). — [Google Cloud Blog: Cloud Run gets WebSockets, HTTP/2 and gRPC bidi streams](https://cloud.google.com/blog/products/serverless/cloud-run-gets-websockets-http-2-and-grpc-bidirectional-streams)
- Max concurrency is 1,000 requests per instance (default 80). Responses larger than 32 MB can be streamed using chunked transfer encoding. — [Cloud Run release notes](https://docs.cloud.google.com/run/docs/release-notes); [Using WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets)
- Session affinity: cookie-based, cookie TTL 30 days, **best effort**. It breaks if the instance terminates or hits max concurrency/CPU; "you cannot assume that a client will always reconnect to the same instance"; it takes precedence over traffic splitting and can skew splits. — [Cloud Run session affinity](https://docs.cloud.google.com/run/docs/configuring/session-affinity)
- Autoscaling: scales to zero by default. Requests pend for up to 3.5x average container startup time or 10 s (whichever is greater) before a 429. Scaling uses CPU and concurrency targets (default 60%). Adaptive concurrency keeps CPU below 90%. Cloud Run may briefly exceed max-instances (up to ~15 min) for in-flight requests. For services doing background work, Google recommends min-instances >= 1. — [Cloud Run instance autoscaling](https://docs.cloud.google.com/run/docs/about-instance-autoscaling)
- Billing modes: **request-based** (default; per-request fee plus higher per-second rates, charged only while serving) vs **instance-based** (whole instance lifetime billed, no per-request fee, lower rates: $0.000018/vCPU-s + $0.000002/GiB-s; free tier 240,000 vCPU-s + 450,000 GiB-s per month per billing account). Snippet claims these rates were verified 2026-09-07. — [Cloud Run pricing (via search snippet)](https://cloud.google.com/run/pricing); [cloudcostkit guide](https://cloudcostkit.com/guides/gcp-cloud-run-pricing/)

### Inferences
- **Recommended Cloud Run settings for the MCP service:** `--min-instances=1` (removes cold start, keeps any in-memory MCP session / TasksExtension-style state alive), `--max-instances` sized to the 20-vCPU regional quota (e.g., 4-8 at 1 vCPU so a runaway can't starve Jobs), `--cpu=1 --memory=2Gi`, `--concurrency=40-80` (Python async; lower than 1,000 because one uvicorn worker shares one CPU), `--timeout=3600` only if long-lived SSE GET streams are used, otherwise ~300 s. Use instance-based billing (`--no-cpu-throttling`) since min-instances=1 bills the instance continuously anyway, and background tasks keep CPU.
- **Session state:** because affinity is best effort, a stateful Streamable-HTTP server (Mcp-Session-Id held in memory) will break on scale-out or instance recycling. Either run the MCP SDK in stateless mode (`stateless_http=True` in the Python SDK) or persist session/task state in Postgres. With min=1 and max=1 it works but gives up HA. (Relevant to this repo: CLAUDE.md notes the MCP server's TasksExtension store is in-memory and `--workers 1` is load-bearing — this conflicts with Cloud Run horizontal scaling and should be externalized before max-instances > 1.)
- **Client streams must reconnect:** the 60-min cap means SSE GET streams must be resumable (Last-Event-ID) and clients must reconnect.
- **GKE Autopilot**: no hard request timeout and true sticky sessions are possible, but it adds cluster fee and operational surface; not justified for 5 tools at low traffic. **App Engine** is effectively legacy for new work (Google steers new apps to Cloud Run). (Both are inferences; see Gaps.)
- For anonymous reads + token writes, the IAM-invoker model in Google's guide doesn't fit public anonymous access: deploy with `--allow-unauthenticated` (or `--no-invoker-iam-check`) and enforce write tokens in the app, with Cloud Armor rate limiting in front (Q2).

### Gaps
- Did not fetch GKE Autopilot pricing (cluster management fee, per-pod pricing) or App Engine limits in this pass; the recommendation against them is judgment, not a sourced comparison.
- Exact request-based billing rates and the discounted "idle" rate for min-instances under request-based billing were not retrievable (pricing page JS-rendered).
- Cloud Run cold-start numbers: no official figure found.
- Google Cloud blog posts on MCP on Cloud Run (e.g., codelabs, "build and deploy a remote MCP server") were not fetched; the docs guide above is the primary reference.

## Q2. Global external Application Load Balancer + serverless NEG + Cloud Armor

### Takeaway
An external ALB with a serverless NEG adds a Google-managed cert on a custom domain, Cloud Armor WAF/rate limiting/L7 DDoS, and multi-region failover, for roughly $18/month (forwarding rule) + ~$10-15/month (Armor Standard) at low traffic. Since Cloud Run's own domain mapping is still Preview and "not recommended for production," the LB is the production-grade path to a custom domain.

### Cited Findings
- Load balancer pricing: first 5 forwarding rules $0.025/hour; inbound data processed $0.008/GB. With serverless NEG backends, LB charges apply in addition to Cloud Run compute, **but Cloud Run data-transfer charges do not apply** to requests passed from an external ALB via serverless NEG. — [Cloud Load Balancing pricing (via search snippet)](https://cloud.google.com/load-balancing/pricing); [Serverless NEG concepts](https://docs.cloud.google.com/load-balancing/docs/negs/serverless-neg-concepts)
- Setup guide for global external ALB with Cloud Run backends: [Set up a global external ALB with Cloud Run](https://docs.cloud.google.com/load-balancing/docs/https/setup-global-ext-https-serverless)
- Cloud Armor Standard (pay-as-you-go): $5/policy/month, $1/rule/month, $0.75 per million requests; no commitment or data-processing fee. — [Cloud Armor pricing (via search snippet)](https://cloud.google.com/armor/pricing)
- Cloud Run domain mappings are **Preview**; "due to latency issues, they are not production-ready and are not supported at General Availability"; not recommended for production. Limited to specific regions (asia-east1, asia-northeast1, asia-southeast1, europe-north1, europe-west1, europe-west4, us-central1, us-east1, us-east4, us-west1), 64-char limit, no self-managed certs. — [Mapping custom domains](https://docs.cloud.google.com/run/docs/mapping-custom-domains)

### Inferences
- Worth it even at small scale for a public anonymous-read endpoint: rate limiting (`throttle`/`rate_based_ban` rules keyed on IP) is the main defense against cost amplification — anonymous MCP reads that trigger DB/vector queries are a cheap DoS vector. Suggested policy: 1 policy with ~4-6 rules (preconfigured WAF sqli/xss at low sensitivity, per-IP throttle on /mcp, stricter per-IP rule on write paths, optional geo/deny list) = ~$10-11/month + $0.75/M requests.
- Set Cloud Run ingress to `internal-and-cloud-load-balancing` so the `run.app` URL can't bypass Armor.
- Alternative for cheapest launch: use the default `*.run.app` URL (no LB) and accept no WAF; or put a third-party CDN/WAF (e.g., Cloudflare) in front. Firebase Hosting rewrite is another GA custom-domain option (not researched here).
- Multi-region: one global ALB can front serverless NEGs in up to 3 regions (the quota limit) for failover.

### Gaps
- Cloud Armor Enterprise pricing and whether Adaptive Protection is included in Standard were not confirmed. Rate-limiting rules are believed to be available in Standard but not confirmed from a primary page in this pass.
- Google-managed SSL certificate / Certificate Manager pricing not confirmed (classic managed certs historically free).

## Q3. Secret Manager, service accounts, least privilege

### Takeaway
Mount rotating secrets as volumes (read at access time, follows `latest`) and pin env-var secrets to explicit versions. Give each Cloud Run service and job its own user-managed service account with only `secretmanager.secretAccessor` on the specific secrets it needs. Cost is negligible (~$0.06/version/month).

### Cited Findings
- Env-var secrets are resolved at instance startup (the instance fails to start if retrieval fails); Google recommends pinning env-var secrets to a specific version instead of `latest`. Volume-mounted secrets are not checked at startup, are fetched from Secret Manager on read, and follow the latest version, so they suit rotation. Access requires `roles/secretmanager.secretAccessor` on the service identity. — [Cloud Run: configure secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets)
- Secret Manager pricing: $0.06 per active secret version per location per month; $0.03 per 10,000 access operations; first 6 versions free; management ops not billed. — [Secret Manager pricing (via search snippet)](https://cloud.google.com/secret-manager/pricing)
- Org policy `iam.automaticIamGrantsForDefaultServiceAccounts` stops Compute Engine and App Engine default SAs from getting `roles/editor` on creation; enforced by default for orgs created after 2024-05-03; does not remove Editor from pre-existing default SAs. Google recommends leaving it enabled. — [Troubleshoot org policy errors for service accounts](https://docs.cloud.google.com/iam/docs/troubleshoot-org-policies); [Compute Engine service accounts](https://docs.cloud.google.com/compute/docs/access/service-accounts); [Google Cloud blog: stronger default org policies](https://cloud.google.com/blog/products/identity-security/introducing-stronger-default-org-policies-for-our-customers/)
- Cloud Build's default service account also changed (legacy Cloud Build SA vs Compute default SA). — [Cloud Build default service account change](https://docs.cloud.google.com/build/docs/cloud-build-service-account-updates)

### Inferences
- Suggested identities: `mcp-server@` (secretAccessor on DB URL + token-signing secret; `aiplatform.user` only if it calls Vertex; `cloudtrace.agent`, `monitoring.metricWriter`, `logging.logWriter`), `ingest-job@` (DB secrets, Vertex, third-party API keys), `grader-batch@` (Batch agent reporter + AR reader + GCS writer), `deployer@` (CI; `run.developer`, `iam.serviceAccountUser` only on the runtime SAs, `artifactregistry.writer`). Never run on the default compute SA.
- Third-party LLM API keys and Neon connection strings: volume mounts at e.g. `/secrets/neon/url` so rotation doesn't need a redeploy; if many Neon shard URLs, store a single JSON secret (one version) rather than N secrets.
- Vertex AI calls should use the attached SA (ADC), no key.

### Gaps
- Did not verify automatic rotation-schedule (Pub/Sub notifications) specifics.
- Whether this new GCP project sits under an org created after May 2024 (hence default no-Editor) is project-specific.

## Q4. Supply chain & CI/CD: Artifact Registry scanning, Binary Authorization, Cloud Build vs GitHub Actions + WIF

### Takeaway
For a tiny team already on GitHub: GitHub Actions + keyless Workload Identity Federation (`google-github-actions/auth`) → build/push to Artifact Registry → `gcloud run deploy` with image digest. Turn on AR vulnerability scanning (note $0.26 per scanned image), and add Binary Authorization later if/when compliance demands it.

### Cited Findings
- Artifact Registry automatic container vulnerability scanning: $0.26 per scanned image; scanned on first push (charged for initial scan). — [Artifact Analysis pricing (via search snippet)](https://cloud.google.com/artifact-analysis/pricing); [Container scanning overview](https://docs.cloud.google.com/artifact-analysis/docs/container-scanning-overview)
- Binary Authorization enforces a deploy-time policy (e.g., only images attested as having passed a vulnerability scan); integrates with Artifact Analysis to create attestations. — [Artifact Registry: Artifact Analysis and vulnerability scanning](https://docs.cloud.google.com/artifact-registry/docs/analysis); [Protect artifacts](https://docs.cloud.google.com/artifact-registry/docs/protect-artifacts)
- `google-github-actions/auth` supports WIF; its README recommends attribute conditions on the stable `repository_id` / `repository_owner_id` claims rather than names (names can be renamed/squatted), e.g. `assertion.repository_id == "123" && assertion.repository_owner_id == "456"`. — [google-github-actions/auth](https://github.com/google-github-actions/auth)

### Inferences
- Pipeline: PR → tests; main merge → `auth` (WIF, direct resource access or impersonate `deployer@`) → `docker build` → push to `REGION-docker.pkg.dev/PROJECT/mcp/...` → deploy by digest to a no-traffic tagged revision → smoke test `/mcp` → shift traffic. Add an attribute condition restricting to the repo ID and `ref == refs/heads/main` for the deploy SA.
- Scanning cost scales per push: ~60 pushes/month ≈ $15.6/month; reduce by pushing only on main merges and using AR cleanup policies.
- Cloud Build is fine too (native, no WIF setup), but duplicates GitHub CI; choose one.
- Binary Authorization on Cloud Run: start in dry-run/"require images from our AR" policy; full attestation flow is overhead for 1-2 engineers.

### Gaps
- Binary Authorization pricing and Cloud Run-specific enforcement docs not fetched. Artifact Registry storage price (believed ~$0.10/GB-month after 0.5 GB free) not verified in this pass.

## Q5. Google Cloud Batch for SWE-bench grading (vs raw Compute Engine VMs)

### Takeaway
Batch is free on top of the VMs it creates, supports Spot, exit-code-based retries, and container runnables. It consumes Compute Engine quota, so the 12-vCPU global CE quota is the binding limit for grading throughput.

### Cited Findings
- "There is no additional cost for using Batch. You are only charged for the cost of the underlying resources." Jobs are bounded by Batch quotas and use Compute Engine machine series. — [Batch get started](https://docs.cloud.google.com/batch/docs/get-started)
- `provisioningModel: SPOT` enables Spot VMs; `lifecyclePolicies` with `action: RETRY_TASK` on specific `exitCodes` plus `maxRetryCount` automate retries (e.g., on Spot preemption). — [Automate task retries](https://docs.cloud.google.com/batch/docs/automate-task-retries); [Batch API ProvisioningModel](https://docs.cloud.google.com/dotnet/docs/reference/Google.Cloud.Batch.V1/latest/Google.Cloud.Batch.V1.AllocationPolicy.Types.ProvisioningModel)
- Image streaming (`enableImageStreaming: true`) switches the runtime from Docker to containerd and restricts container fields to imageUri, commands, entrypoint, volumes, enableImageStreaming. — [Batch: use image streaming](https://docs.cloud.google.com/batch/docs/use-image-streaming)

### Inferences
- SWE-bench grading needs to launch per-instance Docker images (docker-in-job). Simplest on Batch: a **script runnable** on a Batch-provided VM image that has Docker, running the harness which calls `docker run` itself — rather than a container runnable with the Docker socket mounted. Do not enable image streaming for this (containerd).
- Spot + RETRY_TASK on preemption exit codes fits embarrassingly-parallel grading; keep tasks idempotent (repo's existing GCE grading queue already shards by instance).
- Against raw VMs (the repo's current GCE grading scripts): Batch removes the custom queue/power-off logic but gives less control over pre-pull/NVMe tuning; with only 12 vCPU either option is quota-bound — request a CE quota increase for Spot/preemptible CPUs.
- Cloud Run Jobs cannot run nested Docker, so they are unsuitable for SWE-bench grading but fine for the ingestion worker.

### Gaps
- Did not confirm Batch's own quotas (jobs per region, etc.) or whether Batch default VM images ship with Docker preinstalled (believed yes for container runnables' host; unverified for script runnables).
- Spot VM discount percent not fetched.

## Q6. Database: Neon vs Cloud SQL for PostgreSQL vs AlloyDB (pgvector)

### Takeaway
At low traffic Neon (Launch/Scale) stays cheapest and uniquely offers branching and project-per-shard; move to Neon Scale for a 99.95% SLA, IP allow-lists and Private Link. Cloud SQL (pgvector 0.8.1, GA Managed Connection Pooling) is the in-GCP option with private IP from Cloud Run. AlloyDB (HNSW + ScaNN) is overkill on price at this scale.

### Cited Findings
- **Neon plans:** Free — 100 CU-hours/project, 0.5 GB/project, 6 h restore window, 100 projects, 10 branches/project, no SLA. Launch — $0.106/CU-hour, $0.35/GB-month, restore window up to 7 days, 100 projects, 10 branches/project (+$1.50/branch-month), no SLA, no IP allow, no private networking. Scale — $0.222/CU-hour, $0.35/GB-month, up to 30 days restore, 1,000 projects (expandable), 25 branches/project, SLA, IP allow rules, private networking at $0.01/GB, HIPAA, SOC 2. No monthly minimums; hourly metering. — [Neon pricing](https://neon.com/pricing)
- Neon Scale includes Private Link and a 99.95% availability SLA (secondary summary of Neon pages). — [Neon pricing](https://neon.com/pricing) (via search summary; SLA figure not seen directly on the fetched page)
- Neon pooling: PgBouncer in **transaction mode**, `max_client_conn = 10,000`. — [Neon connection pooling docs](https://neon.com/docs/connect/connection-pooling)
- **Cloud SQL:** pgvector upgraded 0.8.0 → 0.8.1 per release notes; Managed Connection Pooling is GA (clients connect to a pool cluster that absorbs connection spikes); requires maintenance version `...R20250302.00_04` or later. — [Cloud SQL for PostgreSQL release notes](https://docs.cloud.google.com/sql/docs/postgres/release-notes); [Managed Connection Pooling overview](https://docs.cloud.google.com/sql/docs/postgres/managed-connection-pooling)
- Cloud SQL pricing: priced per vCPU and GB memory per hour; shared-core tiers are Enterprise edition only; db-f1-micro (0.2 vCPU, 0.6 GiB) ~$8-11/month in us-central1 (sources disagree: ~$8 vs $11.32). CUDs: 1-yr 25%, 3-yr 52% (same for AlloyDB). — [Bytebase Cloud SQL pricing](https://www.bytebase.com/dbcost/cloudsql-pricing/); [Cloud SQL CUDs](https://docs.cloud.google.com/sql/docs/postgres/cud); [Bytebase AlloyDB pricing](https://www.bytebase.com/blog/understanding-google-alloydb-pricing/)
- **AlloyDB:** smallest instance is 2-vCPU C4A (Axion Arm, `c4a-highmem-2-lssd`); vCPU $0.06608/h → ~$48/month for vCPU alone (memory billed separately, so real cost is higher). Supports pgvector HNSW plus Google's ScaNN index; 30-day free trial cluster (8 vCPU basic primary, storage to 1 TB). — [Bytebase AlloyDB pricing](https://www.bytebase.com/blog/understanding-google-alloydb-pricing/); [AlloyDB choose machine type](https://docs.cloud.google.com/alloydb/docs/choose-machine-type); [AlloyDB free trial](https://docs.cloud.google.com/alloydb/docs/free-trial-cluster); [Google blog: ScaNN vs pgvector HNSW](https://cloud.google.com/blog/products/databases/how-scann-for-alloydb-vector-search-compares-to-pgvector-hnsw)

### Inferences
- **Recommendation:** stay on Neon for launch. The sharded many-project layout fits Launch (100-project cap) only if shards <= 100; beyond that or for SLA/IP allow-lists, Scale (1,000 projects). Use the pooled (`-pooler`) endpoint from Cloud Run with asyncpg `statement_cache_size=0` (transaction-mode PgBouncer breaks server-side prepared statements), and keep the pool small per instance (e.g., 5).
- Pick a Neon region close to the Cloud Run region; Neon traffic from Cloud Run goes over public internet + TLS unless Private Link is used (Scale). Egress from Cloud Run to Neon incurs Internet egress (small at this scale).
- Staging = Neon branch per shard (copy-on-write), which Cloud SQL/AlloyDB cannot match (they'd need clones).
- **Cloud SQL option** if the goal is "everything in GCP, private networking": Enterprise edition, dedicated-core 1-2 vCPU (shared-core is not SLA-backed — see Gaps), private IP, Cloud Run **Direct VPC egress** to reach it, Managed Connection Pooling, PITR enabled. One instance hosting many databases replaces many Neon projects.
- AlloyDB only when vector QPS/recall at scale justifies ~$150+/month minimum per instance.

### Gaps
- Neon: GCP region list, exact SLA percent/credits and Private Link availability on GCP not verified from primary pages.
- Cloud SQL: SLA coverage for shared-core tiers (believed excluded), HA pricing (believed ~2x), PITR retention defaults, Managed Connection Pooling pricing — not verified this pass.
- AlloyDB memory price per GB-hour not captured, so full 2-vCPU monthly cost is not computed.
- Direct VPC egress docs not fetched (feature is GA per general knowledge; unverified here).

## Q7. Observability

### Takeaway
Instrument the Python app with the OpenTelemetry SDK and export OTLP to Google's Telemetry API (telemetry.googleapis.com), either directly or via the Google-Built OpenTelemetry Collector as a Cloud Run sidecar. Logging stays in the 50 GiB/month free tier at low traffic.

### Cited Findings
- Google-Built OpenTelemetry Collector sends correlated OTLP traces, metrics and logs to Google Cloud Observability; there are Cloud Run sidecar deployment instructions and Python samples. — [Google-Built OTel Collector overview](https://docs.cloud.google.com/stackdriver/docs/instrumentation/google-built-otel); [Deploy collector on Cloud Run](https://docs.cloud.google.com/stackdriver/docs/instrumentation/opentelemetry-collector-cloud-run); [Collector-based samples](https://docs.cloud.google.com/trace/docs/setup/sample-overview)
- Telemetry (OTLP) API `telemetry.googleapis.com` accepts logs, metrics and traces in OTLP; Cloud Trace accepts OTLP traces there. — [Google Cloud blog: OpenTelemetry in Google Cloud Observability](https://cloud.google.com/blog/products/management-tools/opentelemetry-now-in-google-cloud-observability); [Cloud Run OTLP sidecar tutorial](https://docs.cloud.google.com/run/docs/tutorials/custom-metrics-opentelemetry-sidecar)
- Cloud Logging: $0.50/GiB ingestion incl. 30-day retention; first 50 GiB per project free; $0.01/GiB/month beyond 30 days. — [Google Cloud blog: Cloud Logging pricing](https://cloud.google.com/blog/topics/cost-management/how-to-approach-cloud-logging-pricing-for-cloud-admins)

### Inferences
- Setup: `opentelemetry-instrumentation-starlette`/`asgi`, `-asyncpg`, `-httpx` (for LLM calls) → OTLP to sidecar collector; structured JSON logs to stdout with `logging.googleapis.com/trace` field for correlation; Error Reporting picks up stack traces from logs automatically.
- Uptime check: an HTTP check against a cheap `/healthz` (not a POST /mcp tool call) from 3+ locations; alert policy on uptime failure.
- SLOs: Cloud Monitoring service-level objectives on the Cloud Run service (availability = non-5xx ratio, e.g., 99.5% / 28 days; latency p95 on `request_latencies`), with burn-rate alerts.

### Gaps
- Did not fetch uptime check / SLO docs or Cloud Monitoring/Trace pricing (trace spans and custom metrics above free allotments).

## Q8. Low-traffic monthly cost estimate (us-central1-ish, list prices)

### Takeaway
Roughly **$120-160/month** for the production shape (min-instance MCP service, LB + Armor, secrets, registry w/ scanning, logs in free tier, few-hours-a-day job, small Neon DB). Dropping the LB/Armor saves ~$30; Neon Scale for SLA adds ~$20-40.

### Cited Findings (unit prices used)
- Cloud Run instance-based: $0.000018/vCPU-s, $0.000002/GiB-s; free 240k vCPU-s + 450k GiB-s/month. — [Cloud Run pricing (via snippet)](https://cloud.google.com/run/pricing)
- LB forwarding rule $0.025/h; $0.008/GB processed. — [Load Balancing pricing (via snippet)](https://cloud.google.com/load-balancing/pricing)
- Armor Standard $5/policy, $1/rule, $0.75/M requests. — [Armor pricing (via snippet)](https://cloud.google.com/armor/pricing)
- Secret Manager $0.06/version/month (6 free), $0.03/10k accesses. — [Secret Manager pricing (via snippet)](https://cloud.google.com/secret-manager/pricing)
- AR scanning $0.26/image. — [Artifact Analysis pricing (via snippet)](https://cloud.google.com/artifact-analysis/pricing)
- Logging $0.50/GiB after 50 GiB free. — [Cloud Logging pricing blog](https://cloud.google.com/blog/topics/cost-management/how-to-approach-cloud-logging-pricing-for-cloud-admins)
- Neon Launch $0.106/CU-h, Scale $0.222/CU-h, $0.35/GB-month. — [Neon pricing](https://neon.com/pricing)

### Inferences (computed estimates, 730 h month)
| Item | Assumption | Est. $/month |
|---|---|---|
| Cloud Run MCP service | 1 vCPU/2 GiB, min=1, instance-based, always on: 2,628,000 vCPU-s x 0.000018 = $47.30 + 5,256,000 GiB-s x 0.000002 = $10.51; minus free tier (~$5.2) | ~$52-58 |
| Global ALB | 1 forwarding rule x 730 h x $0.025 + negligible data | ~$18.3 |
| Cloud Armor Standard | 1 policy + 5 rules + ~1-5M req | ~$11-14 |
| Secret Manager | ~10-15 active versions, low access | <$1 |
| Artifact Registry | storage a few GB (~$0.10/GB unverified) + scanning ~30-60 images x $0.26 | ~$8-16 |
| Logging/Monitoring/Trace | under 50 GiB logs free | ~$0-5 |
| Cloud Run Job (ingestion) | 2 vCPU/4 GiB, 3 h/day x 30 = 324,000 s → 648k vCPU-s x 0.000018 = $11.66 + 1.296M GiB-s x 0.000002 = $2.59 (assumes jobs billed at the instance-based rates) | ~$14 |
| DB option A: Neon Launch | 0.25 CU always-on (730 x 0.25 x $0.106 = $19.35) + 10 GB ($3.50); scale-to-zero lowers it | ~$15-25 |
| DB option B: Neon Scale | same shape at $0.222/CU-h = $40.5 + $3.5 | ~$45 |
| DB option C: Cloud SQL | db-f1-micro ~$8-11 + storage (no SLA); 1-2 vCPU dedicated realistic for prod | ~$10-15 (micro) / higher dedicated |
| DB option D: AlloyDB | 2 vCPU C4A: $48 vCPU alone + memory | $100+ (not recommended) |
| **Total (Neon Launch)** | | **~$120-150** |
| Batch grading (separate, bursty) | Spot VMs only while grading; free orchestration | usage-based |

- Cost levers: request-based billing with min-instances bills idle time at a reduced rate (rate unverified) — compare once traffic is known; a 1-yr Cloud Run CUD applies to always-on instances; skip the LB at pre-launch.

### Gaps
- Request-based and idle-instance Cloud Run rates, Cloud Run Jobs' exact rate card, Tier-2 region multipliers, and internet egress to Neon/third-party LLM APIs were not verified; the estimates above assume Tier-1 region list prices and should be checked in the [GCP pricing calculator](https://cloud.google.com/products/calculator).
- Vertex AI Gemini judge-call costs and third-party LLM API costs are excluded (usage-driven).
