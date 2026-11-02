-- Auto-renew (Razorpay Subscriptions) for the ladder plans, 2026-11-02. The one-person First Trial is NOT offered with
-- auto-renew: it is one per account, so a second charge would be refused by the entitlement index and need manual review.
--
--   billing_plan_rows.rzp_plan_*   the Razorpay Plan made for this row (Razorpay plans are immutable): cached with the
--                                  amount and period they were made for, so a dashboard edit to either makes a new one
--   billing_subscriptions          one row per recurring mandate we created; the webhook finds it by the Razorpay id and
--                                  activates the plan from it (the charged amount is checked against amount_paise)

ALTER TABLE billing_plan_rows ADD COLUMN IF NOT EXISTS rzp_plan_id     TEXT;
ALTER TABLE billing_plan_rows ADD COLUMN IF NOT EXISTS rzp_plan_paise  BIGINT;
ALTER TABLE billing_plan_rows ADD COLUMN IF NOT EXISTS rzp_plan_days   INTEGER;

CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_user_id             UUID NOT NULL,
    plan                      TEXT NOT NULL CHECK (plan IN ('starter', 'pro', 'plus')),
    seats                     INTEGER NOT NULL DEFAULT 1 CHECK (seats BETWEEN 1 AND 10),
    referral_code             TEXT,
    amount_paise              BIGINT NOT NULL CHECK (amount_paise > 0),   -- what every charge must be, in paise
    period_days               INTEGER NOT NULL CHECK (period_days BETWEEN 7 AND 365),
    razorpay_subscription_id  TEXT UNIQUE NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'created',
    current_end               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS billing_subscriptions_buyer
    ON billing_subscriptions (buyer_user_id, created_at DESC);
