-- 215_referral_and_refund_fixup.sql
--
-- Next free number: 215 (this file). Before it the highest was 214 (org_team_admin).
--
-- Why this file exists instead of fixes in 212 and 213.
--
--   212_referral_rewards.sql was written and APPLIED as 200_referral_rewards.sql (the number 200 was already taken by
--   200_goal_tool_rankings.sql). The rename to 212 came after it had been applied, and so did a later edit that added
--   reward_inr. Its bytes therefore no longer match the checksum the ledger recorded, and migrate.py -- correctly --
--   refuses to treat it as applied. A migration is immutable once applied; the repair is a NEW migration, not an edit
--   to the old one. 212 stays on disk, unchanged, as the record of what that first version did.
--
--   213_wallet_refunds.sql was written but never applied, and by the time it would run, 214 was already applied: a
--   file numbered below an applied migration must not be introduced. Its contents are carried here instead, and the
--   213 file is removed so there is exactly one place a reader can find them.
--
-- So this file is the whole delta between what the live database has and what services/referral_rewards.py and
-- services/wallet_refunds.py need, in one idempotent migration that sorts after everything applied:
--
--   1. referral_rewards.reward_inr          the reward as it was promised, in whole rupees (the service writes it)
--   2. org_wallet_credits may hold a NEGATIVE row
--                                           a reversal or a refund is a row with the opposite sign -- an append-only
--                                           ledger says "take this back" by appending, never by editing or deleting.
--                                           The two CHECKs from db/156 only allowed money in; they now allow the sign
--                                           to flip, while still allowing the existing 0-paise grants and refusing a
--                                           row whose rupees and dollars disagree.
--   3. org_wallet_credits.source gains 'refund'
--   4. wallet_refunds                       one row per refund the gateway reports (Razorpay refund.created /
--                                           refund.processed), keyed on the gateway's refund id so a redelivery applies
--                                           once. account_id is the buyer's OWN wallet, read from our credit row for
--                                           that payment, never from the webhook body.
--   5. an index for the repair run           referral_rewards (status, created_at): services/referral_rewards.
--                                           release_pending() looks for a 'pending' row, or a 'released' row whose
--                                           wallet credit is missing.
--
-- Nothing here changes an existing row: the table is empty at this point, every wallet row already stored is positive
-- and keeps its meaning, and the wider CHECKs are met by every row that met the narrower ones.
--
-- Additive and idempotent.

-- ---- 1. what the reward was, in rupees ------------------------------------------------------------
ALTER TABLE referral_rewards ADD COLUMN IF NOT EXISTS reward_inr BIGINT
    CHECK (reward_inr IS NULL OR reward_inr > 0);
-- Nullable on purpose: rows written before this column existed have 0 rows behind them (the table is empty at the
-- migration), and a nullable column keeps the ADD COLUMN cheap if that is ever untrue on another deployment.
-- services/referral_rewards.py always writes it.

-- ---- 2. a reversal is a row with the opposite sign ------------------------------------------------
-- The db/156 CHECKs were written when the wallet only ever held money in. A reversal or refund appends a row of the
-- opposite sign, so they must allow it. They must NOT become "non-zero", though: many existing rows legitimately pay
-- 0 paise and carry their whole value in credit-dollars -- plan-included usage ('plan_included'), a signup bonus
-- ('signup_bonus'), a manual grant ('manual'). So the rule is about the SIGN, not about zero:
--
--   * paise may be zero (a grant of usage, not of rupees) or either sign (a credit or a reversal);
--   * the two currencies must agree in sign, so one row can never mean "money in" in rupees and "money out" in
--     dollars. amount_usd stays non-zero, as it always was: it is the unit the balance and the spend checks use, and a
--     row that moves no usage at all is a bug in a caller, not a credit.
ALTER TABLE org_wallet_credits DROP CONSTRAINT IF EXISTS org_wallet_credits_amount_paise_check;
ALTER TABLE org_wallet_credits ADD CONSTRAINT org_wallet_credits_amount_paise_check
    CHECK (amount_paise = 0 OR amount_usd = 0 OR (amount_paise > 0) = (amount_usd > 0));

ALTER TABLE org_wallet_credits DROP CONSTRAINT IF EXISTS org_wallet_credits_amount_usd_check;
ALTER TABLE org_wallet_credits ADD CONSTRAINT org_wallet_credits_amount_usd_check
    CHECK (amount_usd <> 0);

-- ---- 3. where the money went ----------------------------------------------------------------------
ALTER TABLE org_wallet_credits DROP CONSTRAINT IF EXISTS org_wallet_credits_source_check;
ALTER TABLE org_wallet_credits ADD CONSTRAINT org_wallet_credits_source_check
    CHECK (source IN ('razorpay', 'manual', 'signup_bonus', 'plan_included', 'referral_reward', 'refund'));

-- ---- 4. one row per refund the gateway reports -----------------------------------------------------
CREATE TABLE IF NOT EXISTS wallet_refunds (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    refund_id         TEXT NOT NULL,                  -- the gateway's refund id (rfnd_...): the idempotency key
    payment_id        TEXT NOT NULL,                  -- the payment being refunded (pay_...)
    order_id          TEXT,                           -- the order it belonged to, when known
    account_id        UUID NOT NULL,                  -- whose wallet: the buyer's own, from our own credit row
    amount_paise      BIGINT NOT NULL CHECK (amount_paise > 0),
    applied_paise     BIGINT NOT NULL CHECK (applied_paise >= 0),   -- what was actually debited, never more than it
    currency          TEXT NOT NULL DEFAULT 'INR',
    status            TEXT NOT NULL DEFAULT 'applied'
                      CHECK (status IN ('applied', 'skipped', 'failed')),
    reason            TEXT,                           -- why skipped/failed, in words a support person can read
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The column the 213 version did not have: a refund may be applied for less than it asked for (a partly spent wallet),
-- so the row records both the refund and the debit. Added separately for a deployment that already has the table.
ALTER TABLE wallet_refunds ADD COLUMN IF NOT EXISTS applied_paise BIGINT;
UPDATE wallet_refunds SET applied_paise = amount_paise WHERE applied_paise IS NULL;
ALTER TABLE wallet_refunds ALTER COLUMN applied_paise SET DEFAULT 0;
ALTER TABLE wallet_refunds ALTER COLUMN applied_paise SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_refund_id ON wallet_refunds (refund_id);
CREATE INDEX IF NOT EXISTS idx_wallet_refund_payment ON wallet_refunds (payment_id);
CREATE INDEX IF NOT EXISTS idx_wallet_refund_account ON wallet_refunds (account_id, created_at DESC);

-- Refunds arrive from the payment webhook (no tenant) and are read by the signed-in person by user id, like
-- billing_orders and referral_rewards, so this table is deliberately NOT row-secured.

-- ---- 5. the repair run's index ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_referral_reward_status ON referral_rewards (status, created_at);
