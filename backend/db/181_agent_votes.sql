-- 181_agent_votes.sql
--
-- What this adds: the thumbs up / down on an agent's answer in a room, and which outside tools that answer used, so a vote
-- moves those tools in the ranking (app/tool_routing/store.py tool_outcomes; a person's vote counts three times an automatic
-- judgment of one call). One agent_turns row per answer the room agent posted (event_id = the room event); one agent_votes
-- row per person per answer (changing a vote replaces it, taking it back deletes it). No message text is stored.
-- Access: application only (RLS on, no policies, the Supabase API roles revoked), like 162/180. Idempotent.

CREATE TABLE IF NOT EXISTS agent_turns (
    event_id    TEXT PRIMARY KEY,
    room_id     TEXT NOT NULL,
    owner_id    TEXT NOT NULL,                 -- who asked
    refs        TEXT[] NOT NULL DEFAULT '{}',  -- the tools used, as tool_observations names them (host or package)
    tool_goal   TEXT,                          -- app/tool_routing/goals.py id (NULL: none)
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_votes (
    event_id    TEXT NOT NULL REFERENCES agent_turns (event_id) ON DELETE CASCADE,
    voter       TEXT NOT NULL,
    vote        SMALLINT NOT NULL CHECK (vote IN (-1, 1)),
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, voter)
);

CREATE INDEX IF NOT EXISTS idx_agent_turns_goal ON agent_turns (tool_goal);

ALTER TABLE agent_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_votes ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON agent_turns FROM anon;
        REVOKE ALL ON agent_votes FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON agent_turns FROM authenticated;
        REVOKE ALL ON agent_votes FROM authenticated;
    END IF;
END $$;
