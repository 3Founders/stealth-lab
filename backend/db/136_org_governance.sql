-- 136_org_governance.sql
--
-- What an organisation's admins control, what every provider call costs, and how an organisation's data leaves.
-- Next free number: 135 was highest before this file.
--
-- DESIGN RULE for everything here: NO FALLBACK. An absent policy is a denial, a zero budget is zero spend, an
-- unset tenant setting sees no rows, a missing price blocks a budgeted call, and nothing "assumes the best".
-- (The older sl_tenant_scope_allows() in db/29 deliberately falls back to "allow" when app.tenant_id is unset;
-- the tables below use sl_org_strict() instead, which does the opposite.)
--
--   1. org_policies          one row per organisation: kill switch, allowlists, data classes, spend caps.
--   2. provider_call_ledger  every provider call: reserved before it is sent, settled once after. Four token
--                            fields kept apart (fresh input / cache read / cache write / output) because
--                            providers disagree about whether "input" includes cached tokens.
--   3. org_legal_holds       an active hold blocks erasure.
--   4. org_erasure_requests  two-person request -> approval -> execution, with a manifest.
--   5. audit_events          gains a per-tenant hash chain (tamper evidence) and is made append-only.
--   6. v_org_usage_daily     per-org daily usage, security_invoker so row-level security still applies.
--
-- Idempotent: safe to re-run.

