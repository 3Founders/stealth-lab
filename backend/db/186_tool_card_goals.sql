-- 186_tool_card_goals.sql
--
-- Next free number: 185 (zdr provider policies) was highest before this file.
--
-- What this adds: the missing link between the tool catalog and the goal graph. A tool card is an
-- implementation; a Goal is the task it serves. The goal-first router asks "which goal is this?"
-- first, then "which ways/tools achieve that goal", instead of matching the request against
-- 48k tool cards directly.
--
-- One row per (card, goal): how strongly the card's own text matches a Goal. The mapping is
-- produced by scripts/map_tool_cards_to_goals.py (lexical now, embeddings later) and is
-- deliberately rebuildable: drop the rows and run the script again.
--
-- No FK to goals: canonical Goals may live on a knowledge shard (goals.home_shard_id), and a
-- cross-database FK is impossible. The script only maps to Goals that exist on the control
-- database; a missing Goal is simply not a row.
--
-- Access: application only, like 160/162/163 (RLS on, no policies, the Supabase API roles revoked).
-- Idempotent.

CREATE TABLE IF NOT EXISTS tool_card_goals (
    card_id     text NOT NULL REFERENCES tool_cards (id) ON DELETE CASCADE,
    goal_id     uuid NOT NULL,
    score       real NOT NULL DEFAULT 0,
    source      text NOT NULL DEFAULT 'lexical',   -- lexical | embedding | manual
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (card_id, goal_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_card_goals_goal
    ON tool_card_goals (goal_id, score DESC);

ALTER TABLE tool_card_goals ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_card_goals FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_card_goals FROM authenticated;
    END IF;
END $$;
