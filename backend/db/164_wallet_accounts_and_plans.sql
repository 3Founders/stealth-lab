-- 164: wallets owned by a person (no hidden organization), and the Pro / Pilot plans bought with them.
--
-- Why. A wallet, a spending policy and the provider-call ledger were all keyed by an organization id, so a person with no
-- company needed a hidden "personal organization" (services/personal_org.py) just to hold a balance. This migration lets
-- the same key be a USER id: the tenant id, the row-level-security predicate (sl_org_strict compares text) and every
-- query by organization_id keep working unchanged, because a user id and an organization id are both uuids that never
-- collide. What changes is only that those five tables stop requiring the id to be an organizations row; a trigger
-- requires it to be an organizations row OR a users row instead, so an unknown id is still refused.
--
--   * org_policies, provider_call_ledger, org_denials, org_wallet_credits, billing_orders: foreign key to organizations
--     dropped, replaced by the existence trigger below.
--   * org_wallet_credits.source gains 'plan_included' (the usage that comes with a plan; amount_paise 0).
--   * billing_orders learns what an order is for (a plan, its seats and team name, who bought it) so the webhook can
--     activate it from what WE recorded, never from the webhook body.
--   * plan_entitlements: append-only record of a plan bought (Pro 30 days, Pilot 14 days), one row per verified payment.
--
-- Additive and idempotent. Nothing existing changes behaviour: organizations keep working as before.

CREATE OR REPLACE FUNCTION sl_account_known(p_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (SELECT 1 FROM organizations WHERE id = p_id)
        OR EXISTS (SELECT 1 FROM users WHERE id = p_id);
$$;

CREATE OR REPLACE FUNCTION tg_account_known()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT sl_account_known(NEW.organization_id) THEN
        RAISE EXCEPTION 'organization_id % is neither an organization nor a user', NEW.organization_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE
    t TEXT;
    c TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['org_policies', 'provider_call_ledger', 'org_denials', 'org_wallet_credits', 'billing_orders']
    LOOP
        FOR c IN
            SELECT con.conname
              FROM pg_constraint con
              JOIN pg_class rel ON rel.oid = con.conrelid
             WHERE rel.relname = t AND con.contype = 'f'
               AND con.confrelid = 'organizations'::regclass
        LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', t, c);
        END LOOP;
        EXECUTE format('DROP TRIGGER IF EXISTS tg_account_known ON %I', t);
        EXECUTE format('CREATE TRIGGER tg_account_known BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION tg_account_known()', t);
    END LOOP;
END $$;

ALTER TABLE org_wallet_credits DROP CONSTRAINT IF EXISTS org_wallet_credits_source_check;
ALTER TABLE org_wallet_credits ADD CONSTRAINT org_wallet_credits_source_check
    CHECK (source IN ('razorpay', 'manual', 'signup_bonus', 'plan_included'));
-- amount_usd > 0 stays: a plan always includes some usage

ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS plan           TEXT CHECK (plan IS NULL OR plan IN ('pro', 'pilot'));
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS seats          INTEGER CHECK (seats IS NULL OR seats BETWEEN 1 AND 10);
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS team_name      TEXT;
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS buyer_user_id  UUID;
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS period_days    INTEGER;

CREATE TABLE IF NOT EXISTS plan_entitlements (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_user_id    UUID NOT NULL,
    account_id       UUID NOT NULL,                      -- the wallet that received the included usage: the buyer, or the new team
    plan             TEXT NOT NULL CHECK (plan IN ('pro', 'pilot')),
    seats            INTEGER NOT NULL CHECK (seats BETWEEN 1 AND 10),
    team_name        TEXT,
    starts_at        TIMESTAMPTZ NOT NULL,
    ends_at          TIMESTAMPTZ NOT NULL,
    payment_ref      TEXT NOT NULL,                      -- the gateway's payment id; the idempotency key
    order_id         TEXT,
    amount_paise     BIGINT NOT NULL CHECK (amount_paise > 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_entitlement_payment ON plan_entitlements (payment_ref);
CREATE INDEX IF NOT EXISTS idx_plan_entitlement_buyer ON plan_entitlements (buyer_user_id, ends_at DESC);

-- a plan once sold is never edited or removed; a renewal or a refund is a row of its own
CREATE OR REPLACE FUNCTION tg_plan_entitlements_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'plan_entitlements is append-only' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
DROP TRIGGER IF EXISTS tg_plan_entitlements_guard ON plan_entitlements;
CREATE TRIGGER tg_plan_entitlements_guard BEFORE UPDATE OR DELETE ON plan_entitlements
    FOR EACH ROW EXECUTE FUNCTION tg_plan_entitlements_guard();
-- Like billing_orders this is deliberately NOT row-secured: the payment webhook arrives with no tenant. Reads are by the
-- signed-in buyer's own user id, applied in the query.

-- one pilot per account, enforced by the database and not only by the order route
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_pilot_per_buyer ON plan_entitlements (buyer_user_id) WHERE plan = 'pilot';

-- Razorpay delivers at least once. A processed event id is remembered so a redelivery is acknowledged without work; the
-- payment_ref / order idempotency above remains the second layer.
CREATE TABLE IF NOT EXISTS billing_webhook_events (
    event_id      TEXT PRIMARY KEY,                     -- the x-razorpay-event-id header
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
