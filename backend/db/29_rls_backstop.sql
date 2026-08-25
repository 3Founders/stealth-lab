-- Migration 29 (Lane CORE-A, HARDENING H2): row-level security BACKSTOP
-- beneath the app-layer tenancy predicates, on the append-only truth
-- tables.
--
-- WHAT THIS IS NOT: the primary policy. Every read/write path filters
-- through services/access.py's builders (V2 visibility, H1 tenancy);
-- those stay the SINGLE policy source. Two independent enforcers that can
-- disagree are worse than one -- so this layer adds no rules of its own,
-- only re-states the one the builder already makes, keyed to a setting
-- the builder's transaction wrapper binds. It exists because app-layer
-- predicates protect only against queries that remember to use them; a
-- backstop protects against the query someone writes next year without
-- one.
--
-- MECHANISM: every policy reads current_setting('app.tenant_id', true).
-- That setting is bound ONLY inside a transaction, by
-- services/access.py::tenant_transaction() via set_config(..., TRUE) --
-- the parameterizable spelling of SET LOCAL (asyncpg cannot parameterize
-- SET LOCAL itself). THE ASYNCPG CAVEAT IS BINDING: a session-scoped
-- setting survives pool.release() and leaks onto the NEXT borrower of
-- that physical connection -- cross-tenant visibility in one direction,
-- silent data-blinding in the other. Transaction-local settings die at
-- COMMIT *and* ROLLBACK, so there is no cleanup path to forget and no
-- exception path that leaves residue.
--
-- PERMISSIVE-WHEN-UNSET, ON PURPOSE (why this can ship today):
-- unset/empty app.tenant_id falls back to the row's own tenant_id --
-- every existing query path behaves EXACTLY as before, which IS the
-- public-commons posture: all rows carry the commons tenant anyway.
-- Enforcement arms precisely where the helper is adopted and nowhere
-- else, so the backstop cannot claim enforcement it does not have --
-- the same visible-posture rule access.py's builders follow
-- (unrestricted renders literal TRUE, never silence). Flipping to
-- default-deny later is a one-line change to sl_tenant_scope_allows
-- plus an enforced adoption sweep, not a migration rewrite.
--
-- FORCE ROW LEVEL SECURITY: without FORCE, RLS never applies to the
-- table OWNER -- and this deployment's backend typically connects as
-- the owner, i.e. the backstop would be decorative exactly where it
-- matters. With FORCE + permissive-when-unset, owner connections behave
-- identically unless a transaction carries the setting. Views over the
-- covered tables (procedure_evidence_stats) execute with their owner's
-- rights and inherit the same unset-means-unchanged behavior.
--
-- TABLES COVERED -- append-only TRUTH ([H]) only:
--   evidence (24)               outcome/verification record
--   executions (23)             what actually ran
--   change_sets (25)            who changed what, and why
--   change_set_operations (25)  per-object operation detail
--   failure_routes (27)         §36 routing decisions -- Band 2.4's [H]
--                               log, this lane's table, so it rides
--                               along ("at minimum" named the first four)
--
-- TABLES DELIBERATELY LEFT RLS-FREE (the public-commons posture, until a
-- founder ruling says otherwise):
--   * knowledge_nodes / task_nodes / edges / episodes / traces /
--     observations / states / procedures / implementations: the shared
--     commons SUBSTRATE. Their isolation story is the app-layer
--     visibility+tenancy pair threaded at every query path (V2/H1).
--     RLS here is the multi-tenancy CUTOVER decision -- it needs the
--     query-path adoption sweep first (cross-lane request #2), not just
--     a policy.
--   * execution_plans / task_graphs: [D→frozen] DERIVED artifacts of
--     their procedure version -- compiled then frozen, not
--     tenant-partitioned truth; they become isolated when procedures do.
--   * organizations / users / roles / org_memberships: identity MASTER
--     DATA (28) -- these rows DEFINE tenants; tenant-scoped RLS on them
--     is circular. They answer to the authn boundary instead.
--   * governance/rate-limit tables: process-local enforcement plus audit
--     ledger (H3); scoping there is by key, not by organization.
--
-- Idempotent: safe to re-run. Fresh-start compliant: additive column
-- birth with the commons default -- zero backfill, every pre-existing
-- row resolves to the seeded organization exactly as V0's core tables'
-- rows always have.
-- Next free number: 28 was highest before this file.

-- ============================================================
-- 1. The ONE expression every policy delegates to. Single definition =
-- no USING/WITH CHECK drift: five tables' worth of policies cannot
-- disagree with each other or with themselves. STABLE: reads a GUC,
-- touches nothing.
--
-- Semantics:
--   setting UNSET or empty -> COALESCE falls back to the row's own
--                             tenant_id -> always true -> exactly
--                             today's posture;
--   setting BOUND          -> the row must belong to THAT organization.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_tenant_scope_allows(p_row_tenant UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT p_row_tenant::text = COALESCE(
        NULLIF(current_setting('app.tenant_id', true), ''),
        p_row_tenant::text
    );
$$;

-- ============================================================
-- 2. evidence (db/24) -- outcome/verification record.
-- Column birth mirrors V0's core tables: NOT NULL DEFAULT commons, so
-- existing rows resolve to the seeded organization untouched.
-- ============================================================
ALTER TABLE evidence
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001';

CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence(tenant_id);

ALTER TABLE evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'evidence' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON evidence
            FOR ALL
            USING (sl_tenant_scope_allows(tenant_id))
            WITH CHECK (sl_tenant_scope_allows(tenant_id));
    END IF;
END $$;

-- ============================================================
-- 3. executions (db/23) -- what actually ran.
-- ============================================================
ALTER TABLE executions
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001';

CREATE INDEX IF NOT EXISTS idx_executions_tenant ON executions(tenant_id);

ALTER TABLE executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE executions FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'executions' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON executions
            FOR ALL
            USING (sl_tenant_scope_allows(tenant_id))
            WITH CHECK (sl_tenant_scope_allows(tenant_id));
    END IF;
END $$;

-- ============================================================
-- 4. change_sets (db/25) -- who changed what, and why.
-- ============================================================
ALTER TABLE change_sets
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001';

CREATE INDEX IF NOT EXISTS idx_change_sets_tenant ON change_sets(tenant_id);

ALTER TABLE change_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_sets FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'change_sets' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON change_sets
            FOR ALL
            USING (sl_tenant_scope_allows(tenant_id))
            WITH CHECK (sl_tenant_scope_allows(tenant_id));
    END IF;
END $$;

-- ============================================================
-- 5. change_set_operations (db/25) -- per-object operation detail.
-- ============================================================
ALTER TABLE change_set_operations
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001';

CREATE INDEX IF NOT EXISTS idx_cso_tenant ON change_set_operations(tenant_id);

ALTER TABLE change_set_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_set_operations FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'change_set_operations' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON change_set_operations
            FOR ALL
            USING (sl_tenant_scope_allows(tenant_id))
            WITH CHECK (sl_tenant_scope_allows(tenant_id));
    END IF;
END $$;

-- ============================================================
-- 6. failure_routes (db/27) -- §36 routing decisions ([H] log).
-- ============================================================
ALTER TABLE failure_routes
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001';

CREATE INDEX IF NOT EXISTS idx_failure_routes_tenant
    ON failure_routes(tenant_id);

ALTER TABLE failure_routes ENABLE ROW LEVEL SECURITY;
ALTER TABLE failure_routes FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'failure_routes' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON failure_routes
            FOR ALL
            USING (sl_tenant_scope_allows(tenant_id))
            WITH CHECK (sl_tenant_scope_allows(tenant_id));
    END IF;
END $$;
