-- 138_ledger_timing_and_tier.sql
--
-- Per-call timing and a tier label on the provider-call ledger, and the performance view built on them.
-- Next free number: 137 was highest before this file.
--
--   gate_ms   milliseconds OUR code spent before the provider was called: finding the connection, the policy and
--             budget reservation, the endpoint safety check and credential lookup. This is the router overhead.
--   latency_ms (already there) the provider's own time for the call.
--   tier      an admin-chosen label for the unit ("light", "standard", "flagship", ...) from its connection, so quick
--             models and flagship models can be compared. NULL means the connection did not set one; it is not guessed.
--
-- v_org_performance_daily reports, per organisation, day, provider, model, tool and tier:
--   * p50/p95/p99 of provider latency and of our gate time, and our share of the total time;
--   * cache hit rates computed ONLY over calls whose provider reported cache tokens: an endpoint that does not report
--     them is left out of the rate, not counted as a miss (so a rate of NULL means "not reported", never "0%").
--
-- Idempotent: safe to re-run.

ALTER TABLE provider_call_ledger
    ADD COLUMN IF NOT EXISTS gate_ms INTEGER CHECK (gate_ms IS NULL OR gate_ms >= 0),
    ADD COLUMN IF NOT EXISTS tier    TEXT CHECK (tier IS NULL OR (length(tier) BETWEEN 1 AND 32 AND tier ~ '^[a-z0-9_-]+$'));

CREATE OR REPLACE VIEW v_org_performance_daily WITH (security_invoker = true) AS
SELECT
    organization_id,
    (created_at AT TIME ZONE 'UTC')::date AS day,
    provider, model, tool, tier,
    count(*) FILTER (WHERE status = 'settled')                                                   AS calls,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'settled' AND latency_ms IS NOT NULL) AS provider_p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'settled' AND latency_ms IS NOT NULL) AS provider_p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'settled' AND latency_ms IS NOT NULL) AS provider_p99_ms,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY gate_ms)    FILTER (WHERE status = 'settled' AND gate_ms IS NOT NULL)    AS gate_p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY gate_ms)    FILTER (WHERE status = 'settled' AND gate_ms IS NOT NULL)    AS gate_p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY gate_ms)    FILTER (WHERE status = 'settled' AND gate_ms IS NOT NULL)    AS gate_p99_ms,
    -- our share of the time between "call received" and "provider answered", over calls that have both numbers
    (sum(gate_ms) FILTER (WHERE status = 'settled' AND gate_ms IS NOT NULL AND latency_ms IS NOT NULL))::numeric
      / NULLIF(sum(gate_ms + latency_ms) FILTER (WHERE status = 'settled' AND gate_ms IS NOT NULL AND latency_ms IS NOT NULL), 0)
                                                                                                 AS gate_time_share,
    count(*) FILTER (WHERE status = 'settled' AND tokens_cache_read IS NOT NULL)                 AS calls_reporting_cache,
    count(*) FILTER (WHERE status = 'settled' AND tokens_cache_read > 0)                         AS calls_with_cache_hit,
    (count(*) FILTER (WHERE status = 'settled' AND tokens_cache_read > 0))::numeric
      / NULLIF(count(*) FILTER (WHERE status = 'settled' AND tokens_cache_read IS NOT NULL), 0)  AS cache_hit_rate_requests,
    (sum(tokens_cache_read) FILTER (WHERE status = 'settled' AND tokens_cache_read IS NOT NULL))::numeric
      / NULLIF(sum(COALESCE(tokens_input_fresh, 0) + tokens_cache_read + COALESCE(tokens_cache_write, 0))
               FILTER (WHERE status = 'settled' AND tokens_cache_read IS NOT NULL), 0)            AS cache_hit_rate_tokens
FROM provider_call_ledger
GROUP BY organization_id, (created_at AT TIME ZONE 'UTC')::date, provider, model, tool, tier;
