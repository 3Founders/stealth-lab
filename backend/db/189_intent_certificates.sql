-- 189_intent_certificates.sql
--
-- Next free number: 188 (tool policies) was highest before this file.
--
-- What this adds: task-scoped authorization for tool calls (docs/goal_router.md, P1-06). A
-- certificate names the task, owner, agent, Goal, allowed servers/tools, argument constraints,
-- data class, risk ceiling, egress, call cap and expiry. It can only narrow the static policy in
-- tool_policies; it never widens it. The model cannot mint one: only certificates.issue() from a
-- trusted caller can.
--
-- The signed claims are kept whole in `claims` (jsonb), so verification never has to rebuild a
-- timestamp from columns. The extracted columns exist for lookup and revocation only.
-- Signature: HMAC-SHA256 over canonical JSON (app/tool_routing/signing.py).
--
-- Access: application only, like 160/162/163/186. Idempotent.

CREATE TABLE IF NOT EXISTS intent_certificates (
    id            text PRIMARY KEY,                    -- ic_...
    task_id       text NOT NULL,
    owner_id      text NOT NULL,
    agent         text NOT NULL DEFAULT 'kell',
    goal_id       uuid,
    data_class    text NOT NULL DEFAULT 'PUBLIC_SOURCE',
    risk_ceiling  text NOT NULL DEFAULT 'R0'
                  CHECK (risk_ceiling IN ('R0', 'R1', 'R2', 'R3', 'R4')),
    max_calls     integer NOT NULL DEFAULT 20 CHECK (max_calls > 0),
    claims        jsonb NOT NULL,
    signature     text NOT NULL,
    issued_by     text NOT NULL DEFAULT 'policy-engine',
    issued_at     timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS idx_intent_certificates_task ON intent_certificates (task_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_intent_certificates_owner ON intent_certificates (owner_id, expires_at DESC);

ALTER TABLE intent_certificates ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON intent_certificates FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON intent_certificates FROM authenticated;
    END IF;
END $$;
