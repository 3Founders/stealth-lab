-- Migration 54 (fixes migration 53's real bug): PostgreSQL unique
-- indexes treat every NULL as DISTINCT from every other NULL, so
-- migration 53's `idx_procedure_implementation_bindings_identity` (on
-- `procedure_id, procedure_version, implementation_id, role`) never
-- actually enforced uniqueness for the version-agnostic case
-- (`procedure_version IS NULL`) -- confirmed live: two consecutive
-- `link_implementation()` calls for the identical (procedure, NULL
-- version, implementation, role) relation each inserted a NEW row
-- instead of the second reusing the first (`ON CONFLICT DO NOTHING`
-- never fires when Postgres doesn't consider the rows conflicting at
-- all). Same class of bug migration 37 fixed for migration 36's own
-- constraint, via a new file rather than editing the applied one --
-- same discipline here.
--
-- Fix: index on `COALESCE(procedure_version, 0)` instead of the raw
-- column. `0` is a safe sentinel -- migration 53's own
-- `procedure_implementation_bindings_version_chk` CHECK already forbids
-- a real procedure_version below 1, so `0` can never collide with an
-- actual pinned version.
--
-- Next free number: 54 (53 is highest).

DROP INDEX IF EXISTS idx_procedure_implementation_bindings_identity;

CREATE UNIQUE INDEX IF NOT EXISTS idx_procedure_implementation_bindings_identity
    ON procedure_implementation_bindings(
        procedure_id, COALESCE(procedure_version, 0), implementation_id, role
    );
