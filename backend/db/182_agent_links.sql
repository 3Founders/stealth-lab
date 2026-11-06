-- 182_agent_links.sql
--
-- Next free number: 181 (agent_votes) was highest before this file.
--
-- What this adds: a person can let another person call their agents from anywhere (@anuj/kell), not only from a room they share.
-- One row is one direction: owner_sub lets peer_sub call owner_sub's agents. For two people to call each other, each grants the other.
-- auto_approve (default false) skips the owner's approve button for that peer's tasks; without it a task from another person still waits
-- for the agent's owner, because it runs in their agent with their tools. Same closed-table rule as 155: RLS on, no policies.
-- Idempotent.

CREATE TABLE IF NOT EXISTS agent_links (
    owner_sub    text NOT NULL CHECK (char_length(owner_sub) BETWEEN 1 AND 128),
    peer_sub     text NOT NULL CHECK (char_length(peer_sub) BETWEEN 1 AND 128),
    auto_approve boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    revoked_at   timestamptz,
    PRIMARY KEY (owner_sub, peer_sub),
    CHECK (owner_sub <> peer_sub)
);
CREATE INDEX IF NOT EXISTS agent_links_peer_idx ON agent_links (peer_sub) WHERE revoked_at IS NULL;
ALTER TABLE agent_links ENABLE ROW LEVEL SECURITY;
