-- Migration 128: goal_search_index.has_procedures -- only Goals with a live Procedure are offered to agents.
-- Next free migration number: 129.
--
-- WHY (2026-09-29, local ingestion test): extraction mints a Goal for every step's sub-goal. 75 Goals held 9
-- Procedures; the 66 empty ones ("Implement the fix", "Reproduce the issue ...") outranked the real task Goals in
-- goal search and filled the abstraction hierarchy with low-confidence placements. A Goal an agent cannot act on
-- should not be a retrieval candidate, and should not be placed in the hierarchy until it can be.
--
-- The flag is DERIVED, never written by hand: search_projection recomputes it whenever a Goal's projection or any of
-- its Procedures' projections change (EXISTS an 'active' row in procedure_search_index for the Goal). Identity
-- resolution keeps reading every Goal, so a step Goal is still matched and reused, never duplicated.
--
-- Additive and idempotent. Existing rows start false; `python -m app.ingestion.admin reindex goal` recomputes them
-- (fresh-start rule: no in-migration backfill, and procedure_search_index may live on another database).

ALTER TABLE goal_search_index ADD COLUMN IF NOT EXISTS has_procedures BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_goal_search_index_has_procedures
    ON goal_search_index (goal_id) WHERE has_procedures;
