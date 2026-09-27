-- Migration 124: `verified_solution`, a new artifact role for the verified code a Procedure was extracted from.
--
-- docs/knowledge_side_improvements.md, changes 1-2 (redone on the provenance model, no new column): the verified
-- solution is recorded where provenance already lives --
--   * procedures.source_locator     where the solution is: repo + commit + path + lines + content_hash when it is
--                                   committed somewhere durable; otherwise the artifact's own locator
--   * procedures.source_artifacts   a ref {artifact_id, role: 'verified_solution', execution_allowed: false, note}
--   * ingested_artifacts            the artifact row (hash, repo/path/commit); bytes in object storage, or inline in
--                                   content_ref when small and no object store is configured
-- A verified solution is a reference, never executable: execution_allowed stays false (ingested_artifacts_exec_role_chk).
-- Next free number: 125. Idempotent.

ALTER TABLE ingested_artifacts DROP CONSTRAINT IF EXISTS ingested_artifacts_role_chk;
ALTER TABLE ingested_artifacts ADD CONSTRAINT ingested_artifacts_role_chk
    CHECK (role IS NULL OR role IN ('executable_source', 'style_reference', 'design_reference', 'documentation',
                                    'dependency_manifest', 'test_fixture', 'verified_solution'));
