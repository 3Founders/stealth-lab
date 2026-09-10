-- Migration 56 (MCP hardening B36): multi-agent coordination via
-- declared file intents on the existing execution graph.
--
-- Next free number: 56 (55 is highest).
--
-- WHY COLUMNS ON THE EXISTING execution_run_nodes, NOT A NEW
-- "coordination" TABLE: migration 36's execution_run_nodes IS the
-- runtime PlanNode row this hardening pass keeps reusing -- B36 asks for
-- "each executable PlanNode MUST be able to declare... before
-- substantial work", which is a property OF that row, not a separate
-- entity. `owner_agent_id`/`file_intent_lease_expires_at` are
-- DELIBERATELY separate from the existing `worker_id`/`lease_expires_at`
-- pair (migration 36) -- those two already have a real, different job
-- (single-driver execution-claim exclusivity, seconds-scale, owned by
-- durable_run.py) that this migration does not touch. A multi-agent
-- file-intent lease is a coordination-scope declaration ("I am working
-- on this node's files"), typically much longer-lived than one
-- node-execution claim, and can exist even before a node has ever been
-- claimed for execution at all.
--
--   owner_agent_id                -- the declaring agent's identity
--                                    (may differ from worker_id, which
--                                    is a low-level process id).
--   read_exact / read_globs       -- JSONB arrays of exact paths / glob
--                                    patterns this node expects to READ.
--   write_exact / write_globs     -- same shape, for WRITES -- the
--                                    conflict-detection-relevant fields.
--   symbols_expected_to_modify    -- JSONB array of symbol names, B36's
--                                    own field, advisory (no symbol-level
--                                    conflict detection is implemented --
--                                    see app/execution/coordination.py's
--                                    own docstring for why).
--   file_intent_lease_expires_at  -- an EXPIRED lease's declaration is
--                                    stale and MUST NOT participate in
--                                    conflict detection (B36: "expired/
--                                    stale lease" is one of the things a
--                                    conflict check must itself detect).
--
-- File declarations are advisory coordination leases, not OS filesystem
-- locks (B36's own words) -- nothing here prevents two processes from
-- actually writing the same file; this is a best-effort coordination
-- signal other agents are expected to check via
-- `declare_file_intent`/`check_file_intent_conflicts` before starting
-- substantial work, exactly mirroring how `report_execution`/evidence
-- already treat self-report as advisory-but-recorded, never enforced.
--
-- Discipline matches mig 36/50/53/55: TEXT + CHECK, DO $$-guarded
-- constraints, additive + idempotent.

ALTER TABLE execution_run_nodes
    ADD COLUMN IF NOT EXISTS owner_agent_id               TEXT,
    ADD COLUMN IF NOT EXISTS read_exact                   JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS read_globs                    JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS write_exact                   JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS write_globs                   JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS symbols_expected_to_modify    JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS file_intent_lease_expires_at  TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_execution_run_nodes_file_intent_lease
    ON execution_run_nodes(file_intent_lease_expires_at)
    WHERE file_intent_lease_expires_at IS NOT NULL;
