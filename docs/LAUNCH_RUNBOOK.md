# Launch runbook: from `main` to a running, sharded production

Do the steps **in order**. Each one ends with a **Check**; do not start the next step until that check passes.
Everything here runs from the repo root in **Git Bash** unless a step says otherwise.

What you end up with:

```
K000  control DB (Neon)      routing, Goal DAG, goal_search_index, jobs, economy, private data
B     search/log DB (Neon)   procedure_search_index, claim_search_index, retrieval_decisions, identity_decisions, llm_spend
K001..K100 (Neon, 500 MB)    public Goals and, next to each Goal, its Procedures and Claims
API + MCP (Railway)          read and write through all of the above
Workers (Cloud Run job)      ingestion
Website (prod_frontend)      talks to the API; points installers at the MCP URL
```

Two rules for the whole runbook:

* **Never paste a connection string into a command line, a chat, an issue or a commit.** Put it in
  `~/.stealth-ops/secrets.env`, which `stealth-ops` reads and distributes.
* Use **direct** (non-pooled) Neon URLs everywhere in this runbook. Migrations need them, and the app's pools use prepared statements.

---

## Part A: one-time preparation

### 1. Accounts and tools

You need:

- **Neon**
  - A paid plan that allows **102+ projects**: K000, B and K001..K100. Check this in Neon → Billing.
  - An **API key**: Neon → Account settings → API keys.
- **Google Cloud**
  - A project for the workers, with Cloud Run, Cloud Build, Artifact Registry and Secret Manager enabled.
  - Log in with `gcloud auth login`, then `gcloud config set project <id>`.
- **Railway**
  - The API service and the MCP service.
  - Log in with `railway login`.
- **GitHub CLI** (`gh auth login`) and an **npm** account, for publishing the installer.
- **Python and Node.js**
  - Python 3.11+ with the backend dependencies: `cd backend && pip install -r requirements.txt`.
  - Node 20+ for the website.

**Check:**

```bash
scripts/ops/stealth-ops --help
```

It prints the command list. On Windows cmd/PowerShell, use `scripts\ops\stealth-ops.cmd`.

### 2. Get the code

```bash
git checkout main
git pull
```

**Check:** `git log --oneline -1` shows `feat(ops): script to provision knowledge shards as Neon projects` or a later commit.

### 3. Create the secrets file

Create `~/.stealth-ops/secrets.env`. On Windows this is `C:\Users\<you>\.stealth-ops\secrets.env`. It is outside the repo and must never be committed.

```
GCP_PROJECT=<your gcp project id>
GCP_REGION=<e.g. us-east1, same continent as the Neon region>
GEMINI_API_KEY=...
GEMINI_API_KEYS=...            # optional: comma-separated extra keys
VOYAGE_API_KEY=...             # optional fallback embedder
JEV_BASE_URL=...               # optional preferred judge
JEV_API_KEY=...
DAILY_LLM_BUDGET_USD=10
# shard sizing for a 500 MB Neon project
OPS_SHARD_CAPACITY_BYTES=524288000
OPS_SHARD_WARN_BYTES=393216000        # 75 %: warn
OPS_SHARD_ROLLOVER_BYTES=445644800    # 85 %: stop placing new objects there
```

Set the Neon key **only in your shell**. It is used and never stored:

```bash
export NEON_API_KEY=<your neon api key>
```

**Check:** `scripts/ops/stealth-ops configure-secrets --target local` lists names only and does not report the Gemini key missing.

---

## Part B: databases

### 4. Snapshot what exists

If a production control database already exists and has data, snapshot it before touching anything:

```bash
scripts/ops/stealth-ops snapshot-prod --name pre-launch
```

This creates a copy-on-write Neon branch, which costs nothing until used. It never restores or deletes anything.

**Check:** the output names the snapshot. On a brand-new setup, skip this step.

### 5. Control database (K000)

- **New setup:** run `scripts/ops/stealth-ops provision-control`. It creates or finds the Neon project `stealth-control`, migrates it and writes `CONTROL_DATABASE_URL` into the secrets file.
- **Existing control DB:**
  1. Add `CONTROL_DATABASE_URL=<its direct url>` to `~/.stealth-ops/secrets.env`.
  2. Run the migrations:

     ```bash
     set -a; source ~/.stealth-ops/secrets.env; set +a
     cd backend
     DATABASE_URL="$CONTROL_DATABASE_URL" python scripts/migrate.py
     ```

