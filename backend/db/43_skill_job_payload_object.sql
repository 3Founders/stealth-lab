-- A generic ingestion job may use any JSONB payload shape, but a structured
-- skill-package job has an immutable object identity.  Reject the legacy
-- double-encoded JSON scalar at the database boundary so an outdated worker
-- fails safely rather than recreating an unbounded duplicate queue.
ALTER TABLE ingestion_jobs
    DROP CONSTRAINT IF EXISTS ingestion_jobs_skill_payload_object_check;

ALTER TABLE ingestion_jobs
    ADD CONSTRAINT ingestion_jobs_skill_payload_object_check
    CHECK (
        job_type <> 'ingest_skill_package'
        OR jsonb_typeof(payload) = 'object'
    );
