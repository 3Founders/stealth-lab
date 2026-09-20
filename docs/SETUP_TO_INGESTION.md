# StealthLab: from the current state to the first real ingestion

This is the whole path, in order: accounts → Neon databases → keys → environment → migrations → shards → smoke test →
workers → bulk ingestion → monitoring. Every StealthLab command below exists in this repo (run from `backend/` unless noted).
The Neon, Cloudflare, Google Cloud and GitHub click-paths are written from general knowledge of those products; their
dashboards change, so treat the *setting names* as what to look for, not exact menu positions.

> **Never point tests or benchmarks at the production database.** They write rows. Use a throwaway database for those
> (section 15).

---

## 0. What you are setting up

```
                      ┌────────────────────────────────────────────────┐
  raw sources  ─────► │ ingestion_jobs queue  (in the CONTROL database) │
  (enqueue CLI)       └───────────────┬────────────────────────────────┘
                                      │ leased by any number of workers (Cloud Run / GitHub Actions / Oracle / laptop)
                                      ▼
  Gemini / Voyage (embeddings)   worker ──► judge chain JEV → Gemini → Gemma   (identity decisions, never cosine/simhash)
  object storage (large payloads)         │
                                          ▼
   CONTROL DB = shard K000 ── routing tables, search projections, queue, private/org data, its share of public knowledge
   SHARD DBs  K001, K002 …  ── more public canonical knowledge (procedures, goals, claims, evidence), routed by the control DB
```

* **The control database** holds the queue, the shard registry and routing tables, the global search projections
  (`goal_search_index`, `procedure_search_index`, `claim_search_index`), audit tables, and shard **K000**'s share of the
  canonical data. Private and org data **always** stays on K000.
* **Shards** are extra databases with the *same schema*, created by running the same migrations. Public canonical knowledge is
  spread over the active shards by rendezvous hashing.
* **Workers** are the same program everywhere. They need a database URL and provider keys, nothing else.
* **End users do not need Postgres.** They install a thin MCP client and connect to the hosted MCP server with a URL and a
  token (section 13). Only you (self-hosting) need Postgres.

You do **not** need the frontend for ingestion.

---

## 1. Accounts and tools checklist

| Need | Why | Required? |
|---|---|---|
| Neon account | Postgres with `pgvector` (control DB + shard DBs) | yes (or any Postgres 15+ with pgvector) |
| Google AI Studio key (Gemini) | embeddings + judge fallback | yes (see 4) |
| Voyage AI key | alternate embedding provider (`voyage-3-large`, 1024-dim) | recommended as the fallback embedder |
| JEV service URL + key | preferred semantic judge | optional but preferred; Gemini/Gemma serve if absent |
| Object storage (Cloudflare R2, S3, or MinIO) | raw payloads > 64 KiB and preserved script bytes | yes for real repos/skill packages with big files |
| A worker host: Google Cloud Run **or** GitHub Actions **or** an Oracle/any Docker VM **or** your laptop | runs `python -m app.ingestion.worker` | pick one |
| A host for the API/MCP server (Railway, Cloud Run, a VM) | serves users and the frontend | needed before users connect, not for ingestion itself |
| `git`, Python 3.12+ (3.14 works locally), `pip`, `psql` (Postgres client) | run the CLIs and inspect the DB | yes |

Local machine setup (once):

```bash
git clone https://github.com/3Founders/stealth-lab && cd stealth-lab/backend
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-objectstore.txt                 # boto3, only needed for s3:// / R2 object storage
```

---

## 2. Neon: create the databases

### 2.1 Decide the layout

Recommended for production: **one Neon project per shard**, so each shard has its own storage, compute and backup history.

| Database | Neon project | Registered as |
|---|---|---|
| Control (= shard K000) | `stealth-control` | built in (`K000`, no env var) |
| Shard 1 | `stealth-k001` | `K001` via env var `K001_DATABASE_URL` |
| Shard 2 … | `stealth-k002` … | `K002` via `K002_DATABASE_URL` |

You can start with **only the control database**. With no remote shard registered everything lives on K000 and there is no extra
lookup cost. Add shards when the control DB approaches your plan's storage/compute limit (section 8). Adding a shard later does
not move existing rows; it only affects where *new* public knowledge lands.

