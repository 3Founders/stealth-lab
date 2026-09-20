-- Operational alert de-duplication state (app/ingestion/ops_alerts.py). One row per alert key:
-- an alert notifies once when it has been true for N consecutive evaluations, re-notifies only after a
-- cool-down, and sends one "resolved" when it clears. Also holds the last result of each verify-* run
-- (key 'verify:<name>') so a failed integrity check keeps alerting until it passes again.
-- Purely operational; no canonical data references it. Idempotent.
CREATE TABLE IF NOT EXISTS ops_alert_state (
    alert_key     TEXT PRIMARY KEY,
    firing        BOOLEAN NOT NULL DEFAULT false,
    consecutive   INTEGER NOT NULL DEFAULT 0,
    severity      TEXT NOT NULL DEFAULT 'warning',
    first_seen    TIMESTAMPTZ,
    last_notified TIMESTAMPTZ,
    detail        JSONB NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ops_alert_state_sev_chk CHECK (severity IN ('info', 'warning', 'critical'))
);
