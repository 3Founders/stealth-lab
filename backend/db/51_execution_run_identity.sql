-- Migration 51 (MCP hardening B3/B4/B32): ProcedureRun identity fields.
--
-- Next free number: 51 (50 is highest).
--
-- WHY COLUMNS ON THE EXISTING execution_runs, NOT A NEW ProcedureRun
-- TABLE: migration 36's execution_runs IS the durable, resumable run
-- unit this codebase already has -- CLAUDE.md rule 2 forbids a second
-- execution/run substrate, and B3 itself says so explicitly ("Once a
-- Procedure is accepted, create or return a durable ProcedureRun -- or
-- use the exact existing equivalent"). This migration only adds the
-- identity fields B3 requires that execution_runs does not yet carry:
--
--   request_id                  -- idempotency key for "create or return"
--   workspace_id                 -- distinct from scope_type/scope_entity_id
--                                    (which scope KNOWLEDGE visibility, not
--                                    the hosted workspace_registry concept)
--   trace_id                     -- execution_runs had NONE at all before
--                                    this (a confirmed real gap: the
--                                    durable-run layer and the OTel-style
--                                    trace-ingestion layer were unlinked)
--   parent_run_id / parent_node_id -- self-referential run nesting. Adding
--                                    the COLUMNS now is schema groundwork
--                                    only; the actual child-run lifecycle
--                                    (WAITING_CHILD, resume-on-child-
--                                    terminal, cycle protection) is a
--                                    separate, later gate (B9-B13) and is
--                                    NOT implemented by this migration.
--   claim_working_set_revision  -- the exact `as_of` instant used for any
--                                    claim-graph read tied to this run, so
--                                    a later replay/audit can pin what
--                                    knowledge state a decision saw. A
--                                    timestamp, not a fabricated version
--                                    integer -- there is no whole-graph
--                                    version scheme to report honestly.
--   route_decision_id           -- links back to the route_decisions row
--                                    (migration 50) that selected this
--                                    Procedure, closing the link that
--                                    migration 50's own comment named as
--                                    "belongs to B3's gate, not invented
--                                    ahead of it".
--
-- `verification_plan_id` (B3's field list) is DELIBERATELY NOT ADDED
-- here: no VerificationPlan object exists anywhere in this codebase yet
-- (confirmed by audit -- the verification ladder is a separate, later
-- gate, B34). Adding a column that references nothing real would be
-- exactly the "field nobody populates" placeholder CLAUDE.md rule 1
-- forbids; it will be added when B34 gives it a real referent.
--
-- `implementation_bindings` (B3's field list) is likewise DELIBERATELY
-- NOT a stored column: execution_run_nodes.implementation_id/
-- implementation_version (migration 36) is ALREADY the per-node source
-- of truth for implementation binding. A second, run-level JSONB copy
-- would only be able to drift from it -- the run-context assembly code
-- (continue_run) computes this view by joining execution_run_nodes
-- instead.
--
-- Discipline matches mig 33/35/36/50: TEXT + CHECK, DO $$-guarded
-- constraints, additive + idempotent.

ALTER TABLE execution_runs
    ADD COLUMN IF NOT EXISTS request_id                  TEXT,
    ADD COLUMN IF NOT EXISTS workspace_id                 TEXT,
    ADD COLUMN IF NOT EXISTS trace_id                     TEXT,
    ADD COLUMN IF NOT EXISTS parent_run_id                UUID REFERENCES execution_runs(id),
    ADD COLUMN IF NOT EXISTS parent_node_id               UUID REFERENCES execution_run_nodes(id),
    ADD COLUMN IF NOT EXISTS claim_working_set_revision   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS route_decision_id            UUID REFERENCES route_decisions(id);

-- Idempotent "create or return": a caller-supplied request_id must
-- identify at most one run. Partial (WHERE request_id IS NOT NULL) so
-- callers that never supply one (every existing caller, today) are
-- completely unaffected -- NULL <> NULL, so any number of NULLs coexist.
CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_runs_request_id
    ON execution_runs(request_id) WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_execution_runs_parent_run_id
    ON execution_runs(parent_run_id) WHERE parent_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_runs_route_decision_id
    ON execution_runs(route_decision_id) WHERE route_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_runs_workspace_id
    ON execution_runs(workspace_id) WHERE workspace_id IS NOT NULL;