### 2.2 Create each project

1. neon.tech → **New Project**. Pick the **region closest to your workers** (a worker in `us-east-1` and a database in
   `eu-central-1` adds ~100 ms to every query, and ingestion is chatty). Postgres version **16 or 17**.
2. Name it (`stealth-control`). Keep the default database (`neondb`) or rename it `stealth`.
3. **Enable/verify pgvector:** in the Neon SQL editor (or `psql`), run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE EXTENSION IF NOT EXISTS pgcrypto;
   CREATE EXTENSION IF NOT EXISTS btree_gist;
   SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pgcrypto','btree_gist');
   ```
   All three are available on Neon. The migrations run the same `CREATE EXTENSION IF NOT EXISTS` statements, but the role that
   runs them needs permission; the default project owner role does. HNSW indexes (`goal/procedure/claim_search_index`) need
   pgvector ≥ 0.5, which current Neon provides.
4. **Compute settings** (Project → *Compute* / branch settings):
   * **Autosuspend:** turn it **off** (or set a long timeout) for the control DB while ingestion runs. A suspended compute
     takes seconds to wake, and idle-connection drops interact badly with long-held advisory-lock connections.
   * **Compute size:** start at 1–2 CU (4–8 GB). The vector indexes and queue polling want RAM. Scale up before a bulk run,
     not during.
   * **Autoscaling:** allow a range (e.g. 1–4 CU) on the control DB.
5. **Connection strings.** Neon gives two hostnames per database: the **direct** one and the **pooled** one (contains
   `-pooler`). **Use the DIRECT (non-pooled) URL everywhere in this runbook** — migrations, workers, MCP/API, admin CLI:
   * the queue uses `FOR UPDATE SKIP LOCKED` and session-level advisory locks (per-source locks, goal reconciliation
     single-flight); the transaction-mode pooler does not preserve sessions, so locks and prepared statements break.
   * keep `?sslmode=require` on the URL (Neon requires TLS).
   ```
   postgresql://<role>:<password>@ep-xxxx.<region>.aws.neon.tech/<db>?sslmode=require
   ```
6. **Connection budget.** Each worker process opens up to `2 × lanes + 4` connections (see 10.1). With 4 lanes that is 12 per
   process; 8 Cloud Run tasks = 96. Check your Neon plan's connection limit (it scales with compute size) and size
   `INGEST_WORKER_CONCURRENCY` × process count to stay under ~70 % of it. The API/MCP server adds its own pool.
7. **IP allow-list** (if you enabled it): add the egress IPs of your worker host. Cloud Run and GitHub Actions do **not** have
   stable IPs by default; leave the allow-list off and rely on the long random password + TLS, or route through a static-IP NAT.
8. **Backups:** Neon keeps point-in-time history (window depends on plan) and lets you create a **branch** from any point.
   Before the first migration on a database that already has data, create a branch named `pre-migration-98` so you can roll back
   (section 11.3).

Repeat for each shard project.

### 2.3 Keep the URLs out of the repo

Put URLs in `backend/.env` locally (already git-ignored — verify with `git check-ignore backend/.env`) and in your host's secret
store in production. **Never commit a connection string.**

---

## 3. Provider keys

| Provider | What StealthLab uses it for | Where to get it | Env var |
|---|---|---|---|
| Google Gemini | embeddings (`gemini-embedding-001`, first in `EMBEDDING_PROVIDER_CHAIN=gemini,voyage`) and semantic judge fallback | aistudio.google.com → *Get API key* | `GEMINI_API_KEY` (or several: `GEMINI_API_KEYS=k1,k2,…` to spread rate limits) |
| Voyage AI | embeddings fallback (`voyage-3-large`) | dash.voyageai.com → *API keys* | `VOYAGE_API_KEY` |
| JEV | preferred judge for identity/applicability | your JEV deployment | `JEV_BASE_URL`, `JEV_API_KEY`, `JEV_CAPABILITIES` |
| Local Gemma | last-resort judge | your local/served Gemma | `LOCAL_MODEL_PROVIDER`, `LOCAL_MODEL_NAME` |

Notes that matter:

* **Embedding dimension must be 1024** (the schema is `VECTOR(1024)`). The configured embedders are set up for 1024. Do not
  switch to a model with another dimension without altering the column and re-embedding everything.
* **Never mix embedding models within one index silently.** Every stored vector is stamped with its model/version and every
  ANN query filters on it. If you change `EMBEDDING_PROVIDER_CHAIN` mid-corpus, run `python -m app.ingestion.admin reindex all`.
* **JEV capabilities:** set `JEV_CAPABILITIES=applicability,identity` only once your JEV service actually exposes
  `/judge-identity`. Until then leave it unset; identity and retrieval judgments are served by Gemini/Gemma (the chain skips
  JEV for capabilities it doesn't advertise). JEV is preferred, never required.
* **Judge outage behaviour (so you know what "healthy" looks like):** public goals/claims/procedures **fail and retry**
  (never guessed); private claims are created and later re-judged by `reconcile-claims`.
* **Rate limits:** if Gemini returns 429s, add more keys to `GEMINI_API_KEYS` or lower worker lanes. The workers back off and
  retry; nothing is lost, it only gets slower.

---

## 4. Object storage (Cloudflare R2 shown; S3/MinIO are the same shape)

Used for: job payload strings > 64 KiB (offloaded at enqueue, content-addressed by sha256, verified when the worker reads
them) and the bytes of preserved scripts (`ingested_artifacts.content_ref`). Without it, payloads > 1 MiB are **refused** at
enqueue and script bytes are not stored (only their hash/locator).

1. Cloudflare dashboard → **R2** → *Create bucket* `stealth-raw` (private; no public access).
2. **Manage R2 API Tokens** → create a token with **Object Read & Write** limited to that bucket. Save the *Access Key ID*,
   *Secret Access Key* and the account's S3 endpoint `https://<accountid>.r2.cloudflarestorage.com`.
