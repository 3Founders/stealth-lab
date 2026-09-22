-- Migration 105: preserved-artifact identity for any classified role, not
-- just role = 'executable_source'.
--
-- Migration 101 scoped idx_ingested_artifacts_preserved_script_identity to
-- role = 'executable_source' only, matching _preserve_script_artifact's own
-- single-role behavior at the time. Ingestion now also preserves
-- non-executable REFERENCE resources (style_reference, design_reference,
-- documentation, dependency_manifest, test_fixture -- e.g. a bundled .ts
-- file that exemplifies a distinctive visual style, preserved so it can be
-- retrieved later, never executed). Both cases upsert on the exact same
-- (source_type, uri, content_hash) conflict target, so this widens the
-- existing index's predicate rather than adding a redundant parallel one.
--
-- Idempotent: drop-if-exists + create-if-not-exists, safe to re-run.
DROP INDEX IF EXISTS idx_ingested_artifacts_preserved_script_identity;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_artifacts_preserved_role_identity
    ON ingested_artifacts(source_type, uri, content_hash)
    WHERE role IS NOT NULL;
