-- Worker-ingestion integrity: keep queue payloads queryable/idempotent and
-- make a persisted vector's space auditable.  Imported procedures remain
-- candidate/unverified; this migration changes neither lifecycle state nor
-- evidence.

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS embedding_provider TEXT,
    ADD COLUMN IF NOT EXISTS embedding_input_type TEXT,
    ADD COLUMN IF NOT EXISTS embedding_text_hash TEXT;

ALTER TABLE procedures
    DROP CONSTRAINT IF EXISTS procedures_embedding_input_type_check;

ALTER TABLE procedures
    ADD CONSTRAINT procedures_embedding_input_type_check
    CHECK (embedding_input_type IS NULL OR embedding_input_type IN ('document', 'query'));

CREATE INDEX IF NOT EXISTS idx_procedures_embedding_space
    ON procedures(embedding_model_id, embedding_provider)
    WHERE t_invalid IS NULL AND embedding IS NOT NULL;

-- One row per provider/model makes the Gemini token budget authoritative
-- across laptops, CI runners, and serverless workers sharing this database.
CREATE TABLE IF NOT EXISTS embedding_rate_windows (
    scope              TEXT PRIMARY KEY,
    window_started_at  TIMESTAMPTZ NOT NULL,
    tokens_used        BIGINT NOT NULL DEFAULT 0 CHECK (tokens_used >= 0),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS claimed_by TEXT;

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_worker_status
    ON ingestion_jobs(claimed_by, status, claimed_at)
    WHERE claimed_by IS NOT NULL;

-- asyncpg's JSONB encoder accepts a mapping.  Earlier worker versions passed
-- json.dumps(mapping), creating a JSON scalar string.  Normalize valid legacy
-- object strings in place; the semantic payload is preserved exactly.
UPDATE ingestion_jobs
SET payload = (payload #>> '{}')::jsonb
WHERE jsonb_typeof(payload) = 'string'
  AND left(ltrim(payload #>> '{}'), 1) = '{';

-- A cancelled job is retained as an auditable record of duplicate scheduling;
-- it is not a failed ingestion and resume logic will not retry it.
ALTER TABLE ingestion_jobs
    DROP CONSTRAINT IF EXISTS ingestion_jobs_status_check;

ALTER TABLE ingestion_jobs
    ADD CONSTRAINT ingestion_jobs_status_check
    CHECK (status IN ('pending', 'processing', 'done', 'failed', 'cancelled'));

-- Keep the first pending copy of an immutable package and cancel later
-- duplicates. Processing jobs are left alone: a worker may already have one
-- and must be allowed to finish or fail honestly.
WITH ranked_pending AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY payload->>'source_id', payload->>'commit', payload->>'path'
               ORDER BY id
           ) AS duplicate_rank
    FROM ingestion_jobs
    WHERE job_type = 'ingest_skill_package'
      AND status = 'pending'
      AND payload ? 'source_id'
      AND payload ? 'commit'
      AND payload ? 'path'
)
UPDATE ingestion_jobs AS job
SET status = 'cancelled',
    completed_at = now(),
    last_error = 'cancelled: duplicate immutable skill-package job'
FROM ranked_pending AS ranked
WHERE job.id = ranked.id
  AND ranked.duplicate_rank > 1;

-- Future worker inserts must not create multiple pending claims for the same
-- immutable source package. Historical terminal rows remain auditable.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_jobs_pending_skill_identity
    ON ingestion_jobs (
        job_type,
        (payload->>'source_id'),
        (payload->>'commit'),
        (payload->>'path')
    )
    WHERE job_type = 'ingest_skill_package'
      AND status = 'pending';
