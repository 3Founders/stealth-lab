-- 155_delegate_relay.sql
--
-- Next free number: 154 was highest before this file.
--
-- What this adds: the Delegator relay (.scratch/prompts/delegator_pull_brief.md). A signed-in person registers the agents they
-- run (a Claude chat with the kell connector, Claude Code, opencode, ...), sends a task to one from the website, and that agent
-- PULLS it (the MCP tool delegate_inbox) and posts the outcome back (delegate_result). The relay never calls into anyone's
-- machine and holds no model credentials of any kind.
--
-- Scope: this is the person's own work queue, not knowledge-graph data. Every row carries the owner's Supabase subject
-- (owner_sub) and the application checks it on every read and write. Like contact_requests (154) the tables are closed to every
-- role but the backend's own: RLS is ON with NO policies, and the Supabase API roles are revoked, so the public anon key can
-- neither read nor write a row.
--
-- Retention: delete_after is 7 days from creation, a default the founders can change (brief, open question 1). The relay deletes
-- expired rows opportunistically when tasks are listed or claimed; there is no scheduled job yet.
-- Idempotent.

CREATE TABLE IF NOT EXISTS delegate_agents (
    id          uuid PRIMARY KEY,
    owner_sub   text NOT NULL CHECK (char_length(owner_sub) BETWEEN 1 AND 128),
    name        text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_.-]{0,39}$'),
    kind        text NOT NULL DEFAULT 'agent' CHECK (char_length(kind) BETWEEN 1 AND 40),
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz,
    revoked_at  timestamptz
);

-- one live agent per (owner, name); a revoked name can be reused
CREATE UNIQUE INDEX IF NOT EXISTS delegate_agents_owner_name_live
    ON delegate_agents (owner_sub, name) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS delegate_tasks (
    id            uuid PRIMARY KEY,
    owner_sub     text NOT NULL CHECK (char_length(owner_sub) BETWEEN 1 AND 128),
    agent_id      uuid NOT NULL REFERENCES delegate_agents(id) ON DELETE CASCADE,
    room          text NOT NULL DEFAULT '#general' CHECK (char_length(room) BETWEEN 1 AND 80),
    from_handle   text NOT NULL DEFAULT '@me' CHECK (char_length(from_handle) BETWEEN 1 AND 80),
    body          text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 8000),
    locators      jsonb NOT NULL DEFAULT '[]'::jsonb,
    depth         integer NOT NULL DEFAULT 0 CHECK (depth BETWEEN 0 AND 4),
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('needs_approval', 'queued', 'claimed', 'done', 'failed', 'cancelled')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz,
    finished_at   timestamptz,
    result_text   text CHECK (result_text IS NULL OR char_length(result_text) <= 20000),
    result_check  text CHECK (result_check IS NULL OR char_length(result_check) <= 2000),
    delete_after  timestamptz NOT NULL DEFAULT (now() + interval '7 days')
);

CREATE INDEX IF NOT EXISTS delegate_tasks_inbox_idx ON delegate_tasks (agent_id, created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS delegate_tasks_room_idx ON delegate_tasks (owner_sub, room, created_at DESC);
CREATE INDEX IF NOT EXISTS delegate_tasks_expiry_idx ON delegate_tasks (delete_after);

ALTER TABLE delegate_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE delegate_tasks ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON delegate_agents FROM anon';
        EXECUTE 'REVOKE ALL ON delegate_tasks FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON delegate_agents FROM authenticated';
        EXECUTE 'REVOKE ALL ON delegate_tasks FROM authenticated';
    END IF;
END
$$;
