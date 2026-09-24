# Hosting the StealthLab MCP server

Users connect with `irm https://<site>/install.ps1 | iex`,
`curl -fsSL https://<site>/install.sh | bash` or `npx -y stealthlab-mcp install`.
Each of these points their agent at **one hosted MCP endpoint**. Nothing
StealthLab-side runs on the user's machine. This page covers standing that
endpoint up.

## 1. Create the service (Railway)

1. New service from this repo, and set its **config file path** to
   `railway.mcp.json`. That file builds `backend/Dockerfile.mcp-server`, runs
   one replica, and health-checks `GET /`.
2. The container runs migrations first and then starts uvicorn on `$PORT`
   with `--workers 1`. That setting is required: task state is in-memory.
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

Set the same URL in the three places the installers read it from, then
release:

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