3. Env vars:
   ```
   OBJECT_STORAGE_URL=s3://stealth-raw/prod
   OBJECT_STORAGE_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
   AWS_ACCESS_KEY_ID=…
   AWS_SECRET_ACCESS_KEY=…
   AWS_DEFAULT_REGION=auto
   ```
4. `pip install -r requirements-objectstore.txt` (boto3) — already done in section 1, and add it to the worker image (10.2).
5. In PRODUCTION `memory://` is refused and `OBJECT_STORAGE_URL` is required; for a single-machine trial use
   `OBJECT_STORAGE_URL=file:///var/stealth/blobs`.

---

## 5. The environment file

Create `backend/.env` (start from `backend/.env.example`). Minimum for ingestion:

```dotenv
# --- databases (DIRECT Neon URLs, sslmode=require) ---
CONTROL_DATABASE_URL=postgresql://…@ep-control….neon.tech/stealth?sslmode=require
DATABASE_URL=postgresql://…same as CONTROL_DATABASE_URL…          # legacy name several scripts still read (migrate.py)
# one variable per shard; the registry stores only the NAME of the variable (section 8)
# K001_DATABASE_URL=postgresql://…@ep-k001….neon.tech/stealth?sslmode=require

# --- environment ---
STEALTHLAB_ENV=PRODUCTION            # default when unset; requires a judge provider AND an embedding provider

# --- embeddings + judge ---
GEMINI_API_KEY=…                     # or GEMINI_API_KEYS=k1,k2
VOYAGE_API_KEY=…
# JEV_BASE_URL=https://…   JEV_API_KEY=…   JEV_CAPABILITIES=applicability,identity   # only when JEV serves /judge-identity

# --- object storage ---
OBJECT_STORAGE_URL=s3://stealth-raw/prod
OBJECT_STORAGE_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=…
AWS_SECRET_ACCESS_KEY=…
AWS_DEFAULT_REGION=auto

# --- worker tuning (defaults shown) ---
INGEST_WORKER_CONCURRENCY=4          # lanes per process; DB connections per process = 2*lanes+4
INGEST_LEASE_SECONDS=300
INGEST_JOB_TIMEOUT_SECONDS=1200
INGEST_MAX_ATTEMPTS=5
INGEST_RECONCILE_GOALS=1             # judge concurrent paraphrase goals after each batch
INGEST_RECONCILE_CLAIMS=1            # re-judge claims created during a judge outage after each batch
PROJECTION_DRAIN_ENABLED=1           # API/MCP process drains the search-projection outbox in the background

# --- API / MCP server (needed when you deploy it, section 13) ---
STEALTHLAB_MCP_TOKEN=…               # python -c "import secrets; print(secrets.token_urlsafe(32))"
FRONTEND_ORIGIN=https://your-frontend
GOVERNANCE_ENABLED=true
DAILY_LLM_BUDGET_USD=…               # spend cap; on by default, set a deliberate number
PRIVATE_VISIBILITY_ENABLED=false     # stays false until real auth exists; REAL_AUTH_ENABLED=false too
```

