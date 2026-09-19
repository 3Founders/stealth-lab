-- Migration 93: semantic-judge requeue support + derived context-compaction views.
--
-- 1. ingestion_jobs.run_after: lets a requeued PENDING_SEMANTIC_JUDGMENT /
--    compaction-retry job wait out a backoff before claim_jobs() picks it up.
--    NULL = runnable immediately (every existing job). Reuses the existing
--    queue -- no second scheduler.
-- 2. context_compaction_views: the DERIVED compacted working-context view for
--    a session. Raw trajectory (trace_events / collector files) is NEVER
--    modified; a view can be discarded and recomputed with a better model.
-- 3. context_retention_cache: per-item retention judgments, keyed by content
--    hash + goal/node/state versions + provider/model + prompt version.
--
-- Additive + idempotent.

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS run_after TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_pending_run_after
    ON ingestion_jobs (run_after)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS context_compaction_views (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       TEXT NOT NULL,
    execution_run_id UUID,
    input_hash       TEXT NOT NULL,   -- hash of the raw items this view was derived from
    state_hash       TEXT NOT NULL,   -- hash of the durable-state snapshot used
    provider         TEXT,
    model            TEXT,
    prompt_version   TEXT,
    status           TEXT NOT NULL CHECK (status IN ('compacted', 'skipped_unavailable')),
    view             JSONB NOT NULL,
    stats            JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_context_compaction_views_session
    ON context_compaction_views (session_id, created_at DESC)
    WHERE status = 'compacted';

CREATE TABLE IF NOT EXISTS context_retention_cache (
    cache_key  TEXT PRIMARY KEY,
    decision   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
