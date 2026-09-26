-- Migration 122: step-level model routing (docs/model_routing_plan.md §10). Next free number: 124
-- (123 is its project-B twin). Additive only.
--
-- A routed attempt may be ONE STEP of a Procedure run (a run.md node) instead of the
-- whole task: step_order names the step of procedure_id, step_role its kind. Steps of
-- one run share the run's instance_key, so their outcomes share the run's difficulty.
-- routing_posteriors gains the 'procedure_steps' kind: one row per Procedure holding
-- the draws of all its observed steps. Idempotent.

ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS step_order INTEGER;
ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS step_role  TEXT;
DO $$ BEGIN
    ALTER TABLE routing_observations ADD CONSTRAINT routing_observations_step_role_check
        CHECK (step_role IS NULL OR step_role IN ('plan', 'edit', 'verify', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
ALTER TABLE routing_decisions ADD COLUMN IF NOT EXISTS step_order INTEGER;

ALTER TABLE routing_posteriors DROP CONSTRAINT IF EXISTS routing_posteriors_entity_kind_check;
ALTER TABLE routing_posteriors ADD CONSTRAINT routing_posteriors_entity_kind_check
    CHECK (entity_kind IN ('goal', 'procedure', 'procedure_steps'));