Rules: `CONTROL_DATABASE_URL` wins over `DATABASE_URL` for the ingestion tools; `migrate.py` reads `DATABASE_URL` (or `--dsn`).
`STEALTH_REMOTE_SHARD_WRITES` defaults to on (`1`); set `0` to stop placing new public knowledge on remote shards without a
deploy. Private and org rows never leave K000 regardless.

---

## 6. Migrations (control database)

Migrations `01 … 98` are idempotent and ledgered; running twice is safe.

```bash
cd backend
python scripts/migrate.py --dsn "$CONTROL_DATABASE_URL" --status      # what is applied / pending
python scripts/migrate.py --dsn "$CONTROL_DATABASE_URL" --dry-run     # what would run
python scripts/migrate.py --dsn "$CONTROL_DATABASE_URL"               # apply
```

What the latest ones do (so you know what you are approving):

| Migration | Effect |
|---|---|
| 94 | trace correlation ids on `execution_run_events` |
| 95 | shard registry, `object_routes`, search projections + HNSW/GIN indexes, `identity_decisions`, `retrieval_decisions`, queue lease columns; **backfills routes and the projection outbox for existing rows** (replayable) |
| 96 | replaces cross-database foreign keys with route-aware triggers; `goal_names` global exact-goal index; `raw_objects` |
| 97 | `procedures.source_locator` |
| 98 | **removes the Implementation object**: archives every legacy implementation row into `legacy_implementation_fold`, then drops the implementation tables/columns; adds step bindings, `step_execution_telemetry`, `procedures.source_artifacts`, artifact columns on `ingested_artifacts` |

On a **database that already has data** (for example the current hosted Supabase/Neon copy): create a Neon branch first
(2.2 step 8), then migrate. After 98, if the archive has rows, fold them (section 12).

Sanity checks after migrating:

```bash
psql "$CONTROL_DATABASE_URL" -c "select count(*) from knowledge_shards"          # K000 exists
psql "$CONTROL_DATABASE_URL" -c "select extname from pg_extension order by 1"    # vector, pgcrypto, btree_gist present
psql "$CONTROL_DATABASE_URL" -c "select to_regclass('implementations'), to_regclass('legacy_implementation_fold')"
python -m app.ingestion.worker --validate-config                                 # exit 0 = config + DB + providers OK
python -m app.ingestion.admin status                                             # queue empty, projection lag 0
```

---

## 7. Migrations (each shard)

A shard is just another database with the same schema:

```bash
python scripts/migrate.py --dsn "$K001_DATABASE_URL"
```

`register-shard` (next section) verifies the schema before it accepts the shard, so a half-migrated database cannot be
registered by mistake.

---

## 8. Register shards and choose placement

```bash
export K001_DATABASE_URL="postgresql://…"                 # the variable must be set in the shell running this
python -m app.ingestion.admin register-shard K001 --dsn-env K001_DATABASE_URL --weight 100
python -m app.ingestion.admin shards                       # registry: id, status, weight
python -m app.ingestion.admin verify-refs                  # every routed object exists on its shard (exit 1 if not)
```

Only the *name* `K001_DATABASE_URL` is stored in the database; every process that needs to read or write that shard
(workers, API/MCP) must have that variable in its own environment.

Placement facts:

* New **public** goals are placed by weighted rendezvous hashing over shards with status `active`. Goal-local procedures and
  claims prefer their goal's shard.
