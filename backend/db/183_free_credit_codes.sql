-- 183_free_credit_codes.sql
--
-- Next free number: 182 (agent_links) was highest before this file.
--
-- What this adds: a code that gives a signed-in person a small amount of free usage credit with NO payment (a company trial), for a limited
-- number of people. The founders' first use: the code COMPANYTRIAL, $0.25 each, 10 people.
--
-- It is a property of a referral code, not a plan: the plan tables require a payment (amount_paise > 0, price > 0), and a free grant is a
-- different thing, so it does not touch them. A code with grant_usd set gives that credit to each person once; it does NOT unlock the paid
-- one-person plan (referrals.active_codes leaves it out).
--
--   billing_referral_codes.grant_usd   dollars of free usage per person (NULL: an ordinary referral code)
--   billing_referral_codes.max_uses    how many people may ever redeem it (NULL: no limit)
--   free_credit_redemptions            one row per person per code; the primary key is what stops a second redemption, and counting rows
--                                      is how the limit is held (the redeem path takes a per-code lock so two people cannot take the last slot)
--
-- The credit itself is a row in org_wallet_credits (source 'signup_bonus', 0 paise paid), so the balance, the ledger and the spend checks
-- are exactly those of paid credit. Append-only like that table. Idempotent.

ALTER TABLE billing_referral_codes ADD COLUMN IF NOT EXISTS grant_usd NUMERIC(14, 6)
    CHECK (grant_usd IS NULL OR (grant_usd > 0 AND grant_usd <= 100));
ALTER TABLE billing_referral_codes ADD COLUMN IF NOT EXISTS max_uses INTEGER
    CHECK (max_uses IS NULL OR max_uses > 0);

CREATE TABLE IF NOT EXISTS free_credit_redemptions (
    code        TEXT NOT NULL,
    user_id     UUID NOT NULL,
    amount_usd  NUMERIC(14, 6) NOT NULL CHECK (amount_usd > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, user_id)
);

CREATE OR REPLACE FUNCTION tg_free_credit_redemptions_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'free_credit_redemptions is append-only' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
DROP TRIGGER IF EXISTS tg_free_credit_redemptions_guard ON free_credit_redemptions;
CREATE TRIGGER tg_free_credit_redemptions_guard BEFORE UPDATE OR DELETE ON free_credit_redemptions
    FOR EACH ROW EXECUTE FUNCTION tg_free_credit_redemptions_guard();
-- Like billing_orders this is deliberately NOT row-secured: it is read and written by the server for the signed-in person, by user id in the query.
