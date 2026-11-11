-- 196_tool_approvals.sql
--
-- Next free number: 191 (task runs) is the highest applied here; 192-195 are reserved for Phase 3
-- (trust, trials, verification, listings). This file takes 196 so it cannot collide with them.
--
-- What this adds: human approvals for tool calls the policy marks `approve` (docs/goal_router.md P1-11).
-- One row is one exact call shape: owner, task, server, tool, argument hash and certificate. It is
-- short-lived and single-use. The gate creates a pending row when it returns `approve`, the person
-- approves or denies it, and the next identical call consumes it once.
--
-- Never the arguments themselves, only their hash: the card can say what will run without keeping the
-- payload. Access: application only, like 160/162/163/190. Idempotent.

CREATE TABLE IF NOT EXISTS tool_approvals (
    id            text PRIMARY KEY,                    -- ap_...
    owner_id      text NOT NULL,
    task_id       text,
    goal_id       uuid,
    server        text NOT NULL,
    tool          text NOT NULL,
    args_hash     text NOT NULL,
    risk          text NOT NULL DEFAULT 'R2',
    data_class    text NOT NULL DEFAULT 'PUBLIC_SOURCE',
    cert_id       text,
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'used')),
    requested_at  timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz,
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz,
    decided_by    text,
    note          text
);

CREATE INDEX IF NOT EXISTS idx_tool_approvals_owner
    ON tool_approvals (owner_id, status, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_approvals_call
    ON tool_approvals (owner_id, server, tool, args_hash);

ALTER TABLE tool_approvals ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_approvals FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_approvals FROM authenticated;
    END IF;
END $$;
