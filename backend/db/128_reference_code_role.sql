-- Migration 128: `reference_code`, an artifact role for the non-trivial code spans the code cascade keeps.
--
-- The code cascade (app/services/code_cascade/) finds the functions and classes of an outstanding repository that carry real
-- logic, has a model confirm and describe them, and stores each as a ONE-STEP PROCEDURE whose step points at the exact lines
-- (source_locator, granularity 'span', commit-pinned) and whose source_artifacts holds the span itself, so a small model that
-- cannot open a URL can still be shown the code.
--
-- Same shape as migration 124 (`verified_solution`), with one deliberate difference: a verified solution with a durable
-- location keeps NO bytes and is opened by the agent; a reference-code span ALWAYS keeps its text (inline when small, object
-- storage otherwise), because the reader is a model that cannot follow a link. It is a REFERENCE: execution_allowed stays
-- false, and ingested_artifacts_exec_role_chk (db/98) already allows only 'executable_source' to be executable.
--
--   ingested_artifacts   role 'reference_code', uri = the commit-pinned blob URL with a line anchor, content_hash = sha256 of
--                        the span text, content_ref = {sha256, size, inline | locator, exemplar: {...}} where `exemplar` carries
--                        the capability statement, why it is non-trivial, license and attribution
--   procedures           source_locator (span), source_artifacts [{artifact_id, role: 'reference_code', execution_allowed: false}]
--
-- Next free number: 129. Idempotent.

ALTER TABLE ingested_artifacts DROP CONSTRAINT IF EXISTS ingested_artifacts_role_chk;
ALTER TABLE ingested_artifacts ADD CONSTRAINT ingested_artifacts_role_chk
    CHECK (role IS NULL OR role IN ('executable_source', 'style_reference', 'design_reference', 'documentation',
                                    'dependency_manifest', 'test_fixture', 'verified_solution', 'reference_code'));