**Check:** `cd backend && DATABASE_URL="$CONTROL_DATABASE_URL" python scripts/migrate.py --status` shows no pending migrations. The newest applied migration is `119_goal_commitments_escrow.sql` or later.

### 6. Search/log database (project B)

1. Create a Neon project named `stealth-search` in the **same region** as the control DB (Neon console → New project).
2. Copy its **direct** connection string and add it to the secrets file:

   ```
   SEARCH_DATABASE_URL=<direct url of stealth-search>
   ```

3. Create B's tables (migrations marked `-- target: search`):

   ```bash
   set -a; source ~/.stealth-ops/secrets.env; set +a
   cd backend
   python scripts/migrate.py --target search --dsn "$SEARCH_DATABASE_URL"
   ```

Do **not** backfill yet. That happens in step 14, after every process knows about B.

**Check:** `python scripts/migrate.py --target search --dsn "$SEARCH_DATABASE_URL" --status` shows nothing pending.

### 7. Create the knowledge shards K001..K100

Use `stealth-ops provision-shards`. It creates or finds a Neon project per shard (`stealth-k001`, …), migrates it, checks the extensions and registers it in `knowledge_shards` with the capacity from `OPS_SHARD_CAPACITY_BYTES`. It writes each `K0xx_DATABASE_URL` into the secrets file, where `configure-secrets` and `deploy-workers` pick them up.

> `backend/scripts/provision_neon_shards.py` does the same thing standalone: it names projects `stealthlab-k001` and writes `backend/.neon_shards.env`.
> **Use one tool or the other, never both.** Mixing them creates two Neon projects per shard.

First try **two** shards, registered with weight 0 so no data goes there yet:

```bash
set -a; source ~/.stealth-ops/secrets.env; set +a
scripts/ops/stealth-ops provision-shards --count 2 --weight 0
scripts/ops/stealth-ops verify-shards
```

When that is clean, do the rest. The command is idempotent: existing shards are verified, not re-created.

```bash
scripts/ops/stealth-ops provision-shards --count 100 --weight 0
```

If it stops part-way (rate limit, network), **run the same command again**. It resumes.

**Check:**

- `scripts/ops/stealth-ops verify-shards` shows 100 shards (plus K000), all reachable, with no pending migrations.
- `grep -c '^K[0-9][0-9][0-9]_DATABASE_URL=' ~/.stealth-ops/secrets.env` prints `100`.

### 8. Worker credential

```bash
scripts/ops/stealth-ops mint-worker-token
```

Workers refuse to start without this credential. It expires after 7 days by default (`--ttl-hours` changes that). Put a reminder in your calendar to re-run this command, followed by step 10.

**Check:** the secrets file now contains `INGEST_SERVICE_TOKEN` and `SERVICE_TOKEN_KEYS`.

### 9. Optional: object storage and observability

```bash
scripts/ops/stealth-ops setup-object-storage       # needs OBJECT_STORAGE_URL + AWS_* in the secrets file
scripts/ops/stealth-ops configure-observability --otlp-endpoint https://... --otlp-headers "Authorization=Basic ..." --sentry-dsn https://...
```

Skip both if you do not use them yet. Neither blocks ingestion.

### 10. Push the secrets to Google Cloud and GitHub

```bash
scripts/ops/stealth-ops configure-secrets --target gcp --target github --target local
scripts/ops/stealth-ops configure-secrets --target gcp --target github --target local --apply
```

The first command only checks. `--apply` asks before it writes anything. The secrets pushed include:

- one per shard, `stealth-k0xx-db-url`;
- `stealth-search-db-url` for B;
- `stealth-control-db-url`.

At the end the command **prints IAM commands** without running them. Run them yourself so the Cloud Run job's service account can read the secrets.

**Check:** re-run the first command. It reports nothing missing for `gcp` or `github`.

---

## Part C: deploy the services

### 11. Set the environment on Railway (API service **and** MCP service)

Both services need the control DB, B and **every** shard URL. Print the database lines to paste:

```bash
grep -E '^(K[0-9]{3}_DATABASE_URL|SEARCH_DATABASE_URL)=' ~/.stealth-ops/secrets.env
```

