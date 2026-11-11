-- 200_goal_tool_rankings.sql
--
-- Next free number: 199 (procedure↔card mapping) was highest before this file.
--
-- What this adds (docs/goal_router.md P3-03): the per-Goal tool order, materialized. The rank is a
-- function of slow-moving evidence (tool_trust, lifecycle, policies, listings, task_runs latency) and
-- a stable goal fit; recomputing it on every request was 6 queries per Goal. The router now reads one
-- row per ranked card and applies only the per-request trial overlay.
--
--   * rank is 1-based within a Goal; rank_score is the P3-03 formula; inputs is the JSON the explain
--     output shows (trust_lb, judged, exploration, cost/latency/risk penalties).
--   * The refresh is scripts/refresh_goal_rankings.py (or app/tool_routing/ranking.py). It replaces a
--     Goal's rows in one transaction; a Goal with no rows falls back to the live ranker.
--
-- Access: application only, like 186/199. Idempotent.

CREATE TABLE IF NOT EXISTS goal_tool_rankings (
    goal_id     uuid    NOT NULL,
    card_id     text    NOT NULL REFERENCES tool_cards (id) ON DELETE CASCADE,
    rank        integer NOT NULL CHECK (rank >= 1),
    rank_score  real    NOT NULL DEFAULT 0,
    inputs      jsonb   NOT NULL DEFAULT '{}'::jsonb,
    ranked_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_tool_rankings_goal
    ON goal_tool_rankings (goal_id, rank);

ALTER TABLE goal_tool_rankings ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON goal_tool_rankings FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON goal_tool_rankings FROM authenticated;
    END IF;
END $$;
