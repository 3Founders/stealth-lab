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

### What changed recently (read this if you used an earlier version of this runbook)

| Commit | What it adds | What you must do |
|---|---|---|
| `8c57638`, `7df90df` | Per-Goal model recommender; the MCP tools `recommend_models` and `report_model_run` (v1 and v2 surfaces) | Migrations 120 (control) and 121 (project B), then step 19b |
| `b71a234` | Goal ranking: demand only raises a Goal; lists and the roots view rank globally | Nothing beyond a normal deploy (API + website) |
| `4c67865` | Step-level routing (per run.md node) | Migrations 122 (control) and 123 (project B); `routing-refit` once; redeploy MCP (step 19b) |
| (verified solutions) | A Procedure's verified solution is kept on the provenance model: `source_locator` plus a new artifact role `verified_solution`; returned by `find_ways` as `verified_solution`. Flag `KNOWLEDGE_VERIFIED_EXAMPLES`, **off by default** | **Migration 124** (control DB and every shard; not project B). Set the flag only after 124 is everywhere |
| `4c72b5c` | Semantic judge fixes: JEV identity batches are split into chunks (it returned HTTP 400 above ~170k characters), replies wrapped in prose parse, and the Gemma fallback uses `GENERAL_COMPUTE_JUDGE_MODEL`. New `admin judge-health` command | **No migration.** Redeploy the API, MCP and workers. Run `judge-health` (step 20) |
| `865d0f8` | (1) Benchmark import (`benchmark-import`); (2) Procedures from verified code solutions; (3) `find_ways` offers judged ways from more specific Goals and lists ways on ambiguous candidates; (4) `recommend_models` constraint `allow_retries` | **No migration, no new variable.** Redeploy the API and MCP (step 12). Benchmark import is optional (step 19c) |

Details of each item are in the steps below. The newest migrations are **124** on the control DB and shards, and **123** on project B.

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

**Check:** `git log --oneline -1` shows `865d0f8 feat: benchmark import, code-solution Procedures, ways from more specific Goals, no-retry routing` or a later commit.

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
GENERAL_COMPUTE_API_KEY=...          # needed for skill-package ingestion and for Procedures from verified code solutions
GENERAL_COMPUTE_API_KEYS=...         # optional: comma-separated extra keys, rotated on 429
GENERAL_COMPUTE_JUDGE_MODEL=gemma-4-31B-it   # the extraction model; must exist in your General Compute catalog
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

**Check:** `cd backend && DATABASE_URL="$CONTROL_DATABASE_URL" python scripts/migrate.py --status` shows no pending migrations. The newest applied migration is `124_verified_solution_role.sql` or later. Migrations 121 and 123 start with `-- target: search`; they belong to project B (step 6) and are not applied here.

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

**Check:** `python scripts/migrate.py --target search --dsn "$SEARCH_DATABASE_URL" --status` shows nothing pending, and the newest applied migration is `123_step_routing_search.sql` or later.

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
| `GENERAL_COMPUTE_API_KEY`, `GENERAL_COMPUTE_JUDGE_MODEL` (+ optional `GENERAL_COMPUTE_API_KEYS`) | ✓ | ✓ | as in the secrets file. They must also reach the workers, which run extraction |
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

**Step-level routing** (per run.md node) was added by migrations 122 (control) and 123 (project B). On an existing deployment:
1. Apply the migrations:
   - control DB: `python scripts/migrate.py` (the MCP container also does this on deploy);
   - project B: `python scripts/migrate.py --target search --dsn "$SEARCH_DATABASE_URL"`.
2. Run `routing-refit` **once** after deploying. Parameters fitted before this version have no step priors, so step-level requests answer "run routing-refit once" until then. Whole-task requests keep working throughout.
3. Redeploy the MCP server (step 12), so hosted agents see the new `recommend_models` / `report_model_run` parameters and the per-node flow in the `plan_and_run` prompt.

**Check:** `recommend_models` with a `step_order` returns `"status": "ok"` and a `step` block.

The MCP tools `recommend_models` and `report_model_run` are on both the default (v1) and the full (v2) surface.

**No-retry ladders** (commit `865d0f8`, no migration): `recommend_models` accepts the constraint `"allow_retries": false`. The recommended ladder then:
- never repeats a model;
- never includes a model that was already tried on this task.

Use it when generation is deterministic (temperature 0), where a repeat attempt would return the same answer and only cost money. The default `true` keeps the old behaviour. If no ladder satisfies the constraint, the call returns an error instead of a ladder with repeats.

### 19c. Benchmark import (optional, after 19b)

This turns a public benchmark into Goals that the recommender and `find_ways` can use. Today the only source is **BigCodeBench**. The command is idempotent: re-running it creates nothing new.