In Railway, open each service → **Variables → Raw Editor**, and paste those lines plus the variables below.

| Variable | API | MCP | Value |
|---|---|---|---|
| `DATABASE_URL` | ✓ | ✓ | the control DB direct URL (same value as `CONTROL_DATABASE_URL`) |
| `SEARCH_DATABASE_URL` | ✓ | ✓ | from the paste above |
| `K001_DATABASE_URL` … `K100_DATABASE_URL` | ✓ | ✓ | from the paste above |
| `GEMINI_API_KEY`, `VOYAGE_API_KEY`, `JEV_BASE_URL` | ✓ | ✓ | as in the secrets file |
| `DAILY_LLM_BUDGET_USD`, `SERVICE_TOKEN_KEYS` | ✓ | ✓ | same values as the workers |
| `STEALTHLAB_MCP_PUBLIC_URL` | | ✓ | `https://mcp.<your-domain>/mcp` |
| `STEALTHLAB_MCP_TOKEN` | | ✓ | a long random string |
| `DEPLOYMENT_MODE` | | ✓ | `shared` |
| `OIDC_ISSUER` / `OIDC_AUDIENCE` | ✓ | ✓ | your Supabase issuer / `authenticated` |
| `MCP_WORKER_COUNT` | | ✓ | `1` |
| `STEALTH_SHARD_READ_TIMEOUT_S` | optional | optional | per-shard read timeout; default is fine |

The full variable reference for the API is in [railway-deployment.md](railway-deployment.md) §3. For the MCP server, see [deploy/hosted-mcp.md](deploy/hosted-mcp.md).

**Check:** in both services, the variable count grew by about 101.

### 12. Deploy the API and the MCP server

```bash
scripts/ops/stealth-ops promote staging production    # the production gate; skip only if you have no staging
scripts/ops/stealth-ops deploy-api                    # add --skip-gate if you skipped promote (your call)
```

The MCP service deploys from `railway.mcp.json`: push to its branch, or press **Deploy** in Railway.

- Its container runs the control-DB migrations on start.
- It does **not** migrate B or the shards. Steps 6 and 7 did those.

Keep **one replica** and attach the custom domain `mcp.<your-domain>`.

**Check:**

```bash
curl https://mcp.<your-domain>/
npx -y stealthlab-mcp doctor --url https://mcp.<your-domain>/mcp
```

Both succeed. The API's `/health` returns 200.

### 13. Deploy the ingestion workers

```bash
scripts/ops/stealth-ops deploy-workers --dry-run     # prints the gcloud commands and writes ~/.stealth-ops/rendered-job.yaml
scripts/ops/stealth-ops deploy-workers
```

The rendered job gets one secret reference per registered shard, plus `SEARCH_DATABASE_URL` when the secrets file has it.

**Check:**

- `~/.stealth-ops/rendered-job.yaml` contains `SEARCH_DATABASE_URL` and `K100_DATABASE_URL`.
- `gcloud run jobs describe ingest-worker --region $GCP_REGION` shows the new image.

### 14. Fill project B

Every process now reads and writes B. Copy the existing Procedure and Claim projections into it:

```bash
set -a; source ~/.stealth-ops/secrets.env; set +a
cd backend
DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin search-db-backfill
```

This reindexes the Procedure and Claim projections into B and then verifies them.

- Old log rows (`retrieval_decisions`, …) written before the switch stay on the control DB. That is harmless.
- **Do not unset `SEARCH_DATABASE_URL` later** without running `admin reindex all` against the control DB. Otherwise search reads an empty index.

**Check:** the command ends with the verification passing. Also run `DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin verify-projections`.

### 15. Turn on the shards

Shards have been registered with weight 0 so far. Give them traffic, and take new public Goals off the control DB:

```bash
set -a; source ~/.stealth-ops/secrets.env; set +a
cd backend
for i in $(seq 1 100); do DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin shard-weight "$(printf 'K%03d' $i)" 100; done
DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin shard-weight K000 0
```

Existing objects never move. Only **new** public Goals are placed on K001..K100, and their Procedures and Claims follow them.

**Check:** `DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin shards` lists K001..K100 as `active` with weight 100, and K000 with weight 0.

### 16. Website

In the website's hosting settings (for `prod_frontend`), set:

