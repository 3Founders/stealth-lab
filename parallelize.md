# Parallel skill-ingestion handoff

This is the runbook for finishing Wave 1 on another CPU laptop or on Oracle compute. The ingestion code is already in this checkout; the remaining work is to send the source changes, point workers at the same Postgres/Supabase database, drain the queue, and run the final QA/reporting pass.

## 1. Send the right files

Send or commit the source files below from the same branch/commit. Keep unrelated dirty files in the checkout untouched.

Runtime and source ingestion:

- `config/skill_sources.yaml`
- `backend/app/services/ingestion_sources/base.py`
- `backend/app/services/ingestion_sources/github_corpus.py`
- `backend/app/services/ingestion_sources/manifest.py`
- `backend/app/services/skill_ingestion.py`
- `backend/app/services/ingestion_jobs.py`
- `backend/scripts/ingest_skills.py`
- `backend/scripts/inspect_skill_run.py`
- `backend/scripts/procedure_dedup_sweep.py`

Database and tests:

- `backend/db/39_structured_skill_ingestion.sql`
- `backend/db/40_ingested_artifact_extractor_identity.sql`
- the changed skill-ingestion/migration tests under `backend/tests/`
- `backend/tests/fixtures/skill_retrieval_queries.json`

Reports and audit notes are useful for review but are not needed to run workers:

- `.scratch/skill_ingestion_wave1_audit.md`
- `.scratch/skill_canary_report.md`
- `.scratch/skill_ingestion_wave1_report.md`
- `.scratch/skill_canary_retrieval.json`

Do not send downloaded repository clones, embedding caches, `.pytest_cache`, virtual environments, build output, or any `.env`/credential file. The seven-source manifest is the reproducible input; raw snapshots belong in the existing artifact storage path, not Git.

If the handoff is through Git, review the staged path list before committing rather than using `git add .`:

```powershell
git status --short
git add -- config/skill_sources.yaml `
  backend/app/services/ingestion_sources/base.py `
  backend/app/services/ingestion_sources/github_corpus.py `
  backend/app/services/ingestion_sources/manifest.py `
  backend/app/services/skill_ingestion.py `
  backend/app/services/ingestion_jobs.py `
  backend/scripts/ingest_skills.py `
  backend/scripts/inspect_skill_run.py `
  backend/scripts/procedure_dedup_sweep.py `
  backend/db/39_structured_skill_ingestion.sql `
  backend/db/40_ingested_artifact_extractor_identity.sql `
  backend/tests/fixtures/skill_retrieval_queries.json `
  backend/tests/test_agent_skills_spec_conformance_offline.py `
  backend/tests/test_skill_corpus_wave1_offline.py
