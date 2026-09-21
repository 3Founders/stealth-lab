-- Migration 101: unique identity for preserved bundled-script artifacts.
--
-- _preserve_script_artifact (app/services/skill_ingestion.py) has always
-- upserted with `ON CONFLICT (source_type, uri, content_hash) DO UPDATE SET
-- last_seen = now()`, but no unique constraint on exactly those three columns
-- ever existed -- only idx_ingested_artifacts_compilation_identity, a
-- DIFFERENT 4-column index (source_type, uri, content_hash,
-- COALESCE(extractor_version, '')) for the main per-document artifact row,
-- which a 3-column arbiter target can't match. Every real ingestion run that
-- reached a document with bundled 'script' resources hit
-- InvalidColumnReferenceError here (first observed 2026-09-21, skill-creator).
--
-- Scoped to role = 'executable_source' (script preservation's own row kind,
-- migration comment in skill_ingestion.py), so this is a genuinely separate
-- identity from the document-level compilation-identity index above and
-- never collides with it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_artifacts_preserved_script_identity
    ON ingested_artifacts(source_type, uri, content_hash)
    WHERE role = 'executable_source';
