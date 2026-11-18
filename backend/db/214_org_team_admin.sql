-- 214_org_team_admin.sql
--
-- Next free number: 215. This file takes 214; 213 belongs to the wallet-refunds lane and 212 to referrals.
--
-- What this adds:
--   1. `delegate_agents.updated_at` / `updated_by`, so an admin can edit an agent's recorded containment
--      (scopes, data ceiling, budget, expiry) without revoking it, and the audit row says who changed it.
--   2. `org_invites.email_sent_at`, so the Members page can show whether the invitation email left.
--   3. Tenant-scoped RLS policies for the person-owned tables the organization pages read and write:
--      `delegate_agents`, `delegate_tasks`, `tool_approvals`, `agent_links`, `desktop_grants`.
--
-- The tables stay closed to `anon`/`authenticated` exactly as before. The policy is a backstop for the backend
-- role only: it says a row is visible when its person is an active member of the tenant bound to the transaction.
-- The services still enforce owner/admin themselves. Write paths that run outside `tenant_transaction()` (the
-- relay and the tool gate) must bind the tenant before the application role is narrowed; this file does not
-- change those paths.
--
-- Idempotent.

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE IF EXISTS delegate_agents
    ADD COLUMN IF NOT EXISTS updated_by TEXT;

ALTER TABLE IF EXISTS org_invites
    ADD COLUMN IF NOT EXISTS email_sent_at TIMESTAMPTZ;

-- The membership check every policy below delegates to. SECURITY DEFINER because it reads identity master data
-- (users, org_memberships) that the backend role may not read directly after the role switch. STABLE: it only
-- reads the transaction-local tenant setting.
CREATE OR REPLACE FUNCTION sl_org_subject(p_sub text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1
          FROM users u
          JOIN org_memberships m ON m.user_id = u.id
         WHERE u.external_subject = p_sub
           AND m.organization_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
           AND m.t_expired IS NULL
    )
$$;

DROP POLICY IF EXISTS org_tenant_access ON delegate_agents;
CREATE POLICY org_tenant_access ON delegate_agents FOR ALL
    USING (sl_org_subject(owner_sub)) WITH CHECK (sl_org_subject(owner_sub));

DROP POLICY IF EXISTS org_tenant_access ON delegate_tasks;
CREATE POLICY org_tenant_access ON delegate_tasks FOR ALL
    USING (sl_org_subject(owner_sub)) WITH CHECK (sl_org_subject(owner_sub));

DROP POLICY IF EXISTS org_tenant_access ON tool_approvals;
CREATE POLICY org_tenant_access ON tool_approvals FOR ALL
    USING (sl_org_subject(owner_id)) WITH CHECK (sl_org_subject(owner_id));

DROP POLICY IF EXISTS org_tenant_access ON agent_links;
CREATE POLICY org_tenant_access ON agent_links FOR ALL
    USING (sl_org_subject(owner_sub) OR sl_org_subject(peer_sub))
    WITH CHECK (sl_org_subject(owner_sub) OR sl_org_subject(peer_sub));

DROP POLICY IF EXISTS org_tenant_access ON desktop_grants;
CREATE POLICY org_tenant_access ON desktop_grants FOR ALL
    USING (sl_org_subject(owner_id) OR sl_org_subject(grantee_id))
    WITH CHECK (sl_org_subject(owner_id) OR sl_org_subject(grantee_id));
