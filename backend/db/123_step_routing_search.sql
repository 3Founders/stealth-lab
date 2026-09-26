-- target: search
-- Migration 123: project-B twin of 122 (step identity on the recommender logs).
-- Runs only with `scripts/migrate.py --target search`. Next free number: 124. Idempotent.

ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS step_order INTEGER;
ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS step_role  TEXT;
DO $$ BEGIN
    ALTER TABLE routing_observations ADD CONSTRAINT routing_observations_step_role_check
        CHECK (step_role IS NULL OR step_role IN ('plan', 'edit', 'verify', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
ALTER TABLE routing_decisions ADD COLUMN IF NOT EXISTS step_order INTEGER;