```
NEXT_PUBLIC_KEL_API_URL=https://api.<your-domain>
NEXT_PUBLIC_KEL_MCP_URL=https://mcp.<your-domain>/mcp
NEXT_PUBLIC_KEL_SIGNIN_URL=...        # your sign-in entry point
```

Then **rebuild**. `NEXT_PUBLIC_*` values are baked in at build time.

**Check:** `/goals` lists Goals rather than the "not connected" state, and a Goal page shows the **Demand** panel.

### 17. Publish the installer

1. Put `https://mcp.<your-domain>/mcp` in three places:
   - `packaging/npm/package.json` → `stealthlab.defaultMcpUrl`
   - `install.sh` → `DEFAULT_URL`
   - `install.ps1` → `$DefaultUrl`
2. Commit and push.
3. Add the `NPM_TOKEN` secret to GitHub: `gh secret set NPM_TOKEN`, then paste the token when prompted.
4. Tag the release:

   ```bash
   git tag stealthlab-mcp-v0.1.0
   git push origin stealthlab-mcp-v0.1.0
   ```

**Check:** the publish workflow is green, and `npx -y stealthlab-mcp@0.1.0 doctor` works on a clean machine.

---

## Part D: people and scheduled jobs

### 18. Give reviewers access

Reviewers need the `reviewer` platform role, which grants `knowledge:publish`. That role lets them use `/review/hierarchy` and `/v1/goal-review`.

1. The reviewer signs in once, so their user row exists.
2. Run this against the **control DB** (Neon SQL editor):

   ```sql
   INSERT INTO platform_role_grants (user_id, role, granted_by)
   SELECT id, 'reviewer', 'launch-runbook' FROM users
   WHERE email = '<reviewer email>' AND t_expired IS NULL
   ON CONFLICT (user_id, role) DO UPDATE SET t_expired = NULL;
   ```

To revoke the role later: `UPDATE platform_role_grants SET t_expired = now() WHERE user_id = ... AND role = 'reviewer';`

**Check:** the reviewer opens `/review/hierarchy` and sees the queue, not "Reviewers only".

### 19. Scheduled jobs

Two jobs keep the databases inside 500 MB. Run both **daily**.

```bash
# 1) delete operational rows (retrieval_decisions, identity_decisions, …) older than 30 days
DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin prune-operational --older-than-days 30 --apply
# 2) measure shard sizes; mark shards over 85 % as "full" so new objects go elsewhere
scripts/ops/stealth-ops capacity --apply
```

Both commands also need `SEARCH_DATABASE_URL` and the `K0xx_DATABASE_URL` values in their environment.

- The simplest way to schedule them is a GitHub Actions cron workflow. It can use the same `SHARD_ENV` secret that `configure-secrets` created, the way `.github/workflows/ops-alerts.yml` already does.
- Alerts already run every 10 minutes through `ops-alerts.yml`.

Run each one by hand first, **without** `--apply`, to see what it would do.

**Check:** the dry runs print counts and sizes, and nothing unexpected is marked for deletion or `full`.

### 19b. Model recommender

This is optional; it can come after launch. Design: [model_routing_plan.md](model_routing_plan.md).

The tables already exist:
- `routing_*` on the control DB, created by migration 120 in step 5;
- the recommender's two log tables on project B, created by migration 121 in step 6.

The worker image installs `requirements-routing.txt` (JAX and NumPyro). The API and MCP images do not need it.

```bash
set -a; source ~/.stealth-ops/secrets.env; set +a
cd backend
A="python -m app.ingestion.admin"
# 1) prices (USD per million tokens) for every model you will recommend -- unpriced models are never recommended
DATABASE_URL="$CONTROL_DATABASE_URL" $A routing-price anthropic/claude-opus --input <in> --output <out> --cached <cached>
# 2) optional facts: version lineage (a new version starts from its predecessor), open weights, local
DATABASE_URL="$CONTROL_DATABASE_URL" $A routing-model google/gemma-4 --open-weights --local
# 3) seed data, if you have it: per-instance public benchmark results as JSONL
#    {"goal_id", "model", "scaffold", "instance_key", "accepted", "check_kind": "benchmark", "tokens_in"?, ...}
DATABASE_URL="$CONTROL_DATABASE_URL" $A routing-import results.jsonl
# 4) first fit, then schedule it DAILY next to step 19's jobs
DATABASE_URL="$CONTROL_DATABASE_URL" $A routing-refit
DATABASE_URL="$CONTROL_DATABASE_URL" $A routing-status
```

