-- Migration 108: rename claimed_projects -> synced_projects (terminology
-- correction).
--
-- "claim" already has an established, different meaning elsewhere in
-- keळ (claims.md, verification claims, the claim graph / get_claim_graph
-- / GET /v1/claims/graph). Migration 107 named this table
-- `claimed_projects` before that collision was caught; this migration
-- corrects it to `synced_projects` (the local-project-account-sync
-- feature). 107 itself is left untouched -- migrations are immutable
-- once applied (scripts/migrate.py's checksum ledger refuses to silently
-- re-run an edited file) -- this is the follow-up rename migration, same
-- convention any other post-hoc rename in this repo would use.
--
-- No data changes, no behavior changes: owner_subject, the snapshot_*
-- columns, and every index's semantics are unchanged -- only the table
-- name and the one column whose name itself said "claim" (claimed_at)
-- are renamed. project_id stays the PRIMARY KEY (the "not claimable/
-- syncable by a second account" invariant is unaffected by the rename).
--
-- Idempotent: IF EXISTS / IF NOT EXISTS guards throughout, safe to re-run.

ALTER TABLE IF EXISTS claimed_projects RENAME TO synced_projects;
ALTER TABLE IF EXISTS synced_projects RENAME COLUMN claimed_at TO synced_at;

ALTER INDEX IF EXISTS idx_claimed_projects_owner RENAME TO idx_synced_projects_owner;
