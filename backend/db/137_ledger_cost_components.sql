-- 137_ledger_cost_components.sql
--
-- Split each settled call's cost into input, output and cache costs, and keep the prices used.
-- Next free number: 136 was highest before this file.
--
-- Why in the database and not recomputed later: the amount shown to a customer (and, for gain-share, the amount an
-- invoice rests on) must equal what was recorded at call time. A price that changes next month must not change last
-- month's breakdown, so the price snapshot is stored with the row and the components are computed once, at settlement.
--
-- Components exist ONLY when the cost came from declared prices (cost_source = 'declared'). A provider-reported cost
-- (cost_source = 'provider') or a worst-case bound (cost_source = 'upper_bound') has no per-component split, and the view
-- reports the remainder as cost_unattributed_usd instead of inventing one. The components can differ from cost_usd by
-- rounding (six decimals each).
--
-- Idempotent: safe to re-run.

ALTER TABLE provider_call_ledger
    ADD COLUMN IF NOT EXISTS price_input_per_mtok       NUMERIC(14, 6) CHECK (price_input_per_mtok IS NULL OR price_input_per_mtok >= 0),
    ADD COLUMN IF NOT EXISTS price_output_per_mtok      NUMERIC(14, 6) CHECK (price_output_per_mtok IS NULL OR price_output_per_mtok >= 0),
    ADD COLUMN IF NOT EXISTS price_cache_read_per_mtok  NUMERIC(14, 6) CHECK (price_cache_read_per_mtok IS NULL OR price_cache_read_per_mtok >= 0),
    ADD COLUMN IF NOT EXISTS price_cache_write_per_mtok NUMERIC(14, 6) CHECK (price_cache_write_per_mtok IS NULL OR price_cache_write_per_mtok >= 0),
    ADD COLUMN IF NOT EXISTS cost_input_usd             NUMERIC(14, 6) CHECK (cost_input_usd IS NULL OR cost_input_usd >= 0),
    ADD COLUMN IF NOT EXISTS cost_output_usd            NUMERIC(14, 6) CHECK (cost_output_usd IS NULL OR cost_output_usd >= 0),
    ADD COLUMN IF NOT EXISTS cost_cache_read_usd        NUMERIC(14, 6) CHECK (cost_cache_read_usd IS NULL OR cost_cache_read_usd >= 0),
    ADD COLUMN IF NOT EXISTS cost_cache_write_usd       NUMERIC(14, 6) CHECK (cost_cache_write_usd IS NULL OR cost_cache_write_usd >= 0);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_ledger_components_need_declared_cost') THEN
        ALTER TABLE provider_call_ledger ADD CONSTRAINT chk_ledger_components_need_declared_cost
            CHECK ((cost_input_usd IS NULL AND cost_output_usd IS NULL AND cost_cache_read_usd IS NULL
                    AND cost_cache_write_usd IS NULL) OR cost_source = 'declared');
    END IF;
END $$;

-- Same columns as before, in the same order, plus the new ones at the end (CREATE OR REPLACE VIEW only allows that).
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
    count(*) FILTER (WHERE status = 'settled' AND cost_source = 'upper_bound') AS upper_bound_calls,
    COALESCE(sum(cost_input_usd) FILTER (WHERE status = 'settled'), 0)       AS cost_input_usd,
    COALESCE(sum(cost_output_usd) FILTER (WHERE status = 'settled'), 0)      AS cost_output_usd,
    COALESCE(sum(cost_cache_read_usd) FILTER (WHERE status = 'settled'), 0)  AS cost_cache_read_usd,
    COALESCE(sum(cost_cache_write_usd) FILTER (WHERE status = 'settled'), 0) AS cost_cache_write_usd,
    COALESCE(sum(cost_usd) FILTER (WHERE status = 'settled'), 0)
      - COALESCE(sum(cost_input_usd + cost_output_usd + cost_cache_read_usd + cost_cache_write_usd)
                 FILTER (WHERE status = 'settled'), 0)                      AS cost_unattributed_usd
FROM provider_call_ledger
GROUP BY organization_id, (created_at AT TIME ZONE 'UTC')::date, provider, model, tool;