* **K000 also has a weight** (`knowledge_shards.weight`, default 100). With K000 = 100 and K001 = 100, new public data splits
  roughly 50/50. To send all *new* public data to the remote shards, set K000's weight to 0:
  `psql "$CONTROL_DATABASE_URL" -c "update knowledge_shards set weight = 0 where shard_id = 'K000'"` (weight 0 = never chosen for
  new public placement; existing rows stay, and private/org data still lands on K000).
* Rollover: `python -m app.ingestion.admin shard-status K001 full` stops **new** placements on K001 but keeps reading it.
  Existing objects never move. `readonly` and `unhealthy` are also available.
* A shard that is unreachable makes retrieval **partial** (it reports `unavailable_shards` and `degraded=true`); it never treats
  the missing candidates as nonexistent. Writers and verifiers are strict (they fail rather than skip).

Starting recommendation: **run with K000 only for the first ingestion**, prove the pipeline, then add K001 before the corpus
grows. Adding a shard is a config change, not a migration of data.

---

## 9. Providers reachable? (dry checks)

```bash
python -m app.ingestion.worker --validate-config
```

Exit codes: `0` ok, `2` configuration problem (it prints the reasons: missing semantic provider, missing embedding provider,
`memory://` storage in production, no database). Fix each line it prints, re-run until it exits 0.

Optional live check that the judge chain answers (costs a few tokens):

```bash
python - <<'PY'
import asyncio
from app.services.semantic.chain import SemanticJudge
async def main():
    j = SemanticJudge.from_settings()
    r = await j.judge_identity("goal", "find callers of a function", "locate every call site of a function")
    print(r.ok, r.value, r.provider, r.model)
asyncio.run(main())
PY
```

---

## 10. Where workers run

Pick **one** to start. They all run the same command and the queue in Postgres coordinates them, so you can mix hosts later.

### 10.1 Local (fastest for the first smoke test)

```bash
python -m app.ingestion.worker --once                        # drain the queue, then exit
python -m app.ingestion.worker --loop --concurrency 4        # stay up and poll
```
PowerShell wrapper: `deploy/ingestion/local/run-worker.ps1`.

Pool size rule: DB connections per process = `2 × lanes + 4`. Scale by **process count**, not lanes; each lane mostly waits on
a model call.

### 10.2 Google Cloud Run job (recommended for bulk)

1. Enable APIs: Cloud Run, Artifact Registry, Secret Manager, Cloud Build. Create an Artifact Registry Docker repo `stealth`.
2. Build and push the image (from the **repo root**):
   ```bash
   gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/stealth/ingest-worker:latest \
       --config <(echo 'steps: [{name: gcr.io/cloud-builders/docker, args: [build,-f,deploy/ingestion/Dockerfile.worker,-t,REGION-docker.pkg.dev/PROJECT/stealth/ingest-worker:latest,.]}]
   images: [REGION-docker.pkg.dev/PROJECT/stealth/ingest-worker:latest]')
   ```
   (Or `docker build -f deploy/ingestion/Dockerfile.worker -t … .` locally and `docker push`.) The image needs `boto3`: add
   `RUN pip install --no-cache-dir -r backend/requirements-objectstore.txt` after the requirements line in the Dockerfile.
3. Secrets (Secret Manager): `stealth-control-db-url`, `stealth-gemini-key`, `stealth-voyage-key`, `stealth-jev-url`,
   `stealth-jev-key`, the R2 keys, and one secret per shard URL (`stealth-k001-db-url` → env `K001_DATABASE_URL`). Give the
   job's service account *Secret Manager Secret Accessor*.
4. Edit `deploy/ingestion/cloudrun/job.yaml`: set the image, region, add the `OBJECT_STORAGE_*`/`AWS_*` env entries and every
   `K00N_DATABASE_URL` shard entry, then:
   ```bash
   gcloud run jobs replace deploy/ingestion/cloudrun/job.yaml --region REGION
   gcloud run jobs execute stealth-ingest-worker --region REGION --tasks 8        # 8 independent workers
   ```
5. Optional steady drain: the Cloud Scheduler command at the bottom of `job.yaml`.
6. Cloud Run job timeouts (3600 s) must exceed `INGEST_JOB_TIMEOUT_SECONDS`; a task that dies simply stops and its leases
   expire, other tasks pick the jobs up.

### 10.3 GitHub Actions batch runner (no infrastructure)

