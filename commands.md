# Ops commands — anthropics/skills ingestion



## Deploy the worker (rebuilds image from current source, redeploys job.yaml)
```
scripts\ops\stealth-ops.cmd deploy-workers --target cloudrun --skip-gate
```

## Run ingestion (Cloud Run, skip local preflight)
```
scripts\ops\stealth-ops.cmd ingest packages.jsonl --runner cloudrun --workers 1 --skip-preflight
```

## Reset stuck/failed/pending jobs back to pending (only anthropics/skills)
```
psql "$env:CONTROL_DATABASE_URL" -c "UPDATE ingestion_jobs SET status='pending', attempts=0, completed_at=NULL, lease_until=NULL, run_after=NULL WHERE job_type='ingest_skill_package' AND payload->>'repo' = 'https://github.com/anthropics/skills' AND status IN ('failed','retryable_failed','processing','pending');"
```

## Check all 19 job statuses + last_error
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT id, payload->>'path' AS path, status, attempts, last_error, completed_at FROM ingestion_jobs WHERE job_type='ingest_skill_package' ORDER BY id;"
```

## Check why ONE specific job failed (replace <id>)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT id, payload->>'path' AS path, status, attempts, last_error, created_at, completed_at FROM ingestion_jobs WHERE id = <id>;"
```

## Check procedures actually created
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT id, name, goal, created_by, created_at FROM procedures WHERE t_invalid IS NULL ORDER BY created_at DESC;"
```

## Find one procedure (newest first) with its steps + source_artifacts -- bump OFFSET to page through (0=newest, 1=next, 2=next...)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT name, goal, jsonb_pretty(steps) AS steps, source_artifacts FROM procedures WHERE t_invalid IS NULL ORDER BY created_at DESC LIMIT 1 OFFSET 0;"
```

## Check goals ingested (count) -- NOTE: this is a GLOBAL count, not scoped to
## any one repository/ingestion run. goals has no repository FK; use the
## scoped query below to compare apples-to-apples against a procedure count.
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT count(*) FROM goals WHERE t_invalid IS NULL;"
```

## Check goals actually linked to one repository's procedures (scoped, apples-to-apples with the procedure count)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT DISTINCT g.canonical_name FROM goals g JOIN procedures p ON p.achieves_goal_id = g.id JOIN ingested_artifacts ia ON ia.procedure_id = p.procedure_id WHERE ia.repository='anthropics/skills' AND g.t_invalid IS NULL AND p.t_invalid IS NULL;"
```

## Breakdown of ALL goals by how they were created (explains a high global count)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT created_from, count(*) FROM goals WHERE t_invalid IS NULL GROUP BY created_from ORDER BY 2 DESC;"
```

## Check all anthropics/skills procedures with their steps (jsonb inline, each step carries its own source_locator)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT p.name, jsonb_pretty(p.steps) FROM procedures p JOIN ingested_artifacts ia ON ia.procedure_id = p.procedure_id WHERE ia.repository='anthropics/skills' AND p.t_invalid IS NULL ORDER BY p.created_at DESC;"
```

## Check every step individually with its goal text + whether it got a linked goal_id (NULL = dropped, usually the backtick-in-text GoalQualityRejected gap)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT p.name AS procedure, s->>'order' AS step_order, s->>'goal' AS step_goal, s->>'goal_id' AS goal_id FROM procedures p JOIN ingested_artifacts ia ON ia.procedure_id = p.procedure_id CROSS JOIN LATERAL jsonb_array_elements(p.steps) AS s WHERE ia.repository='anthropics/skills' AND p.t_invalid IS NULL ORDER BY p.name, (s->>'order')::int;"
```

## Check goals ingested (list, newest first)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT id, canonical_name, description, created_at FROM goals WHERE t_invalid IS NULL ORDER BY created_at DESC LIMIT 30;"
```

## Check artifacts vs procedures (capture rate)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT count(*) AS artifacts, count(procedure_row_id) AS with_procedures FROM ingested_artifacts WHERE repository='anthropics/skills';"
```

## Check a specific artifact's outcome (admission decision/reason, replace <path>)
```
psql "$env:CONTROL_DATABASE_URL" -c "SELECT path, procedure_row_id, admission_decision, admission_reason FROM ingested_artifacts WHERE repository='anthropics/skills' AND path = '<path>';"
```

## Full wipe of anthropics/skills data (real DELETE, not soft-invalidate) — irreversible
```
psql "$env:CONTROL_DATABASE_URL" -c "BEGIN; DELETE FROM procedures WHERE id IN (SELECT procedure_row_id FROM ingested_artifacts WHERE repository='anthropics/skills' AND procedure_row_id IS NOT NULL); DELETE FROM artifact_blocks WHERE artifact_id IN (SELECT id FROM ingested_artifacts WHERE repository='anthropics/skills'); DELETE FROM ingestion_jobs WHERE job_type='ingest_skill_package' AND source_id='anthropic-skills'; DELETE FROM ingested_artifacts WHERE repository='anthropics/skills'; COMMIT;"
```

## Deploy the Vertex AI OAuth2 tier + all fixes, then re-run everything
```
scripts\ops\stealth-ops.cmd deploy-workers --target cloudrun --skip-gate
```
```
psql "$env:CONTROL_DATABASE_URL" -c "UPDATE ingestion_jobs SET status='pending', attempts=0, completed_at=NULL, lease_until=NULL, run_after=NULL WHERE job_type='ingest_skill_package' AND payload->>'repo' = 'https://github.com/anthropics/skills';"
```
```
scripts\ops\stealth-ops.cmd ingest packages.jsonl --runner cloudrun --workers 1 --skip-preflight
```

## Remove ingested_artifacts rows only (forces re-extraction; existing procedures are untouched and get reused via source_key dedup if content/name match)
```
psql "$env:CONTROL_DATABASE_URL" -c "DELETE FROM ingested_artifacts WHERE repository='anthropics/skills';"
```
