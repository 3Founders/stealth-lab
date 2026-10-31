-- 163_tool_gateway.sql
--
-- Next free number: 162 (tool observations) was highest before this file.
--
-- What this adds: the tool gateway's state (docs/tool_gateway_design.md, phase 1). kell's server calls an allowlisted
-- third-party MCP server for a user who has not installed it (use_tool(server=..., name=...)), once that user has
-- allowed it.
--
--   * tool_grants: a user's permission for kell to call one server for them (revocable).
--   * tool_consent_links: one-time links (15 minutes) that the user opens to grant it; only the SHA-256 of the token is
--     kept, so the table cannot be used to grant anything.
--   * tool_gateway_calls: the audit log -- who called which server's tool, when, how it went, sizes and latency.
--     Never the arguments or the result.
--
-- Access: application only, like 154/155/157/159/160/162 (RLS on, no policies, the Supabase API roles revoked).
-- Retention: grants until revoked; links are removed by the server once used or expired; calls are kept for audit.
-- Idempotent.

CREATE TABLE IF NOT EXISTS tool_grants (
    owner_id    text NOT NULL,
    server      text NOT NULL,                          -- the MCP Registry name ("app.glif/glif")
    granted_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz,
    PRIMARY KEY (owner_id, server)
);

CREATE TABLE IF NOT EXISTS tool_consent_links (
    token_hash  text PRIMARY KEY,                       -- sha256 of the link's token
    owner_id    text NOT NULL,
    server      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);

CREATE TABLE IF NOT EXISTS tool_gateway_calls (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    owner_id    text,
    server      text NOT NULL,
    tool        text NOT NULL,
    status      text NOT NULL,                          -- ok | refused:<why> | failed:<why>
    latency_ms  integer,
    bytes_in    integer,
    bytes_out   integer
);
CREATE INDEX IF NOT EXISTS idx_tool_gateway_calls_owner ON tool_gateway_calls (owner_id, at DESC);

ALTER TABLE tool_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_consent_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_gateway_calls ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_grants FROM anon;
        REVOKE ALL ON tool_consent_links FROM anon;
        REVOKE ALL ON tool_gateway_calls FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_grants FROM authenticated;
        REVOKE ALL ON tool_consent_links FROM authenticated;
        REVOKE ALL ON tool_gateway_calls FROM authenticated;
    END IF;
END $$;
