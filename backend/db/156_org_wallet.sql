-- 156: prepaid wallet for organizations (including the hidden personal organization every person gets).
--
-- Why. Spend was capped only by calendar-month and per-person-day budgets in org_policies. To sell prepaid usage, an
-- organization needs a balance that persists: money in (a verified Razorpay payment) minus every call held or settled
-- in provider_call_ledger since the beginning. There is no separate debit table on purpose: the ledger already is the
-- debit record, so the balance can never drift from what was spent.
--
--   org_wallet_credits  append-only money in, one row per verified payment (payment_ref is unique, so a repeated webhook
--                       credits once). amount_usd is what the ledger is denominated in; amount_paise is what was paid.
--   billing_orders      a Razorpay order we created, so a webhook can be matched to an organization we know about instead
--                       of trusting a field the payer could influence.
--   org_policies.wallet_required  when TRUE a call is refused unless balance >= the call's worst case.
--
-- Additive and idempotent. Nothing existing changes behaviour unless wallet_required is set for an organization.

ALTER TABLE org_policies ADD COLUMN IF NOT EXISTS wallet_required BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS org_wallet_credits (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    payment_ref      TEXT NOT NULL,                       -- the gateway's payment id; the idempotency key
    source           TEXT NOT NULL DEFAULT 'razorpay' CHECK (source IN ('razorpay', 'manual', 'signup_bonus')),
    amount_paise     BIGINT NOT NULL CHECK (amount_paise >= 0),
    amount_usd       NUMERIC(14, 6) NOT NULL CHECK (amount_usd > 0),
    inr_per_usd      NUMERIC(10, 4),
    margin           NUMERIC(6, 4),
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_credit_ref ON org_wallet_credits (payment_ref);
CREATE INDEX IF NOT EXISTS idx_wallet_credit_org ON org_wallet_credits (organization_id, created_at DESC);

CREATE TABLE IF NOT EXISTS billing_orders (
    order_id         TEXT PRIMARY KEY,                    -- the gateway's order id
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    amount_paise     BIGINT NOT NULL CHECK (amount_paise > 0),
    currency         TEXT NOT NULL DEFAULT 'INR',
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_billing_orders_org ON billing_orders (organization_id, created_at DESC);

-- money in is never edited or removed (a refund is a negative event of its own, added when refunds are built)
CREATE OR REPLACE FUNCTION tg_wallet_credits_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'org_wallet_credits is append-only' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
DROP TRIGGER IF EXISTS tg_wallet_credits_guard ON org_wallet_credits;
CREATE TRIGGER tg_wallet_credits_guard BEFORE UPDATE OR DELETE ON org_wallet_credits
    FOR EACH ROW EXECUTE FUNCTION tg_wallet_credits_guard();

ALTER TABLE org_wallet_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_wallet_credits FORCE ROW LEVEL SECURITY;
-- billing_orders is deliberately NOT row-secured: a payment webhook arrives with no tenant, and the only thing it reads
-- is the organization we recorded for a gateway order id.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'org_wallet_credits' AND policyname = 'org_strict') THEN
        CREATE POLICY org_strict ON org_wallet_credits FOR ALL USING (sl_org_strict(organization_id))
            WITH CHECK (sl_org_strict(organization_id));
    END IF;
END $$;

-- a refused call because the balance is too low is recorded like any other refusal
ALTER TABLE org_denials DROP CONSTRAINT IF EXISTS org_denials_reason_code_check;
ALTER TABLE org_denials ADD CONSTRAINT org_denials_reason_code_check CHECK (reason_code IN (
    'kill_switch', 'no_policy', 'provider_not_allowed', 'model_not_allowed', 'tool_not_allowed',
    'data_class_not_allowed', 'unpriced_unit', 'monthly_budget_zero', 'user_daily_budget_zero',
    'monthly_budget_exceeded', 'user_daily_budget_exceeded', 'wallet_empty'));