Repo → *Settings → Secrets and variables → Actions*: add `CONTROL_DATABASE_URL`, `GEMINI_API_KEY` (and `GEMINI_API_KEYS`),
`VOYAGE_API_KEY`, `JEV_BASE_URL`, `JEV_API_KEY`, plus `OBJECT_STORAGE_URL`, `OBJECT_STORAGE_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY` and one `K00N_DATABASE_URL` secret per shard (add them to the workflow's `env:` block as well — the
checked-in workflow only lists the first five). Then *Actions → "Ingestion worker batch" → Run workflow* (workers, lanes,
max_jobs, optional manifest to enqueue first). Actions is compute only; cancel or re-run freely.

### 10.4 Oracle VM / any Docker host

```bash
deploy/ingestion/oracle/run-worker.sh --loop         # builds/runs the same image with an env file; systemd unit in its footer
```

---

## 11. Before the first real ingestion

### 11.1 Snapshot

Create a Neon **branch** of the control DB (and each shard) named `pre-first-ingest`. It is instant and copy-on-write.

### 11.2 Fold the legacy implementations (only if the database had any)

```bash
psql "$CONTROL_DATABASE_URL" -c "select count(*) total, count(folded_procedure_id) folded from legacy_implementation_fold"
python -m app.ingestion.admin fold-implementations            # converts archived rows to step bindings / one-step procedures
python -m app.ingestion.admin fold-implementations            # re-run: only unfolded rows are attempted
```
Exit `1` lists rows that failed (they stay unfolded). Nothing is folded into an executable or verified state. When
`total = folded`, you can `drop table legacy_implementation_fold`.

### 11.3 Rollback plan

* Bad migration on a branch you made → restore from the Neon branch (*Restore* / point-in-time) and re-run after fixing.
* Bad ingestion batch → `python -m app.ingestion.admin status` to see counts; stop workers; cancel pending jobs
  (`update ingestion_jobs set status='cancelled' where status in ('pending','retryable_failed')`). Rows already committed
  stay (ingestion is idempotent and re-runnable); to undo them use the Neon branch from 11.1.

### 11.4 Backfill projections for existing data (only if the DB already had procedures/goals/claims)

Migration 95 enqueues them; drain and verify:

```bash
python -m app.ingestion.admin drain-projections
python -m app.ingestion.admin verify-projections            # exit 0 required
python -m app.ingestion.admin reindex all                   # only if verify fails or you changed the embedding provider
```

---

## 12. The smoke test (do this before bulk)

### 12.1 One skill package

```bash
python -m app.ingestion.enqueue skill-package --source-id anthropic-skills --repo anthropics/skills \
    --commit <a real commit sha> --path skills/pdf/SKILL.md
python -m app.ingestion.admin status                              # 1 pending
python -m app.ingestion.worker --once
python -m app.ingestion.admin status                              # 1 done, 0 failed
python -m app.ingestion.admin failures --limit 5                  # empty
```

`skill-package` jobs write **global public** knowledge; they are refused unless the job's scope is global/public.

### 12.2 One already-extracted bundle with an explicit scope

```bash
python -m app.ingestion.enqueue raw --job-type ingest_candidate_bundle --idempotency-key smoke:1 \
  --payload '{"source_key":"smoke:1","source_uri":"https://example.test/smoke","goal":"validate a repository with the discovered script",
              "procedure":{"name":"smoke procedure","steps":[{"description":"run the validator"}]},"claims":[]}' \
  --scope-type global --visibility public
python -m app.ingestion.worker --once
```

### 12.3 Run it twice (idempotency)

Enqueue the same command again (same idempotency key), run the worker: the job count is unchanged and there is still **one**
procedure and one goal for that source.

### 12.4 Check the results

```bash
python -m app.ingestion.admin verify-projections        # 0
python -m app.ingestion.admin verify-dedup              # 0 (no duplicate goal names, no unlinked procedures)
python -m app.ingestion.admin verify-refs               # 0 (only meaningful with shards)
psql "$CONTROL_DATABASE_URL" -c "select decision, count(*) from identity_decisions group by 1"
psql "$CONTROL_DATABASE_URL" -c "select id, name, source_locator->>'uri' as uri, jsonb_array_length(steps) as steps from procedures order by t_created desc limit 5"
psql "$CONTROL_DATABASE_URL" -c "select role, execution_allowed, admission_decision, extraction_status from ingested_artifacts order by first_seen desc limit 5"
```
Scripts found in a skill package must appear as `role = executable_source`, `execution_allowed = f` (preserved but **not
executable** until screened).

