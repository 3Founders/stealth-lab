-- target: search
-- Migration 148: project-B twin of 147 (contamination date and import dedupe key on the recommender log).
-- Runs only with `scripts/migrate.py --target search`. Next free number: 149. Idempotent.

ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS item_created_at DATE;
ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_obs_dedupe ON routing_observations (dedupe_key) WHERE dedupe_key IS NOT NULL;
