-- Migration 82: node-scoping for verification_results (meta-harness Sec 14).
--
-- WHY
--   Confirmed by reading the code directly (not the audit docs, which
--   were stale on this point): node verification today is split across
--   two disjoint mechanisms -- ONE scalar column on execution_run_nodes
--   (`verification_state`, unverified/verified/failed) and a real
--   per-CRITERION table, `verification_results` (migration 55), but that
--   table is scoped to `execution_run_id` ONLY, never to a specific node.
--   So there is no way to answer "which VERIFY rows belong to node N-003"
--   from stored data -- exactly the gap a grep-oriented `.stealth/run.md`
--   (`VERIFY|N-003|V-001|...`) needs closed.
--
-- WHAT THIS ADDS
--   A single nullable column. NULL means "this criterion is run-scoped"
--   (e.g. one derived from `procedures.postconditions`, which names no
--   particular step) -- that stays a real, valid, common case, not an
--   error. A real value means "this criterion belongs to exactly this
--   execution_run_node" -- derived from a step's own `verification`
--   field (`procedure_extraction/schema.py::ProcedureStep.verification`)
--   when a caller supplies the step-order -> node-id mapping.
--
-- HONEST SCOPE: as of this migration, zero real procedures in the
-- corpus have ANY step-level `verification` data (confirmed via direct
-- query -- `with_step_verification: 0` of 4103 real, non-empty-step
-- procedures; 203/4579 procedures DO have real run-level postconditions,
-- unaffected by this change). This column is real, additive schema
-- capability for a real gap, not yet exercised by real data -- the
-- extraction pass that would populate ProcedureStep.verification is a
-- separate, not-yet-built gap, out of this migration's scope.
--
-- Additive, idempotent (this repo's hard rule): ADD COLUMN IF NOT EXISTS,
-- no data changes, no constraint changes to the existing UNIQUE
-- (execution_run_id, criterion_id) -- a node-scoped criterion_id already
-- encodes its step order (e.g. "step:2:verification"), so it stays
-- unique per run on its own; no need to widen the constraint.
--
-- Next free migration number: 83.

ALTER TABLE verification_results
    ADD COLUMN IF NOT EXISTS execution_run_node_id UUID REFERENCES execution_run_nodes(id);

CREATE INDEX IF NOT EXISTS idx_verification_results_node
    ON verification_results (execution_run_node_id)
    WHERE execution_run_node_id IS NOT NULL;
