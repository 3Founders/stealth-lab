-- Migration 150: row-level security that DENIES when no scope is bound (securityp1.md §5.1 #3/#4; runbook residual
-- risks #8/#9). Next free number: 151.
--
-- BEFORE: two helpers were permissive when their setting was unset.
--   * sl_tenant_scope_allows (db/29: evidence, executions, change_sets, change_set_operations, failure_routes)
--     compared a row's tenant with itself when app.tenant_id was unset -> always true.
--   * sl_owner_scope_allows (db/109: synced_projects, sync_device_credentials) did the same with app.owner_subject,
--     and NO code path ever set app.owner_subject -- the sync tables' RLS never enforced anything.
--   * routing_decisions / routing_observations (db/120-121) had no RLS at all, though they carry visibility/owner_id.
-- A query that forgot its scope therefore saw every tenant's / owner's rows. With FORCE RLS this matters even for the
-- table owner, which is how the services connect today.
--
-- AFTER:
--   * app.rls_system = 'on' -- an EXPLICIT system scope (workers, maintenance, the nightly fit, admin commands; set by
--     app/services/access.py), the only way to see across owners/tenants. Never set for a user request.
--   * tenants: unset -> only the shared commons tenant's rows (every row written today carries it: no code path writes
--     another tenant_id to these tables, audited 2026-10-07); bound -> only that tenant's rows (unchanged).
--   * owners (sync): unset -> nothing; bound -> only that owner's rows.
--   * routing: 'public' rows for everyone; other rows only when owner_id is in app.routing_readers (the caller's
--     subject and organisations), or under the system scope.
--
-- Function bodies only change (CREATE OR REPLACE) plus the two routing policies; no data changes. Idempotent.

CREATE OR REPLACE FUNCTION sl_rls_system()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(current_setting('app.rls_system', true), '') = 'on';
$$;

CREATE OR REPLACE FUNCTION sl_tenant_scope_allows(p_row_tenant UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT sl_rls_system() OR CASE
        WHEN NULLIF(current_setting('app.tenant_id', true), '') IS NULL
            THEN p_row_tenant = '00000000-0000-0000-0000-000000000001'::uuid      -- the shared commons only
        ELSE p_row_tenant::text = current_setting('app.tenant_id', true)
    END;
$$;

CREATE OR REPLACE FUNCTION sl_owner_scope_allows(p_row_owner TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT sl_rls_system() OR (
        NULLIF(current_setting('app.owner_subject', true), '') IS NOT NULL
        AND p_row_owner = current_setting('app.owner_subject', true)
    );
$$;

CREATE OR REPLACE FUNCTION sl_routing_allows(p_visibility TEXT, p_owner TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT sl_rls_system()
        OR p_visibility = 'public'
        OR (p_owner IS NOT NULL
            AND p_owner = ANY (string_to_array(NULLIF(current_setting('app.routing_readers', true), ''), ',')));
$$;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['routing_decisions', 'routing_observations'] LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = t) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
            IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = t AND policyname = 'routing_isolation') THEN
                EXECUTE format(
                    'CREATE POLICY routing_isolation ON %I FOR ALL '
                    'USING (sl_routing_allows(visibility, owner_id)) '
                    'WITH CHECK (sl_routing_allows(visibility, owner_id))', t);
            END IF;
        END IF;
    END LOOP;
END $$;
