# Runbook: start bulk ingestion

Everything below is a command that exists in this repo. Run from `backend/` unless noted.
**Never point row-writing tests or the benchmark at the hosted database.**

## 0. Preconditions

```bash
python -m app.ingestion.worker --validate-config     # exit 2 + reasons if a provider/DB is missing
```
PRODUCTION (the default when `STEALTHLAB_ENV` is unset) requires a semantic provider
(`JEV_BASE_URL` and/or `GEMINI_API_KEY` and/or `LOCAL_MODEL_NAME`) and an embedding provider.
Set `JEV_CAPABILITIES=applicability,identity` once the JEV service exposes `/judge-identity`;
until then Gemini/Gemma serve identity/retrieval judgments (the chain skips JEV for them).

## 1. Initialize databases

```bash
export CONTROL_DATABASE_URL=postgresql://…        # direct (non-pooled) DSN for DDL
python scripts/migrate.py --status
python scripts/migrate.py                         # applies …, 95_control_plane_shards_projections.sql
```

## 2. Register shards (optional; K000 = the control DB always exists)

```bash
export K001_DATABASE_URL=postgresql://…           # one env var per shard; the registry stores the NAME
python -m app.ingestion.admin register-shard K001 --dsn-env K001_DATABASE_URL --weight 100
python -m app.ingestion.admin shards
python -m app.ingestion.admin shard-status K001 full      # rollover: stop NEW placements, keep reads
```
Note: canonical writes to non-home shards are not enabled yet (`docs/sharding.md`, "Known blocker").

## 3. Enqueue

```bash
# skill packages (public/global):
python -m app.ingestion.enqueue skill-package --source-id anthropic-skills --repo anthropics/skills \
    --commit <sha> --path skills/pdf/SKILL.md
python -m app.ingestion.enqueue skill-package --manifest packages.jsonl      # {"source_id","repo","commit","path"} per line
# already-extracted candidate bundles, explicit scope:
python -m app.ingestion.enqueue raw --job-type ingest_candidate_bundle --idempotency-key src:123 \
    --payload '{"source_key":"src:123","goal":"…","procedure":{"name":"…","steps":[]},"claims":[]}' \
    --scope-type global --visibility public
```
Re-running is a no-op for anything already queued.

## 4. Run workers

```bash
python -m app.ingestion.worker --once                          # local: drain then exit
python -m app.ingestion.worker --loop --concurrency 4          # local service
gcloud run jobs execute stealth-ingest-worker --tasks 8        # Cloud Run (deploy/ingestion/cloudrun/job.yaml)
# GitHub: Actions → "Ingestion worker batch" → Run workflow (workers=4, concurrency=4, max_jobs=500)
deploy/ingestion/oracle/run-worker.sh --loop                   # Oracle VM / any docker host
```

## 5. Monitor

```bash
python -m app.ingestion.admin status               # counts by status, expired leases, projection lag
python -m app.ingestion.admin failures --limit 20  # last_error of permanent failures
python -m app.ingestion.admin verify-projections   # exit 1 on disagreement
python -m app.ingestion.admin verify-dedup         # duplicate goal names / unlinked procedures / dangling merges
```
Healthy: `expired_leases` ≈ 0 outside a crash, `projection_lag.pending` drains to 0,
`verify-*` exit 0. Each worker prints a JSON summary (`done/retryable_failed/failed/lost`).

## 6. Retry and repair

```bash
python -m app.ingestion.admin retry --job-types ingest_candidate_bundle      # permanent/retryable → pending
python -m app.ingestion.admin drain-projections
python -m app.ingestion.admin reindex all                                    # rebuild projections from canonical rows
python -m app.ingestion.admin reindex procedure --shard K001
python -m app.ingestion.admin reconcile-goals            # judge recent unreconciled goals (workers also do this)
python -m app.ingestion.admin reconcile-goals --all      # legacy-corpus sweep (whole corpus; costly; run off-peak)
```

## 7. Verify dedup and retrieval

```bash
python -m app.ingestion.admin verify-dedup
psql "$CONTROL_DATABASE_URL" -c "select decision, count(*) from identity_decisions group by 1"
psql "$CONTROL_DATABASE_URL" -c "select mode, degraded, count(*) from retrieval_decisions group by 1,2"
```

## 8. Tests

```bash
# offline / deterministic (no DB, no network):
python -m pytest tests -q -k offline --ignore=tests/test_document_skills.py
# live database (disposable DB with pgvector; NOT the hosted one):
export TEST_DATABASE_URL=postgresql://postgres:…@localhost:5432/stealth_test
python scripts/migrate.py
python -m pytest tests/test_search_projection_e2e.py tests/test_goal_identity_e2e.py \
       tests/test_retrieval_golden_e2e.py tests/test_distributed_ingestion_e2e.py \
       tests/test_e2e_ingest_retrieve_execute.py -q
# load benchmark (disposable DB):
python scripts/benchmark_projection_scale.py --goals 100000 --procedures 20000 --queries 200
```
A no-Docker throwaway Postgres: `initdb -D pgdata …; pg_ctl -D pgdata -o "-p 54329" start; createdb …; create extension vector`.

## 9. Troubleshooting

* `CONFIG ERROR: no semantic provider configured` — set a provider or use `STEALTHLAB_ENV=STAGING` for a dry run.
* Jobs stuck `retryable_failed` — `last_error`; provider outage → wait/`admin retry`; they back off to 30 min.
* Jobs `failed` with `lease expired after final attempt` — the worker crashed repeatedly; inspect the worker log, then `admin retry`.
* `SemanticJudgmentUnavailable` in `last_error` — JEV/NLI chain is down; jobs retry on their own.
* Pool exhaustion / hung worker — DB pool must be ≥ 2×lanes+4; lower `INGEST_WORKER_CONCURRENCY` or raise `max_connections`.
* `verify-projections` fails — `admin reindex all`; persistent failures show in `projection_outbox` (`status='failed'`, `last_error`).