### 12.5 Retrieval works end to end

Start the API and ask for the thing you just ingested:

```bash
uvicorn app.main:app --port 8000 &                      # or `uvicorn app.mcp_server.server:app --port 8765` for the MCP server
curl -s -X POST localhost:8000/v1/search/recommend -H 'content-type: application/json' \
     -d '{"goal":"validate a repository with the discovered script"}' | python -m json.tool | head -40
```
Expect a `goal_resolution` with your goal and `retrieval.mode = "jev"` or `"model"` (not `candidates_only`). Set
`STEALTHLAB_ENV=STAGING` if you are testing without a judge provider; retrieval then reports `degraded`.

---

## 13. Deploy the API / MCP server (so users can connect)

Not needed for ingestion, needed before users connect.

* **Existing deploy:** `backend/Procfile` / `backend/railway.json` run `uvicorn app.main:app` on Railway with health check
  `/health`. The MCP server image is `backend/Dockerfile` (port 8765, `uvicorn app.mcp_server.server:app`). Both need
  `git` in the image (already there): `find_best_way` uses `git diff` from the caller's repo path.
* **Environment for the server:** everything in section 5 that is not worker-only: database URLs (control + every `K00N_…`),
  provider keys, object storage, `STEALTHLAB_MCP_TOKEN` (HTTP mode refuses to start without it), `FRONTEND_ORIGIN`,
  governance/budget variables. One process per Neon connection budget: the server's pool adds to the workers'.
* **Users do not need Postgres or any keys.** They add the MCP server URL and their token to their agent:
  ```
  claude mcp add stealthlab --transport http https://<your-host>/mcp --header "Authorization: Bearer <token>"
  ```
  (or the equivalent config block in their client). The local `.stealth` markdown cache MCP is independent of all of this.
* The server drains the search-projection outbox in the background (`PROJECTION_DRAIN_ENABLED`, `PROJECTION_DRAIN_INTERVAL_SECONDS`).

---

## 14. Bulk ingestion

Ramp up, don't jump:

1. **10 packages** — watch `status`, `failures`, provider cost, latency per job.
2. **100 packages** — check `verify-*`, look at the goal names it produced (`select canonical_name from goals order by t_created desc limit 50`).
3. **Bulk** — a manifest with one JSON object per line and idempotent re-runs:
   ```bash
   python -m app.ingestion.enqueue skill-package --manifest packages.jsonl       # {"source_id","repo","commit","path"} per line
   gcloud run jobs execute stealth-ingest-worker --region REGION --tasks 8      # or the GitHub Actions batch
   ```
   Raise `--tasks` (processes) rather than lanes. Keep total connections under your Neon limit (2.2 step 6).

While it runs:

```bash
watch -n 30 python -m app.ingestion.admin status
```
Healthy: `expired_leases` ≈ 0 outside a crash, `projection_lag.pending` drains to 0, `failures` is empty or provider-transient.

After each big batch:

```bash
python -m app.ingestion.admin verify-projections
python -m app.ingestion.admin verify-dedup
python -m app.ingestion.admin reconcile-claims            # workers already do this; run to force it
python -m app.ingestion.admin reconcile-goals             # workers already do this for recent goals
```
Once, on a database that had goals before this system: `python -m app.ingestion.admin reconcile-goals --all` (whole corpus,
costs judge calls, run off-peak).

Retry / repair:

```bash
python -m app.ingestion.admin retry --job-types ingest_candidate_bundle       # failed/retryable → pending
python -m app.ingestion.admin drain-projections
python -m app.ingestion.admin reindex procedure --shard K001
```

---

## 15. Tests and benchmarks (on a throwaway database only)

Create a **separate Neon project or branch** (never the production branch), or a local Postgres with pgvector:

