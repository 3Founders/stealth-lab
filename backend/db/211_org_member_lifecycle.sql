-- 211_org_member_lifecycle.sql
--
-- What this adds: the two things a team needs after the first invite —
--   1. an invite carries the role the person joins with (owner/admin/member/viewer), not always "member";
--   2. a member can be given a different role or removed (the membership is expired, not deleted).
-- It also records the first-pass containment fields on a relay agent (scopes, data ceiling, budget, expiry,
-- sponsor), so /org/agents can show them. The desktop relay does not enforce them yet; the gate remains the
-- enforcement point. Idempotent.

ALTER TABLE IF EXISTS org_invites
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'admin', 'member', 'viewer'));

ALTER TABLE IF EXISTS org_invites
    ADD COLUMN IF NOT EXISTS email TEXT;

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS data_ceiling TEXT NOT NULL DEFAULT 'PUBLIC_SOURCE';

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS budget_usd_month NUMERIC(12, 4);

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS sponsor_sub TEXT;

CREATE INDEX IF NOT EXISTS idx_delegate_agents_owner_live
    ON delegate_agents (owner_sub) WHERE revoked_at IS NULL;
