# Deploying the StealthLab backend to Railway

This document is the production configuration contract for the StealthLab
REST API (`backend/app/main.py`) on Railway. It covers exactly one
deployable: the FastAPI backend a frontend talks to. **The MCP server
(`backend/app/mcp_server/server.py`) is intentionally out of scope** — see
[MCP server](#mcp-server-not-covered-here) below for why.

No real secrets, endpoints, or production URLs are in this document or in
the repository. Every value you must supply is called out explicitly.

## 1. What's already true about this backend

- ASGI entrypoint: `app.main:app` (FastAPI), started with
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT` — already how
  `backend/Procfile` and the working `render.yaml` reference deployment run
  it. This is the same command Railway will run.
- Python 3.12+ (`backend/pyproject.toml`), dependencies pinned in
  `backend/requirements.txt` (the file the working Render deployment
  actually installs from — use it on Railway too, not `pyproject.toml`
  alone; see [pyproject vs requirements.txt](#pyproject-vs-requirementstxt)).
- All configuration goes through `backend/app/config.py`'s `Settings`
  (pydantic-settings) or a handful of env vars read directly (documented in
  the checklist below) — nothing is hardcoded per-environment except
  documented dev-only defaults.
- Database: asyncpg pool against Postgres (`backend/app/db/session.py`).
  Works against Supabase Postgres or any Postgres 14+ with the `vector`
  extension available (`pgvector`); no Supabase-specific driver code.
- Health check: `GET /health` → `{"status": "ok"}` (`backend/app/main.py`).
  Process-alive only, no DB round-trip, cheap.
- Migrations: plain SQL files in `backend/db/*.sql`, applied in lexical
  order by `backend/scripts/migrate.py`, tracked in a `schema_migrations`
  ledger it creates itself. Idempotent and safe to re-run — it detects and
  refuses on an edited-after-applied file rather than silently re-running
  it. **Not destructive**: it only ever runs `CREATE`/`ALTER`-shaped
  migration files that already exist in the repo; it does not drop or
  reset anything.

## 2. Railway service configuration

Because this is a monorepo (backend/frontend/experiments all under one git
root), Railway needs to be told the backend service's root directory —
this is a one-time manual step in the Railway dashboard, not something a
config file can set:

1. Create the service from this GitHub repo.
2. In the service's **Settings → Source**, set **Root Directory** to
   `backend`.
3. Railway will then pick up `backend/railway.json` (added by this change)
   automatically. It pins:

   | Setting | Value |
   |---|---|
   | Builder | Nixpacks (Railway's default Python builder) |
   | Build command | `pip install -r requirements.txt` |
   | Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Healthcheck path | `/health` |
   | Restart policy | `ON_FAILURE`, max 3 retries |

   `$PORT` is Railway's injected port — the app must never hardcode 8000,
   8765, or 8811 in production, and it doesn't: the start command is the
   only place a port number is decided, and it comes from Railway.

No Dockerfile is used for this service. `backend/Dockerfile` exists for a
*different* deployable (the MCP server, which needs `experiments/
swebench_pro/` copied in as a sibling directory) and building it for the
REST API would drag in that unrelated, larger build context for no
benefit. Nixpacks building straight from `backend/requirements.txt` is the
simplest correct option here, matching what already works on Render.

### Why not a release/pre-deploy command for migrations

Railway doesn't have a native "release phase" distinct from the start
command the way some other PaaS's do. Wiring
`python scripts/migrate.py && uvicorn ...` into the start command (as
`backend/Dockerfile` does for the MCP server, which only ever runs a
single container) becomes a problem the moment you scale this service to
more than one replica: every replica would race to run migrations
concurrently on every restart. `scripts/migrate.py` is *idempotent* and
uses a ledger table, so a race is unlikely to corrupt anything, but it's
not a race worth having by default. Run migrations as an explicit,
separate step instead — see [Database migrations](#4-database-migrations)
below.

## 3. Environment variable checklist

Copy this list into Railway's variable editor. `[ ]` = you need to set it,
`[ ]  (optional)` = has a safe default, only set it if you want the
non-default behavior. **No value below is a real secret — every example is
a placeholder.**

### Required for any working deployment

- [ ] `DATABASE_URL` — Postgres connection string (Supabase: use the
  **direct**, non-pooled connection string for the app's own pool, and
  separately for `scripts/migrate.py`; the pooled/PgBouncer transaction-mode
  string is documented as incompatible with asyncpg's prepared-statement
  cache in `backend/app/db/session.py`). Not a secret you type into code —
  paste it as a Railway variable. Example: `postgresql://user:pass@host:5432/postgres`.
  Get it from: Supabase dashboard → Project Settings → Database → Connection string.
- [ ] `FRONTEND_ORIGIN` — the deployed frontend's origin(s), comma-separated
  if more than one (e.g. a production domain and a preview domain). Example:
  `https://your-frontend.vercel.app`. **Must not** be `localhost`,
  `127.0.0.1`, or `*` in production — the app now refuses to boot with
  `ENVIRONMENT=production` and any of those (see
  [Production vs development](#5-production-vs-development)). Not a secret,
  safe to expose (it's a CORS allowlist, read by browsers anyway).
- [ ] `ENVIRONMENT` — set to `production` on Railway. Not a secret. This is
  what turns on the production config guard described above and tags
  Sentry events (if `SENTRY_DSN` is set).

### At least one LLM provider (the debate panel needs three distinct model families; pick a posture)

Pick **one** of the four provider postures below. All are optional/pluggable
— set only the one you're using; the app never requires a provider you
haven't opted into.

- [ ] Closed frontier roster (default posture, no flag needed):
  `ANTHROPIC_API_KEY`, `FIREWORKS_API_KEY`, `OPENAI_API_KEY` — secrets, get
  from each provider's dashboard. Do not expose to frontend/client code.
- [ ] `[ ] (optional)` `USE_GENERAL_COMPUTE=true` + `GENERAL_COMPUTE_API_KEY`
  (secret) + `GENERAL_COMPUTE_PANEL_MODELS` / `GENERAL_COMPUTE_JUDGE_MODEL`
  — hosted open-weight alternative. See https://docs.generalcompute.com.
- [ ] `[ ] (optional)` `USE_OPENROUTER=true` + `OPENROUTER_API_KEY` (secret)
  — single-account 4-seat roster via https://openrouter.ai.
- [ ] `[ ] (optional)` `USE_LOCAL_MODELS=true` + `LOCAL_BASE_URL` — **do not
  use in production**; this points at an Ollama-style local server, which
  Railway's container does not provide. Development-only.

### Judge provider (independent of whichever panel you picked above)

- [ ] `GOOGLE_API_KEY` — secret, Gemini via its OpenAI-compatible endpoint.
  Required unless you're fully on `USE_LOCAL_MODELS` or a provider posture
  whose judge model doesn't need it.

### Embeddings

- [ ] `VOYAGE_API_KEY` — secret. Required unless `GEMINI_API_KEY` alone
  covers the configured `EMBEDDING_PROVIDER_CHAIN` (default `gemini,voyage`
  — first success wins, so having both configured is the resilient option).
- [ ] `GEMINI_API_KEY` — secret. See above.
- [ ] `[ ] (optional)` `GEMINI_API_KEYS` — comma-separated rotation pool if
  you have more than one Gemini key (raises the effective free-tier TPM
  ceiling). Secret.
- [ ] `[ ] (optional)` `EMBEDDING_PROVIDER_CHAIN` — default `gemini,voyage`.
  Not a secret.

### Auth (Supabase Auth preset — recommended path to real multi-user auth)

- [ ] `[ ] (optional, but see below)` `SUPABASE_PROJECT_URL` —
  `https://<ref>.supabase.co`, the **same** Supabase project as your
  `DATABASE_URL`. Not a secret (it's a public project URL), but don't set
  it without `SUPABASE_JWT_AUDIENCE` too — a half-configured pair makes the
  app refuse to boot if `REAL_AUTH_ENABLED`/`PRIVATE_VISIBILITY_ENABLED`/
  `MULTI_USER_EXPOSURE_ENABLED`/`HOSTED_EXECUTION_ENABLED` are on.
- [ ] `[ ] (optional)` `SUPABASE_JWT_AUDIENCE` — normally `authenticated`.
  Not a secret. Requires the Supabase project's JWT signing key to be
  asymmetric (ES256/RS256) — see `backend/.env.example`'s detailed note;
  HS256 is never accepted by this backend.
- [ ] `[ ] (optional)` `OIDC_ISSUER` / `OIDC_AUDIENCE` / `OIDC_JWKS_URL` —
  generic OIDC alternative to the Supabase preset, if you're using a
  different identity provider. Not secrets.
- [ ] `[ ] (optional, defaults false)` `REAL_AUTH_ENABLED`,
  `PRIVATE_VISIBILITY_ENABLED`, `MULTI_USER_EXPOSURE_ENABLED` — leave `false`
  until you've configured OIDC/Supabase above; the app refuses to boot if
  any of these is `true` without identity configured. Not secrets.

### Execution / sandboxing

- [ ] `HOSTED_EXECUTION_ENABLED` — **leave `false`** (the default) unless you
  have specifically decided to enable hosted multi-tenant repo execution and
  have OIDC/Supabase configured. See
  [Hosted execution](#6-hosted-execution-leave-disabled) below — this is a
  deliberate security boundary, not a feature flag to flip for convenience.
  Not a secret.

### Governance (spend/rate limiting — sane defaults, override only if needed)

- [ ] `[ ] (optional)` `GOVERNANCE_ENABLED` — default `true`. Not a secret.
- [ ] `[ ] (optional)` `DAILY_LLM_BUDGET_USD` — default `10.0`. Not a secret.
- [ ] `[ ] (optional)` `PER_VIEWER_DAILY_BUDGET_USD` — default `1.0`. Not a secret.

### Ingestion (background scheduler — safe defaults, does not need touching to deploy)

- [ ] `[ ] (optional)` `INGESTION_AUTO_ENABLED` — default `true`, runs an
  in-process asyncio loop (no separate worker process/dyno needed). Not a
  secret. See [Background jobs](#7-background-jobs--scheduled-ingestion).
- [ ] `[ ] (optional)` `INGESTION_AUTO_MODE` — default `local`
  (workspace-local, DB-free). Leave as-is for a hosted deployment unless you
  specifically want the `global` (DB-backed) sweep; `global` mode still only
  processes small, bounded batches per tick (`INGESTION_AUTO_*_LIMIT`
  settings), never a bulk backfill.

### Observability

- [ ] `[ ] (optional)` `SENTRY_DSN` — off (no-op) if unset. Not shown in any
  UI/response if set; treat as a secret anyway since DSNs can be abused for
  event injection. Get from your Sentry project settings.
- [ ] `[ ] (optional)` `SENTRY_TRACES_SAMPLE_RATE` — default `0.1`. Not a secret.
- [ ] `[ ] (optional)` `RELEASE` — free-text release tag for Sentry. Not a secret.

### Not managed by `Settings` — must still be set as plain Railway variables if you use the feature

These are read directly via `os.environ`/`os.getenv` rather than through
the `Settings` class, so they won't show up if you only skim
`backend/app/config.py`:

- [ ] `[ ] (optional)` `GITHUB_TOKEN` — only if you use the GitHub-corpus
  ingestion source (`app/services/ingestion_sources/github_corpus.py`).
  Secret.
- [ ] `[ ] (optional)` `CLAUDE_PROJECT_DIR` — only relevant to local-mode
  ingestion tied to a specific developer workspace; not meaningful on
  Railway's ephemeral filesystem, leave unset.
- [ ] `[ ] (optional)` `STEALTHLAB_TRACE_DIR` — same as above, dev-local.

## 4. Database migrations

Run this once before the first deploy, and again any time a new migration
file lands in `backend/db/`:

```bash
railway run --service <your-service-name> python scripts/migrate.py
```

(Or run it from your own machine with `DATABASE_URL` set to the same
Supabase/Postgres direct connection string, via `backend/.env` or an
exported env var — `scripts/migrate.py` needs the **direct**, non-pooled
connection string, not a PgBouncer/transaction-pooler URL.)

Check status without applying anything:

```bash
python scripts/migrate.py --status
```

This is a manual, explicit step by design (see
[Why not a release command](#why-not-a-releasepre-deploy-command-for-migrations)
above) — it is never wired into the app's own startup or into
`railway.json`'s start command, so a deploy never silently mutates schema.

## 5. Production vs development

Two things now enforce this boundary at boot (both fail fast with a
message that names the missing/wrong setting, never a value):

1. **`Settings.assert_production_config()`** (`backend/app/config.py`,
   called from `app/main.py`'s lifespan) — refuses to boot when
   `ENVIRONMENT=production` and `FRONTEND_ORIGIN` is unset, still
   `localhost`/`127.0.0.1`, or `*`. This is opt-in: it does nothing unless
   you set `ENVIRONMENT=production`, so no existing local/dev/CI setup
   changes behavior.
2. **`assert_boot_posture()`** (`backend/app/services/authn.py`, already
   existed, unchanged) — refuses to boot if `PRIVATE_VISIBILITY_ENABLED`,
   `REAL_AUTH_ENABLED`, `MULTI_USER_EXPOSURE_ENABLED`, or
   `HOSTED_EXECUTION_ENABLED` is `true` without OIDC/Supabase identity
   configured.

Neither check exposes secret *values* in its error — only the name of the
missing/misconfigured setting.

## 6. Hosted execution: leave disabled

`HOSTED_EXECUTION_ENABLED=false` is the current safe default and this
change does not alter that. With it `false`, repo-executing code paths
keep using caller-provided paths exactly as before (the pre-hosted-mode
behavior) — nothing about deploying to Railway requires turning this on.

If you later decide to enable it, you would additionally need, beyond just
setting the flag and configuring OIDC/Supabase (already enforced by
`assert_boot_posture`):

- A registered workspace/storage-path mechanism
  (`backend/app/services/workspace_registry.py`) so `repo_path` resolution
  moves server-side instead of trusting the caller.
- A real isolation boundary for whatever process actually executes
  untrusted repo code. `backend/app/services/sandbox.py`'s Linux
  `unshare`-based isolation explicitly documents itself as unverified for
  non-root production users, and unprivileged user namespaces are not
  guaranteed available in every hosting environment — Railway's container
  runtime has not been verified to support it. `backend/app/services/
  sandbox_executor.py`'s container-based path likely needs a Docker socket,
  which Railway does not provide to a standard web service by default.
- A explicit decision about whether that execution runs inside the same
  Railway web process at all, versus a separate isolated service/worker —
  running arbitrary user repos inside the main web process without that
  isolation boundary is exactly what this flag exists to prevent.

None of this is required to get the REST API running on Railway — it's
listed here so enabling it later is a deliberate decision with a known
checklist, not a surprise.

## 7. Background jobs / scheduled ingestion

The only thing that runs automatically is `app/services/ingestion_scheduler.py`,
an in-process `asyncio.create_task` loop started in `app/main.py`'s
lifespan — no separate Railway worker service is needed for it, and no
cron config is needed either. It ticks every `INGESTION_AUTO_INTERVAL_SECONDS`
(default 60s) and does small, bounded work per tick (`INGESTION_AUTO_*_LIMIT`
settings cap it) — it never runs a bulk backfill automatically, and per-tick
failures are caught and logged, never crash the process.

Everything else under `backend/scripts/` (`backfill_embeddings.py`,
`backfill_agent_embeddings.py`, `backfill_procedure_embeddings.py`,
`dedup_sweep.py`, `scan_knowledge_conflicts.py`, `seed_*.py`, etc.) is a
manual, one-off operational script — **none of it is wired into this
deployment, and this change does not start any of it.** If you need to run
one of these against the Railway/Supabase database later, run it the same
way as migrations: `railway run --service <name> python scripts/<script>.py`,
as a deliberate, separate action — not something that happens because you
deployed.

## 8. MCP server: not covered here

`backend/app/mcp_server/server.py` is a second, independent ASGI app
(`uvicorn app.mcp_server.server:app`, port 8765) with its own auth model
(`STEALTHLAB_MCP_TOKEN`) and its own documented posture: loopback-only by
design (`backend/README_MCP_SERVER.md`, `docker-compose.yml`'s comments,
and `render.yaml`'s own header comment all say the same thing — it's meant
to be reached from another trusted process/machine, not deployed publicly).
It also imports `Agent`/`RepoSandbox` from `experiments/swebench_pro/` as a
sibling directory at module import time, which is why `backend/Dockerfile`
builds from the repo root, not from `backend/` alone.

This task explicitly scopes to making the REST API deployment-ready, and
the existing `render.yaml` already treats the MCP server the same way (no
service entry for it). Deploying the MCP server publicly on Railway would
be a deliberate deviation from its documented security posture — including
hardcoded `http://127.0.0.1:8765` values in its OIDC-shaped auth-server
metadata (`server.py`'s `AuthSettings`) that would need a real code change,
not just an env var, to be correct behind a public URL. If you want this
later, treat it as a separate, explicit decision with its own review, not
something bundled into this deployment.

## 9. Local smoke test

Run before your first Railway deploy, and again after any config change:

```bash
python scripts/railway_smoke_test.py
```

From `backend/`. No real credentials required. It checks, in order:

1. Dependencies install and `app.main` imports cleanly.
2. A completely unset `DATABASE_URL` fails with a clear
   `Missing required setting 'database_url'` message (not a stack trace
   dump of secrets, not a hang).
3. `ENVIRONMENT=production` with a default/localhost `FRONTEND_ORIGIN`
   fails clearly.
4. `ENVIRONMENT=production` with `FRONTEND_ORIGIN=*` fails clearly.
5. `ENVIRONMENT=production` with a real origin passes.
6. If `docker` is available: brings up the repo's own `docker-compose.yml`
   Postgres (`pgvector/pgvector:pg15`, not a mock, but not a secret either),
   runs `scripts/migrate.py` against it, starts `uvicorn app.main:app` bound
   to `0.0.0.0` on a random free port with a placeholder API key, and
   confirms `GET /health` responds — while checking that the placeholder
   value never appears in the server's own output. Skipped (not failed) if
   `docker` isn't installed.

## 10. pyproject vs requirements.txt

`backend/requirements.txt` is what actually gets installed in the working
Render deployment and is what `backend/railway.json`'s build command uses
too — use it, not bare `pip install -e .` (which reads only
`pyproject.toml`'s dependency list). This change also added `PyJWT[crypto]`
to `pyproject.toml`'s `dependencies` (it was already in
`requirements.txt`, but missing from `pyproject.toml` — a real gap, since
`app/services/authn.py` does `import jwt` unconditionally and `app/main.py`
imports from `authn` at module load, making it a hard boot-time dependency
for every deployment path). No other dependency changes were made.

## 11. Final answer: is the repo ready to connect to Railway?

Yes, for the REST API service, once you:

1. Create the Railway service, set **Root Directory = `backend`**.
2. Set the required environment variables from the checklist above
   (`DATABASE_URL`, `FRONTEND_ORIGIN`, `ENVIRONMENT=production`, and
   whichever LLM/embedding provider keys you're using).
3. Run `railway run python scripts/migrate.py` once before the first
   request hits the service.
4. Deploy. Railway will build via Nixpacks using
   `backend/railway.json`'s pinned build/start commands and poll `/health`.

The MCP server is a separate, deliberately out-of-scope deployable (§8) and
hosted execution stays off (§6) — neither blocks the REST API from running.