What it writes:
- **Goals:** one per task, plus a few domain Goals (from the task's libraries). Each task Goal gets an accepted `SPECIALIZES` edge to its domain Goals. It never overrides an edge a reviewer accepted or rejected.
- **A frozen benchmark per task:** the test code, and which tests are *visible* (about 1/3, used as the runtime check) versus *hidden* (the full suite is the gold grade).
- **Nothing else:** the reference solution is stored only as a hash. Tasks are split into fit and held-out sets with a fixed seed (`--fit-fraction 0.6`, `--seed kel-v1`).

It writes Goals into the knowledge store (control DB or shards), so take a snapshot first and do a dry run:

```bash
set -a; source ~/.stealth-ops/secrets.env; set +a
scripts/ops/stealth-ops snapshot-prod --name pre-benchmark-import
cd backend
DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin benchmark-import --source bigcodebench --download data/ --dry-run
DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin benchmark-import --source bigcodebench --download data/ --limit 20 --manifest bcb-manifest.json --embed
```

- `--download data/` fetches `v0.1.4` (about 2.4 MB) from Hugging Face. Use `--file <path>` for a local copy instead.
- Start with `--limit 20`, then drop the limit.
- `--embed` needs an embedding provider (`GEMINI_API_KEY` or `VOYAGE_API_KEY`).
- The manifest (task → Goal, benchmark, split, visible tests) is what benchmark runners and `routing-import` read.

**Check:**
- the dry run prints the domain counts and the fit/held-out split;
- the real run's report shows `benchmarks_created` / `edges_created` counts;
- re-running the same command reports `benchmarks_existing` instead of new ones;
- the new Goals appear under their domain Goals in `/review/hierarchy`.

A local, isolated demo of the whole loop (import → model attempts → Procedures → recommender → `find_ways` on held-out tasks) is in [experiments/bigcodebench/README.md](../experiments/bigcodebench/README.md). It only touches a local Postgres database, never production.

### 19d. Retrieval changes to know about (commit `865d0f8`, no action needed)

These take effect when the API and MCP are redeployed (step 12). They need no migration and no new variable; they use the judge that is already configured (`JEV_BASE_URL` / `GEMINI_API_KEY`).

- **Ways from more specific Goals:** when `find_ways` settles on a Goal with no usable Procedure of its own, it now also considers Procedures of that Goal's more specific Goals.
  - It looks up to 2 accepted `SPECIALIZES` levels down, at most 20 Goals, most relevant to the request first.
  - Each candidate goes through the normal Procedure tier and is **judged against the request**. Nothing is offered unjudged; with no judge, behaviour is unchanged.
  - An offered Procedure carries `observed_on_more_specific_goal` (the Goal it came from), and the rationale says so.
- **Ways on ambiguous answers:** when the answer is `ambiguous`, it stays `ambiguous`, but each of the top 2 candidate Goals now lists up to 2 judged `ways`, each with `observed_on_goal` when it came from a more specific Goal. A failure here never breaks the answer; it is logged as a warning.
- **Procedures from verified code solutions:** when a run's evidence contains a `code_solution` observation marked `verified`, extraction uses the code-solution extractor (tag `code_solution_v1@1`).
  - It produces method-level steps that cite only APIs present in the code, plus pitfalls.
  - The code itself is never stored.
  - It uses `GENERAL_COMPUTE_JUDGE_MODEL` (default `gemma-4-31B-it`).
  - Until some pipeline records such observations (today only the benchmark demo does), it never fires.

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
5. Call `find_ways` with a broad task that matches several Goals, so the answer is `ambiguous`. The top candidates carry a `ways` list, which may be empty when nothing is judged applicable.
6. If step 19b is done, call `recommend_models` for a Goal with `constraints: {"allow_retries": false}`. The returned ladder has no model twice.

7. Check the semantic judge (identity resolution, goal placement, retrieval):

   ```bash
   cd backend && DATABASE_URL="$CONTROL_DATABASE_URL" python -m app.ingestion.admin judge-health
   ```

   - Every provider (jev, gemini, gemma) must show `identity: ok` and `identity_batch: ok`.
   - Under `general_compute_keys`, every position must be `ok`. **Remove any key that shows HTTP 401 from `GENERAL_COMPUTE_API_KEYS`.** A local check on 2026-09-27 found 6 of 7 configured keys invalid.
   - If the Gemma model shows HTTP 404, set `GENERAL_COMPUTE_FALLBACK_MODEL` (or `GENERAL_COMPUTE_JUDGE_MODEL`) to the exact model id your endpoint serves, for example `gemma-4-31B-it`.

**Check:** all three commands exit 0, `judge-health` exits 0, and the manual checks behave as described.

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

### 24. Known test status (so you don't chase old failures)

```bash
cd backend
python -m pytest -q tests -k offline
```

As of `865d0f8`, **27 offline tests fail**. The same 27 also fail on the commit before it (`4c67865`), so they were already failing before these changes:

| File | Failures |
|---|---|
| `test_stealth_projects_me_offline.py` | 6 |
| `test_bypass_closure_offline.py`, `test_economy_hardening_offline.py` | 3 each |
| `test_local_registry_and_key_store_offline.py`, `test_ongoing_sync_hooks_offline.py` | 2 each |
| `test_auth_enforcement_offline.py`, `test_auth_hardening_offline.py`, `test_auth_redteam_offline.py`, `test_claim_graph_api_offline.py`, `test_enqueue_documents_offline.py`, `test_local_sync_bridge_offline.py`, `test_phase1_security_boundaries_offline.py` (hand-written tenant filter in `goal_abstraction.py`), `test_phase2_authorization_offline.py`, `test_procdoc_v2_pipeline_offline.py` (embedding recipe in `ingestion_jobs.py`), `test_retrieval_identity_offline.py`, `test_skill_ingestion_offline.py` | 1 each |

Some of these depend on test order or on your local environment. For example, `test_auth_enforcement_offline.py` passes when run alone.

**Database (e2e) tests:**
- Run them against a **fresh** local database: `DATABASE_URL=<local test db> python -m pytest -q tests/<file>_e2e.py`.
- `test_goal_resolution_reads_e2e.py` and `test_e2e_ingest_retrieve_execute.py` fail on a database reused across many runs, because rows left over from earlier runs break their counts. They pass on a fresh one.
- The new e2e tests pass: `test_specific_goal_ways_e2e.py`, `test_benchmark_import_e2e.py`, and the offline `test_code_solution_extraction_offline.py` / `test_benchmark_import_offline.py`.

**Known bug, not fixed yet:** when extraction runs without an LLM client, Procedures made by the deterministic extractor are tagged with the LLM extractor's name (`extracted_by`). Keep this in mind when you filter Procedures by extractor.

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
