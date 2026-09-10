-- Migration 52 (MCP hardening B9-B13): recursive child ProcedureRun
-- support -- cycle/budget accounting only, no new scheduler.
--
-- Next free number: 52 (51 is highest).
--
-- WHY ONE NEW COLUMN, NOT A NEW TABLE: migration 51 already added
-- parent_run_id/parent_node_id -- the real parent/child EXECUTION
-- relationship (B15: "Execution A -> child Execution B", never a
-- canonical Procedure dependency). This migration adds exactly the one
-- thing that relationship doesn't give you cheaply: "how many runs
-- share my root ancestor, and what is it" -- needed to enforce
-- B12's max_child_executions budget without a recursive CTE walking
-- every run in the table on every check.
--
--   root_run_id -- self-referential: a root run (parent_run_id IS NULL)
--                   points at itself; a child run copies its parent's
--                   root_run_id. Set once, at creation, in
--                   durable_run.start_run() -- never updated afterward
--                   (a run's place in its own chain's root is fixed at
--                   birth, exactly like parent_run_id already is).
--                   This is a materialized-path denormalization of the
--                   SAME parent_run_id chain migration 51 already
--                   defines, not a second source of truth -- it can
--                   always be recomputed from parent_run_id alone via a
--                   recursive CTE if it were ever wrong, and a test in
--                   this gate asserts exactly that (recompute == stored).
--
-- Recursion DEPTH is deliberately NOT stored as a column -- it is a
-- property of the parent_run_id chain's length at query time (a
-- recursive CTE, cheap at the depth bounds this gate enforces), so
-- there is nothing here that could drift from the chain itself.
--
-- "WAITING_CHILD" (B10) is likewise deliberately NOT a new
-- execution_runs.status value -- a parent run's real status stays
-- 'running' while its current node awaits a child (the node's own lease
-- is what makes crash-recovery work here, unchanged from migration 36);
-- "is this run currently waiting on an active child" is a derived fact
-- (does a non-terminal execution_runs row exist with
-- parent_run_id=this AND parent_node_id=<the node in question>?),
-- computed in `continue_run`/`get_run_context`, not persisted
-- separately -- widening the CHECK constraint for a value nothing new
-- needs to store would be exactly the "second copy of the same fact"
-- CLAUDE.md rule 2 warns against.

ALTER TABLE execution_runs
    ADD COLUMN IF NOT EXISTS root_run_id UUID REFERENCES execution_runs(id);

CREATE INDEX IF NOT EXISTS idx_execution_runs_root_run_id
    ON execution_runs(root_run_id) WHERE root_run_id IS NOT NULL;

-- Backfill: every existing row is its own root (none of them have a
-- parent_run_id yet -- migration 51 added that column but nothing has
-- used it in production before this gate). Idempotent (WHERE
-- root_run_id IS NULL guards a second run of this file).
UPDATE execution_runs SET root_run_id = id
WHERE root_run_id IS NULL AND parent_run_id IS NULL;
