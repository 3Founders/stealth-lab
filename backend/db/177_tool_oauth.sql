-- Tool gateway, phase 2: kell as an OAuth 2.1 client of a remote MCP server that asks for a sign-in
-- (docs/tool_gateway_design.md, "Later phases"). One row per person and server; the tokens are sealed with
-- app/services/secret_box.py under AAD (owner_id, server), so a ciphertext moved to another row fails to open.
--
-- tool_oauth_clients caches the dynamic client registration (RFC 7591): one client_id per server, reused by every person.
-- tool_oauth_pending is the short-lived authorization-code flow (state hash + PKCE verifier), one use, ten minutes.
-- tool_oauth_tokens is what a later call reads; refresh_token may be absent when the server does not issue one.

CREATE TABLE IF NOT EXISTS tool_oauth_clients (
    server                 TEXT PRIMARY KEY,
    client_id              TEXT NOT NULL,
    authorization_endpoint TEXT NOT NULL,
    token_endpoint         TEXT NOT NULL,
    registration_endpoint  TEXT,
    scope                  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_oauth_pending (
    state_hash    TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    server        TEXT NOT NULL,
    client_id     TEXT NOT NULL,
    verifier      TEXT NOT NULL,
    redirect_uri  TEXT NOT NULL,
    token_endpoint TEXT NOT NULL,
    resource      TEXT,
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tool_oauth_pending_expiry ON tool_oauth_pending (expires_at);

CREATE TABLE IF NOT EXISTS tool_oauth_tokens (
    owner_id       TEXT NOT NULL,
    server         TEXT NOT NULL,
    access_token   TEXT NOT NULL,        -- secret_box ciphertext
    refresh_token  TEXT,                 -- secret_box ciphertext, when the server issued one
    token_endpoint TEXT NOT NULL,
    client_id      TEXT NOT NULL,
    scope          TEXT,
    expires_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, server)
);
