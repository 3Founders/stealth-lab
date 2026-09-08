-- Migration 40: extraction semantics are part of an artifact compilation identity.
-- The old source/content uniqueness prevented a corrected parser from producing a
-- new immutable Procedure version for unchanged upstream bytes.
ALTER TABLE ingested_artifacts
    DROP CONSTRAINT IF EXISTS ingested_artifacts_source_type_uri_content_hash_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_artifacts_compilation_identity
    ON ingested_artifacts(source_type, uri, content_hash, COALESCE(extractor_version, ''));