git diff --cached --check
git commit -m "ingestion: add structured skill wave one"
git push origin HEAD
```

Also stage the existing modified ingestion test and migration test if `git status` shows those changes belong to this wave. The receiving machine should clone the pushed commit; it should not copy the local `.env` or raw checkout cache.

## 2. Prepare one shared database

All workers must use the same Postgres/Supabase database. Do not give each machine a separate database unless there is a deliberate later merge plan.

1. Clone the checkout at the exact source-code commit.
2. Install the repository's normal backend environment and dependencies.
3. Put the required environment variables in the worker's local environment (or `backend/.env` as supported by the backend): database URL, embedding/API key, and optionally a GitHub token for higher API limits. Copy variable names, never secrets, into messages or Git.
4. Run migrations once from a host with a direct database connection:

   ```powershell
   cd backend
   python scripts/migrate.py
   ```

   Use the direct Postgres URL for migrations. Workers may use the configured pooled URL if that is the repository convention; if the driver reports prepared-statement errors through a transaction pooler, use the repository's pooler-compatible setting (usually statement cache disabled).

The migrations are idempotent. Do not delete production data or mark imported procedures verified.

## 3. Queue work by source

The current database may already contain queued jobs. Check before re-queueing. Re-ingestion is idempotent, so a duplicate queue request is safe but wastes fetch/API time.

For a source that still needs queueing:

```powershell
cd backend
python scripts/ingest_skills.py ingest --source github-awesome-copilot --queue-only
python scripts/ingest_skills.py ingest --source microsoft-skills --queue-only
python scripts/ingest_skills.py ingest --source openai-plugins --queue-only
```

The smaller sources already exercised in this wave are `addy-agent-skills`, `obra-superpowers`, and `openai-agents-python-skills`. The Agent Skills specification is a parser-conformance corpus and has no normal SKILL.md package count; do not treat zero discovered operational skills there as an ingestion failure.

## 4. Run parallel workers

Start one bounded worker process per laptop/Oracle instance, all using the same database:

```powershell
cd backend
python scripts/ingest_skills.py ingest --process-jobs --worker-limit 10
```

For a CPU laptop use roughly 5–10 concurrent jobs. For an Oracle instance start at 10–25, then lower the value if GitHub or embedding API rate limits appear. One process per host is enough; multiple hosts coordinate through row locking (`SKIP LOCKED`). A crashed worker leaves retryable jobs for another worker.

To requeue failed jobs and then drain them:

```powershell
python scripts/ingest_skills.py ingest --resume
python scripts/ingest_skills.py ingest --process-jobs --worker-limit 10
```

Do not run deduplication with `--apply` while workers are writing. Keep imported procedures in candidate/unverified state.

## 5. Monitor the queue

Run these read-only queries against the shared database:

```sql
SELECT status, count(*)
FROM ingestion_jobs
WHERE job_type = 'ingest_skill_package'
GROUP BY status
ORDER BY status;

SELECT id,
       payload->>'source_id' AS source_id,
       payload->>'path' AS skill_path,
       attempts,
       last_error
FROM ingestion_jobs
WHERE job_type = 'ingest_skill_package'
  AND status = 'failed'
ORDER BY id;
```

The queue's persisted states are the states implemented by the current schema (`pending`, `processing`, `done`, and `failed`). Treat `processing` jobs left by a dead process according to the existing retry/claim logic; do not invent a second job table.

The queue is drained when there are no `pending` or `processing` skill-package jobs. Retry failures only after reading their individual error messages. A partial source failure must remain visible in the final report.

## 6. Final QA and reporting

After the queue is drained:

```powershell
cd backend
python scripts/ingest_skills.py retrieval-qa `
  --suite tests/fixtures/skill_retrieval_queries.json `
  --output ../.scratch/skill_wave1_retrieval_qa.json `
  --summary

python scripts/inspect_skill_run.py --help
python scripts/procedure_dedup_sweep.py
```

The dedup sweep is a dry run. Review clusters and provenance before any merge/apply operation; do not apply merges automatically or concurrently with ingestion.

Run the focused regression suite from `backend`:

```powershell
python -m pytest `
  tests/test_agent_skills_spec_conformance_offline.py `
  tests/test_skill_corpus_wave1_offline.py `
  tests/test_skill_ingestion_offline.py -q
```

Update `.scratch/skill_ingestion_wave1_report.md` with actual counts from the shared database: resolved source SHA, discovered/parsed/accepted/rejected packages, implementations, dependencies, unresolved references, duplicates, failed jobs, index state, and retrieval ranks. Record any real execution separately as `Execution` plus verification/evidence. A source-reported claim is not Stealth verification.

## 7. The final sequence

1. Send/commit the listed source, migration, test, and manifest files.
2. Configure each worker with the same database and API credentials locally.
3. Apply migrations once using a direct database connection.
4. Check existing job counts; queue only missing or failed sources.
5. Start bounded workers on the CPU laptop and Oracle instances.
6. Monitor until `pending = 0` and `processing = 0`; inspect and retry failures.
7. Run retrieval QA, focused tests, and the dry-run dedup sweep.
8. Update the final report with measured results and remaining gaps.
9. Preserve raw artifacts through the artifact substrate and keep secrets/caches out of Git.

Completion means reproducible versioned sources, candidate procedures with provenance, preserved implementations/dependencies, normal retrieval success, and an auditable queue/report. It does not mean that every imported procedure is verified.
