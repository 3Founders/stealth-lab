-- Final-agent-experiment readiness gate: corpus/retrieval contamination fix.
--
-- Root cause (backend/tests/test_retrieval_fixture_isolation_e2e.py,
-- .scratch/final_agent_experiment/retrieval-contamination-review.md): the
-- production retrieval path (applicability.py::_fetch_candidate_pool, via
-- _CANDIDATE_BASE_WHERE) has never had any predicate distinguishing
-- engineering/test/demo-created procedures from real knowledge. Neither
-- `provenance` nor `scope_type` can be used for this: e2e-fixture rows and
-- the 3 genuinely Better-Ways-admitted procedures share the identical
-- provenance='prior_library', scope_type='global' (confirmed by direct
-- query). `created_by` free-text tags are the closest existing signal but
-- are inconsistent (~20 different e2e/test-actor strings, several without
-- an "e2e" substring, and 'procedure_capture' used ambiguously by both real
-- and fixture paths) -- not queryable as a reliable production predicate.
--
-- Fix: one new explicit boolean, fail-closed (DEFAULT true = "presumed
-- engineering/test data unless a caller explicitly says otherwise"). This
-- is the safer default direction: a real capture_procedure() call site that
-- is not updated to pass is_engineering_fixture=False is conservatively
-- EXCLUDED from default retrieval (an availability gap, safe) rather than
-- the current failure mode of unmarked test fixtures being INCLUDED
-- (a correctness/trust gap, unsafe). Existing rows all default to `true`
-- on this ALTER; 40_procedures_engineering_fixture_backfill.sql then
-- explicitly (not implicitly) flips the known-real rows to `false`.
--
-- Additive, idempotent, no data rewrite beyond the new column's own
-- default fill (a single boolean per row, not a rewrite of any existing
-- column).
ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS is_engineering_fixture BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN procedures.is_engineering_fixture IS
    'true = presumed engineering/test/demo scaffolding, excluded from '
    'ordinary user-facing retrieval (applicability.py::_CANDIDATE_BASE_WHERE). '
    'false = real knowledge, explicitly marked so by the capture path. '
    'Fail-closed default: unmarked rows are conservatively excluded, never '
    'silently included. Orthogonal to verification_state (candidate vs '
    'verified is a trust-tier question; this is an eligibility question) '
    'and to scope_type/provenance (visibility/tenancy vs source lineage, '
    'neither of which reliably distinguishes fixtures from real knowledge '
    '-- see this migration''s own header comment for why).';