-- ============================================================
-- 0. Strict tenant predicate and erasure authorisation
-- ============================================================
CREATE OR REPLACE FUNCTION sl_org_strict(p_org UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT p_org IS NOT NULL
       AND NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
       AND p_org::text = current_setting('app.tenant_id', true);
$$;

-- ============================================================
-- 3. Legal holds (declared before the erasure function that reads them)
-- ============================================================
CREATE TABLE IF NOT EXISTS org_legal_holds (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) > 0),
    placed_by        TEXT NOT NULL,
    placed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_by      TEXT,
    released_at      TIMESTAMPTZ,
    CHECK ((released_at IS NULL) = (released_by IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_org_legal_holds_active
    ON org_legal_holds (organization_id) WHERE released_at IS NULL;

-- ============================================================
-- 4. Erasure requests: requested by one person, approved by another
-- ============================================================
CREATE TABLE IF NOT EXISTS org_erasure_requests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    requested_by     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'requested'
                     CHECK (status IN ('requested', 'approved', 'executing', 'completed', 'rejected')),
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) > 0),
    approved_by      TEXT,
    approved_at      TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    manifest         JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {table: {deleted: n, remaining: n} | {blocked: reason}}
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (approved_by IS NULL OR approved_by <> requested_by),                 -- two people
    CHECK (status NOT IN ('approved', 'executing', 'completed') OR approved_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_org_erasure_org ON org_erasure_requests (organization_id, created_at DESC);

-- True only inside a transaction that names an APPROVED/EXECUTING erasure request for this very organisation, and
-- only while the organisation has no active legal hold. Core tables' delete-refusing triggers can call the same
-- function when their erasure path is built (docs/ship_readiness_plan.md).
CREATE OR REPLACE FUNCTION sl_erasure_authorized(p_org UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT p_org IS NOT NULL
       AND NULLIF(current_setting('app.erasure_request', true), '') IS NOT NULL
       AND EXISTS (SELECT 1 FROM org_erasure_requests r
                    WHERE r.id::text = current_setting('app.erasure_request', true)
                      AND r.organization_id = p_org
                      AND r.status IN ('approved', 'executing'))
       AND NOT EXISTS (SELECT 1 FROM org_legal_holds h
                        WHERE h.organization_id = p_org AND h.released_at IS NULL);
$$;

-- ============================================================
-- 1. Org policies: default deny
-- ============================================================
CREATE TABLE IF NOT EXISTS org_policies (
    organization_id           UUID PRIMARY KEY REFERENCES organizations(id),
    kill_switch               BOOLEAN NOT NULL DEFAULT FALSE,
    kill_switch_reason        TEXT,
    -- EXPLICIT allowlists. Empty means nothing is allowed; there is no "allow all" value.
    allowed_providers         TEXT[] NOT NULL DEFAULT '{}',
    allowed_models            TEXT[] NOT NULL DEFAULT '{}',
    allowed_tools             TEXT[] NOT NULL DEFAULT '{}',
    allowed_data_classes      TEXT[] NOT NULL DEFAULT '{}',
    -- 0 means zero spend allowed. "Unlimited" is not expressible; set a number.
    monthly_budget_usd        NUMERIC(14, 4) NOT NULL DEFAULT 0 CHECK (monthly_budget_usd >= 0),
    per_user_daily_budget_usd NUMERIC(14, 4) NOT NULL DEFAULT 0 CHECK (per_user_daily_budget_usd >= 0),
    version                   INTEGER NOT NULL DEFAULT 1,
    updated_by                TEXT NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (NOT kill_switch OR length(btrim(COALESCE(kill_switch_reason, ''))) > 0)   -- a reason is required to stop an org
);

-- ============================================================
-- 2. Provider-call ledger: reserve, then settle once
-- ============================================================
CREATE TABLE IF NOT EXISTS provider_call_ledger (
    id                   UUID PRIMARY KEY,
    organization_id      UUID NOT NULL REFERENCES organizations(id),   -- only org-governed calls are ledgered
    actor_subject        TEXT NOT NULL,
    tool                 TEXT NOT NULL,
    unit                 TEXT NOT NULL,
    connection_id        TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT NOT NULL,
    scaffold             TEXT NOT NULL,
    data_class           TEXT NOT NULL,
    instance_key         TEXT,
    status               TEXT NOT NULL CHECK (status IN ('reserved', 'settled', 'failed')),
    reserved_usd         NUMERIC(14, 6) NOT NULL CHECK (reserved_usd >= 0),   -- worst case, held against the budget
    tokens_input_fresh   BIGINT CHECK (tokens_input_fresh IS NULL OR tokens_input_fresh >= 0),
    tokens_cache_read    BIGINT CHECK (tokens_cache_read IS NULL OR tokens_cache_read >= 0),
    tokens_cache_write   BIGINT CHECK (tokens_cache_write IS NULL OR tokens_cache_write >= 0),
    tokens_output        BIGINT CHECK (tokens_output IS NULL OR tokens_output >= 0),
    cost_usd             NUMERIC(14, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    -- provider = the provider reported the dollars; declared = tokens x the connection's declared price;
    -- upper_bound = no usage came back, so the reserved worst case is what is recorded (never zero).
    cost_source          TEXT CHECK (cost_source IN ('provider', 'declared', 'upper_bound')),
    latency_ms           INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    error_type           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at           TIMESTAMPTZ,
    CHECK ((status = 'settled') = (cost_usd IS NOT NULL AND cost_source IS NOT NULL)),
    CHECK ((status = 'reserved') = (settled_at IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_ledger_org_time ON provider_call_ledger (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_org_actor_time ON provider_call_ledger (organization_id, actor_subject, created_at DESC);

CREATE OR REPLACE FUNCTION tg_ledger_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF sl_erasure_authorized(OLD.organization_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'provider_call_ledger is append-only: deleting needs an approved erasure request and no legal hold'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- UPDATE: a reserved row may move to settled or failed, exactly once, changing nothing else about who/what/when
    IF OLD.status <> 'reserved' THEN
        RAISE EXCEPTION 'ledger row % is already %', OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status NOT IN ('settled', 'failed') THEN
        RAISE EXCEPTION 'a reserved ledger row can only become settled or failed'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.organization_id, NEW.actor_subject, NEW.tool, NEW.unit, NEW.connection_id, NEW.provider,
        NEW.model, NEW.scaffold, NEW.data_class, NEW.instance_key, NEW.reserved_usd, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.organization_id, OLD.actor_subject, OLD.tool, OLD.unit, OLD.connection_id, OLD.provider,
        OLD.model, OLD.scaffold, OLD.data_class, OLD.instance_key, OLD.reserved_usd, OLD.created_at) THEN
        RAISE EXCEPTION 'ledger identity columns are immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_ledger_guard ON provider_call_ledger;
CREATE TRIGGER tg_ledger_guard BEFORE UPDATE OR DELETE ON provider_call_ledger
    FOR EACH ROW EXECUTE FUNCTION tg_ledger_guard();

-- ============================================================
-- Row-level security on the new tables: STRICT (unset setting = no rows, no writes)
-- ============================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['org_policies', 'provider_call_ledger', 'org_legal_holds', 'org_erasure_requests'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = t AND policyname = 'org_strict') THEN
            EXECUTE format(
                'CREATE POLICY org_strict ON %I FOR ALL USING (sl_org_strict(organization_id)) '
                'WITH CHECK (sl_org_strict(organization_id))', t);
        END IF;
    END LOOP;
END $$;

-- ============================================================
-- 5. audit_events: per-tenant hash chain and append-only
-- ============================================================
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS row_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_id ON audit_events (tenant_id, id DESC);

CREATE OR REPLACE FUNCTION sl_audit_row_hash(p_prev TEXT, p_id BIGINT, p_t TIMESTAMPTZ, p_actor TEXT, p_user UUID,
                                             p_action TEXT, p_type TEXT, p_object TEXT, p_tenant UUID, p_details JSONB)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT encode(sha256(convert_to(
        p_prev || '|' || p_id::text || '|' || extract(epoch FROM p_t)::text || '|' || p_actor || '|' ||
        COALESCE(p_user::text, '') || '|' || p_action || '|' || p_type || '|' || p_object || '|' ||
        COALESCE(p_tenant::text, '') || '|' || p_details::text, 'UTF8')), 'hex');
$$;

CREATE OR REPLACE FUNCTION tg_audit_chain()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    prev TEXT;
BEGIN
    -- one writer at a time per tenant chain; held to the end of the transaction
    PERFORM pg_advisory_xact_lock(hashtext('audit_chain:' || COALESCE(NEW.tenant_id::text, 'global')));
    SELECT row_hash INTO prev FROM audit_events
     WHERE tenant_id IS NOT DISTINCT FROM NEW.tenant_id AND row_hash IS NOT NULL
     ORDER BY id DESC LIMIT 1;
    NEW.prev_hash := COALESCE(prev, repeat('0', 64));
    NEW.row_hash := sl_audit_row_hash(NEW.prev_hash, NEW.id, NEW.t_created, NEW.actor_subject, NEW.actor_user_id,
                                      NEW.action, NEW.object_type, NEW.object_id, NEW.tenant_id, NEW.details);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_audit_chain ON audit_events;
CREATE TRIGGER tg_audit_chain BEFORE INSERT ON audit_events
    FOR EACH ROW EXECUTE FUNCTION tg_audit_chain();

CREATE OR REPLACE FUNCTION tg_audit_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS tg_audit_append_only ON audit_events;
CREATE TRIGGER tg_audit_append_only BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION tg_audit_append_only();

-- Returns the id of the first row whose stored hash does not match its recomputed hash (or whose prev_hash does not
-- match the row before it), or NULL when the tenant's chain is intact. Rows from before this migration carry no hash
-- and are skipped.
CREATE OR REPLACE FUNCTION sl_audit_chain_check(p_tenant UUID)
RETURNS BIGINT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    r RECORD;
    expected_prev TEXT := repeat('0', 64);
BEGIN
    FOR r IN SELECT * FROM audit_events
              WHERE tenant_id IS NOT DISTINCT FROM p_tenant AND row_hash IS NOT NULL ORDER BY id LOOP
        IF r.prev_hash IS DISTINCT FROM expected_prev OR r.row_hash IS DISTINCT FROM
           sl_audit_row_hash(r.prev_hash, r.id, r.t_created, r.actor_subject, r.actor_user_id, r.action,
                             r.object_type, r.object_id, r.tenant_id, r.details) THEN
            RETURN r.id;
        END IF;
        expected_prev := r.row_hash;
    END LOOP;
    RETURN NULL;
END;
$$;

-- ============================================================
-- 6. Usage view (security_invoker: the CALLER's row-level security applies, not the view owner's)
-- ============================================================
CREATE OR REPLACE VIEW v_org_usage_daily WITH (security_invoker = true) AS
SELECT
    organization_id,
    (created_at AT TIME ZONE 'UTC')::date                                   AS day,
    provider, model, tool,
    count(*) FILTER (WHERE status IN ('settled', 'failed'))                AS calls,
    count(*) FILTER (WHERE status = 'settled')                             AS succeeded,
    count(*) FILTER (WHERE status = 'failed')                              AS failed,
    COALESCE(sum(tokens_input_fresh), 0)                                   AS tokens_input_fresh,
    COALESCE(sum(tokens_cache_read), 0)                                    AS tokens_cache_read,
    COALESCE(sum(tokens_cache_write), 0)                                   AS tokens_cache_write,
    COALESCE(sum(tokens_output), 0)                                        AS tokens_output,
    COALESCE(sum(cost_usd) FILTER (WHERE status = 'settled'), 0)           AS cost_usd,
    count(*) FILTER (WHERE status = 'settled' AND cost_source = 'upper_bound') AS upper_bound_calls
FROM provider_call_ledger
GROUP BY organization_id, (created_at AT TIME ZONE 'UTC')::date, provider, model, tool;