```bash
python scripts/migrate.py --dsn "<throwaway control>"
python scripts/migrate.py --dsn "<throwaway shard>"
export DATABASE_URL="<throwaway control>"  TEST_SHARD_DATABASE_URL="<throwaway shard>"
python -m pytest tests -q                                   # ~17 min; needs the same provider keys for the live-LLM tests
python scripts/benchmark_projection_scale.py --goals 100000 --procedures 20000 --queries 200
```
A clean database matters: leftover rows from earlier runs make a few identity tests fail (they assume an empty corpus).
Tests that need a live LLM are the only ones expected to fail without provider keys.

---

## 16. Troubleshooting (Neon-specific first)

| Symptom | Cause / fix |
|---|---|
| `prepared statement … does not exist`, advisory-lock errors, jobs never lease | you used the **pooled** (`-pooler`) URL. Use the direct URL. |
| first query after idle takes seconds, or `connection terminated` | Neon autosuspend. Disable it (or lengthen) for the control DB during ingestion. |
| `too many connections` / `remaining connection slots are reserved` | connections = processes × (2·lanes + 4) + server pools exceeds the plan limit. Lower `INGEST_WORKER_CONCURRENCY` or `--tasks`, or raise compute. |
| `CONFIG ERROR: no semantic provider configured` | set `GEMINI_API_KEY` (or JEV/Gemma) or use `STEALTHLAB_ENV=STAGING` for a dry run. |
| `no embedding provider` | set `GEMINI_API_KEY` or `VOYAGE_API_KEY`. |
| `OBJECT_STORAGE_URL=memory:// is not allowed in PRODUCTION` | configure R2/S3 (section 4). |
| enqueue refused: "payload … no OBJECT_STORAGE_URL is configured" | payload > 1 MiB and no store; configure object storage. |
| `CREATE EXTENSION vector` permission denied | run migrations with the project owner role, not a restricted role. |
| jobs stuck `retryable_failed` | read `last_error`; provider outage → they back off up to 30 min on their own; `admin retry` re-queues sooner. |
| jobs `failed`: "lease expired after final attempt" | a worker crashed repeatedly; read its log, fix, `admin retry`. |
| `SemanticJudgmentUnavailable` | judge chain down; public writes wait and retry, private claims are created and re-judged by `reconcile-claims`. |
| `verify-projections` exit 1 | `admin drain-projections`, then `admin reindex all`; persistent failures are in `projection_outbox (status='failed', last_error)`. |
| retrieval `degraded: true`, `unavailable_shards` | a shard's URL variable is missing in that process or the shard is down. Set the variable / restore the shard. |
| `verify-refs` exit 1 | a route points at an object that isn't on its shard (interrupted write or wrong URL variable); check that the process points at the right database. |

---

## 17. Go-live checklist

- [ ] Neon control project (direct URL, autosuspend off, pgvector present); shard projects if used
- [ ] Branch snapshot taken before first migration and before first ingestion
- [ ] `backend/.env` / secret stores filled; no secret in git
- [ ] Migrations `01 … 98` applied to control and every shard; `--status` shows none pending
- [ ] Shards registered; `verify-refs` exit 0
- [ ] `python -m app.ingestion.worker --validate-config` exit 0
- [ ] Object storage configured and reachable
- [ ] `fold-implementations` finished (or archive empty)
- [ ] `verify-projections` and `verify-dedup` exit 0
- [ ] Smoke test (section 12) passed, including the idempotent re-run and a real retrieval
- [ ] Worker host chosen and one full `--once` run completed there
- [ ] Connection budget computed for the planned worker count
- [ ] Cost cap decided (`DAILY_LLM_BUDGET_USD`) and provider quotas checked
- [ ] Monitoring routine agreed (`admin status` / `failures` / `verify-*`)

## 18. Known gaps (so nothing surprises you)

* No live-provider ingestion run has been performed yet in the test environment (no credentials there); sections 9 and 12 are
  where that first real proof happens.
* Deployment templates for Cloud Run, GitHub Actions and Oracle were never executed on those platforms from this repo.
* Non-script repository files (style/design references) have validation and storage columns but no ingestion adapter yet.
* Private claims created during a judge outage **before** `reconcile-claims` existed are not swept.
* The stealth projection's faulted-in claim block still uses the older `## ` markdown style inside a pipe-format page.
