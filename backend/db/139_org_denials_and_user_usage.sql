-- 139_org_denials_and_user_usage.sql
--
-- What an organisation's policy refused, and spend per person.
-- Next free number: 138 was highest before this file.
--
--   org_denials              one row each time the organisation's policy or budget refuses a provider call: who, what,
--                            and a machine-readable reason. No prompt or reply content, ever. Append-only; erasable only
--                            under an approved erasure request (same rule as the ledger).
--   v_org_usage_by_user_daily spend and tokens per person per day. `actor_subject` is the identity provider's user id, not
--                            an email.
--
-- Same rules as db/136: strict row-level security (an unset tenant setting sees nothing), security_invoker views.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS org_denials (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    actor_subject    TEXT NOT NULL,
    tool             TEXT NOT NULL,
    unit             TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    data_class       TEXT NOT NULL,
    reason_code      TEXT NOT NULL CHECK (reason_code IN (
                         'kill_switch', 'no_policy', 'provider_not_allowed', 'model_not_allowed', 'tool_not_allowed',
                         'data_class_not_allowed', 'unpriced_unit', 'monthly_budget_zero', 'user_daily_budget_zero',
                         'monthly_budget_exceeded', 'user_daily_budget_exceeded')),
    detail           TEXT NOT NULL,               -- the message the caller saw; contains no content
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_org_denials_org_time ON org_denials (organization_id, created_at DESC, id);

CREATE OR REPLACE FUNCTION tg_denials_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' AND sl_erasure_authorized(OLD.organization_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'org_denials is append-only: deleting needs an approved erasure request and no legal hold'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS tg_denials_guard ON org_denials;
CREATE TRIGGER tg_denials_guard BEFORE UPDATE OR DELETE ON org_denials
    FOR EACH ROW EXECUTE FUNCTION tg_denials_guard();

ALTER TABLE org_denials ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_denials FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'org_denials' AND policyname = 'org_strict') THEN
        CREATE POLICY org_strict ON org_denials FOR ALL USING (sl_org_strict(organization_id))
            WITH CHECK (sl_org_strict(organization_id));
    END IF;
END $$;

CREATE OR REPLACE VIEW v_org_usage_by_user_daily WITH (security_invoker = true) AS
SELECT
    organization_id,
    actor_subject,
    (created_at AT TIME ZONE 'UTC')::date                                      AS day,
    count(*) FILTER (WHERE status IN ('settled', 'failed'))                   AS calls,
    count(*) FILTER (WHERE status = 'failed')                                 AS failed,
    COALESCE(sum(tokens_input_fresh), 0)                                      AS tokens_input_fresh,
    COALESCE(sum(tokens_cache_read), 0)                                       AS tokens_cache_read,
    COALESCE(sum(tokens_cache_write), 0)                                      AS tokens_cache_write,
    COALESCE(sum(tokens_output), 0)                                           AS tokens_output,
    COALESCE(sum(cost_usd) FILTER (WHERE status = 'settled'), 0)              AS cost_usd,
    COALESCE(sum(cost_input_usd) FILTER (WHERE status = 'settled'), 0)        AS cost_input_usd,
    COALESCE(sum(cost_output_usd) FILTER (WHERE status = 'settled'), 0)       AS cost_output_usd,
    COALESCE(sum(cost_cache_read_usd) FILTER (WHERE status = 'settled'), 0)   AS cost_cache_read_usd,
    COALESCE(sum(cost_cache_write_usd) FILTER (WHERE status = 'settled'), 0)  AS cost_cache_write_usd,
    count(*) FILTER (WHERE status = 'settled' AND cost_source = 'upper_bound') AS upper_bound_calls
FROM provider_call_ledger
GROUP BY organization_id, actor_subject, (created_at AT TIME ZONE 'UTC')::date;