**Check:** `routing-status` shows an active parameter version. Its diagnostics show `divergences: 0` and `max_r_hat` below 1.05.

The MCP tools `recommend_models` and `report_model_run` are on both the default (v1) and the full (v2) surface.

---

## Part E: verify, then ingest

### 20. End-to-end checks

```bash
scripts/ops/stealth-ops doctor          # ordered preflight; non-zero exit = do not ingest yet
scripts/ops/stealth-ops smoke-test      # fixture ingest -> replay -> no duplicates -> retrieval
scripts/ops/stealth-ops verify-all      # projections + dedup + refs
```

Then check by hand:

1. In an MCP client, call `find_ways` with a real task. You get ranked Procedures back.
2. In B's SQL editor, `SELECT count(*) FROM retrieval_decisions WHERE t_created > now() - interval '10 minutes';` returns more than 0. Logging now goes to B.
3. After the smoke test, `SELECT shard_id, count(*) FROM object_routes GROUP BY 1;` on the control DB shows a new row on some K0xx shard, not only on K000.
4. On a Goal page, commit 1 Credit, then withdraw it. Your balance returns to where it started.

**Check:** all three commands exit 0, and the four manual checks behave as described.

### 21. First real ingestion: small first

```bash
scripts/ops/stealth-ops snapshot-prod --name pre-first-ingest
scripts/ops/stealth-ops ingest first10.jsonl --runner cloudrun --workers 2     # 10 packages
scripts/ops/stealth-ops watch                                                   # in a second terminal
scripts/ops/stealth-ops post-batch-verify
```

`ingest` stops on its own if the budget runs out or permanent failures pile up.

When 10 packages go through cleanly, move up to about 100, then to the full manifest with `--workers 8`.

**Check:** `post-batch-verify` passes, and `stealth-ops capacity` shows shard sizes growing evenly.

---

## Part F: local cleanup

### 22. Fix your local `.env`

The password in `DATABASE_URL_LOCAL` in `backend/.env` is stale. Update it to your local Postgres password, or point it at a Neon dev branch. Local tests and `dbtarget` scripts use it.

**Check:** `cd backend && python scripts/dbtarget.py local -- python scripts/migrate.py --status` connects and lists the migration ledger.

### 23. Test the Windows installer

On a Windows machine without the tool installed, run the installer from the website:

- once in **Windows PowerShell 5.1**;
- once in **PowerShell 7**.

**Check:** both finish, and `stealthlab-mcp doctor` reports the hosted URL as reachable.

---

## Later: every future migration

A new file in `backend/db/` must reach **every** database:

1. **Control DB:** `python scripts/migrate.py` with `DATABASE_URL` set to the control DB. The MCP container also does this on deploy.
2. **B:** needed only if the file starts with `-- target: search`: `python scripts/migrate.py --target search --dsn "$SEARCH_DATABASE_URL"`.
3. **Every shard:** `scripts/ops/stealth-ops provision-shards --count 100`. On existing shards this snapshots any shard with data, then migrates and verifies it. It never re-creates a project.
4. `scripts/ops/stealth-ops verify-shards` shows nothing pending anywhere.

## If something goes wrong

| Symptom | Do this |
|---|---|
| a shard is down or slow | `stealth-ops disable-shard K0xx`: no new placement; reads report it as `degraded`/`unavailable_shards`. Nothing is deleted. |
| a shard is near 500 MB | `stealth-ops capacity --apply` marks it `full`. Existing objects stay; new ones go to other shards. |
| search returns nothing after a deploy | a process is missing `SEARCH_DATABASE_URL`. Compare the variables across API, MCP and the worker job (step 11/13). |
| errors naming a `K0xx_DATABASE_URL` / a shard it cannot reach | that process lacks `K0xx_DATABASE_URL`. Re-paste the step 11 block, or re-run step 10 and 13. |
| workers refuse to start | the worker credential expired. Run `stealth-ops mint-worker-token`, then `configure-secrets --apply`. |
| need to undo data changes | restore from a `snapshot-prod` branch in the Neon console. This is manual on purpose; nothing restores or deletes automatically. |
