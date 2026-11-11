-- 188_tool_policies.sql
--
-- Next free number: 187 (security subgoals) was highest before this file.
--
-- What this adds: the deterministic policy store in front of every tool call
-- (docs/goal_router.md, P1-05). A row says, for a server/tool/goal, what risk it carries,
-- whether it is allowed, denied or needs approval, which data classes may reach it, and the
-- caps the gate must enforce.
--
-- Matching is exact: server and tool are the registry names (`ai.exa/exa`, `web_search_exa`);
-- NULL means "any". `goal_id` is a uuid with no FK (Goals live on knowledge shards).
-- Evaluation is in app/tool_routing/policy.py: explicit deny wins, then the most specific row,
-- then data class, then the risk ceiling, then the effect. No row means deny.
--
-- Seeds: Exa is public read (R0 allow); Hinoki is offensive security (R3 approve) and only
-- PUBLIC_SOURCE. P1-09 must register its card with server = 'hinoki' so this row applies.
--
-- Access: application only, like 160/162/163/186. Idempotent.

CREATE TABLE IF NOT EXISTS tool_policies (
    id          bigserial PRIMARY KEY,
    server      text,                                   -- registry name, NULL = any server
    tool        text,                                   -- MCP tool name, NULL = any tool
    goal_id     uuid,                                   -- Goal scope, NULL = any goal
    risk        text NOT NULL DEFAULT 'R1'
                CHECK (risk IN ('R0', 'R1', 'R2', 'R3', 'R4')),
    effect      text NOT NULL DEFAULT 'deny'
                CHECK (effect IN ('allow', 'deny', 'approve')),
    data_classes text[] NOT NULL DEFAULT ARRAY['PUBLIC_SOURCE'],
    max_calls   integer CHECK (max_calls IS NULL OR max_calls > 0),
    egress      text[] NOT NULL DEFAULT '{}',
    timeout_s   integer CHECK (timeout_s IS NULL OR timeout_s > 0),
    priority    integer NOT NULL DEFAULT 0,
    enabled     boolean NOT NULL DEFAULT true,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tool_policies_server ON tool_policies (server, tool) WHERE enabled;
CREATE INDEX IF NOT EXISTS idx_tool_policies_goal ON tool_policies (goal_id) WHERE enabled;

ALTER TABLE tool_policies ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_policies FROM anon;
        REVOKE ALL ON SEQUENCE tool_policies_id_seq FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_policies FROM authenticated;
        REVOKE ALL ON SEQUENCE tool_policies_id_seq FROM authenticated;
    END IF;
END $$;

INSERT INTO tool_policies (server, tool, risk, effect, data_classes, max_calls, egress, note)
SELECT 'ai.exa/exa', NULL, 'R0', 'allow', ARRAY['PUBLIC_SOURCE'], 60,
       ARRAY['api.exa.ai', 'mcp.exa.ai'], 'Exa public web read (P1-08)'
WHERE NOT EXISTS (
    SELECT 1 FROM tool_policies WHERE server = 'ai.exa/exa' AND tool IS NULL
);

INSERT INTO tool_policies (server, tool, risk, effect, data_classes, max_calls, egress, note)
SELECT 'hinoki', NULL, 'R3', 'approve', ARRAY['PUBLIC_SOURCE'], 20,
       ARRAY['api.hinoki.security'], 'Hinoki defensive security surface (P1-09)'
WHERE NOT EXISTS (
    SELECT 1 FROM tool_policies WHERE server = 'hinoki' AND tool IS NULL
);
