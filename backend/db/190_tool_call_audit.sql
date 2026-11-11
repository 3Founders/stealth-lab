-- 190_tool_call_audit.sql
--
-- Next free number: 189 (intent certificates) was highest before this file.
--
-- What this adds: the append-only, tamper-evident receipt for every tool-call decision
-- (docs/goal_router.md, P1-07). One row per authorize()/call, whether it was allowed, denied or
-- sent for approval. Arguments and results are never stored, only their hashes.
--
-- Chain: `prev_hash` is the previous row's `hash`; the first row uses 64 zeros. `hash` is
-- SHA-256 over canonical JSON of the receipt fields. `signature` is HMAC-SHA256 over the same
-- bytes (app/tool_routing/signing.py). scripts/verify_audit_chain.py walks the chain.
--
-- Access: application only, like 160/162/163/186. Idempotent. Kept for audit.

CREATE TABLE IF NOT EXISTS tool_call_audit (
    id            bigserial PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    receipt_id    text NOT NULL UNIQUE,               -- rc_...
    owner_id      text,
    agent         text NOT NULL DEFAULT 'kell',
    task_id       text,
    goal_id       uuid,
    server        text NOT NULL DEFAULT '',
    tool          text NOT NULL DEFAULT '',
    args_hash     text NOT NULL DEFAULT '',
    decision      text NOT NULL
                  CHECK (decision IN ('allow', 'deny', 'approve')),
    reason        text NOT NULL DEFAULT '',
    risk          text NOT NULL DEFAULT 'R1',
    data_class    text NOT NULL DEFAULT 'PUBLIC_SOURCE',
    cert_id       text,
    approval_id   text,
    latency_ms    integer,
    cost_usd      numeric(12, 6),
    result_status text NOT NULL DEFAULT '',
    result_hash   text,
    prev_hash     text NOT NULL,
    hash          text NOT NULL,
    signature     text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_call_audit_at ON tool_call_audit (at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_audit_task ON tool_call_audit (task_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_audit_server ON tool_call_audit (server, at DESC);

ALTER TABLE tool_call_audit ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_call_audit FROM anon;
        REVOKE ALL ON SEQUENCE tool_call_audit_id_seq FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_call_audit FROM authenticated;
        REVOKE ALL ON SEQUENCE tool_call_audit_id_seq FROM authenticated;
    END IF;
END $$;
