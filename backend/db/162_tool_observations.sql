-- 162_tool_observations.sql
--
-- Next free number: 161 (invoicing) was highest before this file.
--
-- What this adds: which outside tools were suggested and used for a task, and how the task ended -- the tool router's
-- outcome record (docs/tool_routing_plan.md, P3), as routing_observations is the model router's. Claude Code's capture
-- hooks send one record per prompt that needed a tool or used one (POST /tools/outcome); a chat client sends it with
-- the report_tools tool. The ranking then reads, per tool goal, how often a tool that was used ended in a pass
-- (app/tool_routing/store.py tool_outcomes), beside relevance and popularity.
--
-- One row per tool per task: used = it was called; suggested = the tool note or find_ways offered it. ref names the
-- server across users -- its npm/PyPI package or its URL host (never the user's own name for it, never a URL path, which
-- can carry a key). outcome: pass / fail (the task's tests, or the client's own judgment) / unknown. No prompt text.
-- Access: application only, like 154/155/157/159/160 (RLS on, no policies, the Supabase API roles revoked).
--
-- Retention: kept; a row is a few hundred bytes. Idempotent.

CREATE TABLE IF NOT EXISTS tool_observations (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    tool_goal   text,                                   -- app/tool_routing/goals.py id (NULL: no goal named)
    ref         text NOT NULL CHECK (char_length(ref) BETWEEN 1 AND 200),   -- package or host
    tool        text NOT NULL DEFAULT '',               -- the tool called ("list_issues"), when known
    used        boolean NOT NULL,
    suggested   boolean NOT NULL DEFAULT false,
    outcome     text NOT NULL DEFAULT 'unknown' CHECK (outcome IN ('pass', 'fail', 'unknown')),
    check_kind  text NOT NULL DEFAULT 'self_report',    -- tests | judge | self_report
    client      text NOT NULL DEFAULT '',               -- claude-code | claude-web | ...
    goal_id     uuid,                                   -- find_ways' Goal, when there was one
    owner_id    text                                    -- who reported it (the token's subject)
);

CREATE INDEX IF NOT EXISTS idx_tool_obs_goal_ref ON tool_observations (tool_goal, ref) WHERE used;
CREATE INDEX IF NOT EXISTS idx_tool_obs_at ON tool_observations (at);

ALTER TABLE tool_observations ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_observations FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_observations FROM authenticated;
    END IF;
END $$;
