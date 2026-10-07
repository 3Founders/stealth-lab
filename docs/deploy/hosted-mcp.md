# Hosting the StealthLab MCP server

Users connect with `irm https://<site>/install.ps1 | iex`,
`curl -fsSL https://<site>/install.sh | bash` or `npx -y stealthlab-mcp install`.
Each of these points their agent at **one hosted MCP endpoint**. Nothing
StealthLab-side runs on the user's machine. This page covers standing that
endpoint up.

## 1. Create the service (Railway)

1. New service from this repo, and set its **config file path** to
   `railway.mcp.json`. That file builds `backend/Dockerfile.mcp-server`, runs
   one replica, and health-checks `GET /readyz` (readiness: the database answers,
   migrations are current, and the process is not shutting down).
2. The container runs migrations first and then starts uvicorn on `$PORT`
   with `--workers 1`. That setting is required: task state is in-memory.
   `--timeout-graceful-shutdown 25` lets in-flight requests finish on SIGTERM.
   Keep the host's stop grace period above 25 s.

**Probes:**

| Endpoint | Checks | Who uses it |
|---|---|---|
| `GET /healthz` | Liveness only: the event loop answers, no dependency checks | Docker `HEALTHCHECK` (`scripts/docker_healthcheck.py`), so a database blip never restarts the container |
| `GET /readyz` | 200 when the pool exists, `SELECT 1` answers within 2 s, the newest control migration in the image is applied, and the process is not draining. Otherwise 503, with the failed check named and no connection details | The host health check (Railway `healthcheckPath`), so traffic is withheld while the process is not ready |
| `GET /` | Unchanged static `ok` | Anything that still probes it |

On SIGTERM, `/readyz` turns 503 at once, uvicorn stops accepting connections and
waits up to 25 s for in-flight requests, and the shutdown then closes the
database pool and flushes Sentry and OpenTelemetry. Model and embedding providers
are not part of readiness: `find_ways` degrades to lexical search without them.

`python -m app.ingestion.admin ledger-settle-abandoned [--apply]` closes
provider-call reservations that a dead process left open. It settles them at
their reserved worst case, so budgets never under-count. Run it after a crash,
or on a schedule. `scripts/live_shutdown_check.py --dsn <loopback scratch DB>`
re-runs the live drain test.
3. Add a custom domain, for example `mcp.<your-domain>`.

Any other container host (Cloud Run, Render, Fly) works the same way: build
`backend/Dockerfile.mcp-server` from the repo root, give it `PORT`, and run a
single instance.

## 2. Environment

| Variable | Value | Why |
|---|---|---|
| `DATABASE_URL` | the production Postgres (pgvector) | Goals, Procedures, claims |
| `STEALTHLAB_MCP_PUBLIC_URL` | `https://mcp.<your-domain>` | **Required when hosted.** Without it the MCP SDK only accepts `localhost` Host headers, so every request through the real domain is refused with 421. It also sets the advertised issuer and resource URL. |
| `STEALTHLAB_MCP_TOKEN` | random secret (`python -c "import secrets;print(secrets.token_urlsafe(32))"`) | Required at boot. It is an operator/service credential, not something users need; reads are anonymous. |
| `DEPLOYMENT_MODE` | `shared` | Multi-user posture; requires the two OIDC variables below |
| `OIDC_ISSUER` | `https://<project>.supabase.co/auth/v1` | Signed-in users (needed by `report_discovery`) |
| `OIDC_AUDIENCE` | `authenticated` | same |
| `MCP_WORKER_COUNT` | `1` | Must match `--workers 1` |
| `JEV_BASE_URL` and/or `GEMINI_API_KEY` | judge provider | Contextual Goal and Procedure judgment. Without it, `find_ways` still answers, but with `goal_judgment.mode = "lexical_fallback"` |
| `VOYAGE_API_KEY` (or `GEMINI_API_KEY`) | embeddings | vector leg of Goal and Procedure search |

## 3. Check it

```bash
curl -s https://mcp.<your-domain>/            # {"service":"stealthlab-mcp","status":"ok",...}
npx -y stealthlab-mcp doctor --url https://mcp.<your-domain>/mcp
```

`doctor` must print `server : stealthlab 1.0.0` and `ok`.

## 4. Point the installers at it

**Website one-liners (`curl | bash`, `irm | iex`):** set
`NEXT_PUBLIC_KEL_MCP_URL=https://mcp.<your-domain>/mcp` on the website
deployment and rebuild it. The build step (`prod_frontend/scripts/sync-installers.mjs`)
writes it into the served `install.sh` and `install.ps1` as their default
endpoint. No repository edit is needed.

**`npx -y stealthlab-mcp install` with no `--url`** reads the default baked into
the npm package. Set the same URL in the three places below, then release:

1. `packaging/npm/package.json` → `stealthlab.defaultMcpUrl`
2. `packaging/npm/install/install.sh` → `DEFAULT_URL`
3. `packaging/npm/install/install.ps1` → `$DefaultUrl`

`npm publish` refuses to run until all three match. Push the tag
`stealthlab-mcp-v<version>` to publish (this needs the `NPM_TOKEN` repository
secret). The website serves `install.sh` and `install.ps1` from
`prod_frontend/public/`, which is copied from `packaging/npm/install/` at build
time.

## 5. Ingestion

The hosted MCP server only reads and records discoveries. Goals, Procedures,
hierarchy placement and Benchmark transfers are produced by the ingestion
workers (`deploy/ingestion/`, `python -m app.ingestion.worker --loop`). The
workers need the same `DATABASE_URL` and the judge and embedding providers
listed above. Without a judge or embedder they refuse to start instead of
guessing. Each worker loop also re-enqueues Goal placement for any recent Goal
whose placement job was lost.
