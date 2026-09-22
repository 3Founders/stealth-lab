-- Migration 104: support columns for the knowledge/verification/ranking
-- consolidation pass (parent-attribution fix, duplicate-vs-improvement
-- delta, evidence-trust unification, ranking consolidation).
--
-- This is deliberately the ONLY schema change in that pass. Lineage
-- attribution, evidence trust, and ranking consolidation are all fixed in
-- application code, reusing existing tables exactly as the directive asks
-- ("use the existing stable procedure family/version model... do not
-- create another lineage system" / "prefer reusing... the existing more
-- rigorous capability estimator"):
--   - lineage fix: app/economy/credits.py::reward_verified_reuse now
--     resolves a Procedure's submission by its STABLE procedure_id
--     instead of the exact (supersede-able) row id -- a query change, no
--     new column, no new table.
--   - evidence trust: app/services/evidence_trust.py is the one shared
--     classifier; procedure_graph_api.get_procedure_detail and the
--     ranking estimator both call it. No schema change -- the existing
--     `evidence.evidence_type`/`outcome_status`/`created_by` columns
--     already carry everything the classification needs.
--   - ranking: app/economy/ranking.py now wraps the pre-existing
--     `procedure_graph_api.get_solution_view`/`_capability_estimate`
--     instead of recomputing its own Wilson interval. No schema change.
--
-- The one genuinely new fact that needed somewhere to live:
-- `procedure_submissions.parent_similarity_score` -- the cosine
-- similarity of an 'improvement' submission against its DECLARED PARENT
-- specifically (as opposed to `duplicate_score`, already on this table
-- since migration 102, which is the best match anywhere in the goal's
-- corpus). Nullable: 'new' submissions and improvements where either side
-- has no embedding leave it NULL, never a fabricated 0.
--
-- Idempotent: IF NOT EXISTS throughout, matching every migration since 18/33/35.
--
-- Next free migration number: 105.

ALTER TABLE procedure_submissions
    ADD COLUMN IF NOT EXISTS parent_similarity_score REAL;

CREATE INDEX IF NOT EXISTS idx_procedure_submissions_parent_similarity
    ON procedure_submissions(parent_similarity_score) WHERE parent_similarity_score IS NOT NULL;

-- Next free migration number: 105.
