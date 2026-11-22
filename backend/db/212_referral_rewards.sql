-- 212_referral_rewards.sql
--
-- Next free number: 212 (this file). Before it the highest was 211 (org_member_lifecycle); 200 (goal_tool_rankings) was
-- taken, which is why this one is numbered here.
--
-- What this adds: a referral that pays the person who shared a code, once the person they referred has PAID.
-- Until now a personal code only unlocked the one-person Pilot for whoever used it (db/167, services/referrals.py);
-- the referrer got nothing, and "rewards are NOT built" was the written state. This is that reward, in the shape the
-- legal briefing (docs/referral_legality_2026-11.md) already says is the low-risk one:
--
--   * ONE level only. No reward for the referrer's referrer, no tiers, no rank language.
--   * Paid on a COMPLETED PAID SALE, never on a signup. The trigger is the payment webhook, after the order was
--     paid for -- not when someone applies a code on the pricing page.
--   * Credit only. It is usage credit in the referrer's own prepaid wallet, non-transferable and never cash, so it
--     stays inside the closed loop the RBI note in the briefing describes.
--
-- INDIVIDUAL ACCOUNTS ONLY. The credit is always added to the referrer's OWN wallet account -- the account whose id
-- is the person's user id (services/accounts.py). A team or organization order can carry a code for attribution, but
-- it never pays into an organization wallet and never pays from one: this table has no organization column at all, so
-- there is nothing for a team order to write to.
--
--   referral_rewards   one row per referred buyer. The unique index on referred_user_id is the first defence: an
--                      account can be referred ONCE, ever, whoever the code belongs to. order_id and payment_id are
--                      both unique, which is the idempotency the payment webhook needs -- Razorpay redelivers, and a
--                      redelivery must find the row already there rather than pay twice.
--
--                      status walks one way:
--                        released   the reward was added to the referrer's wallet, in the SAME transaction that wrote
--                                   this row (org_wallet_credits, source 'referral_reward', payment_ref
--                                   'referral:<id>'). Release is automatic: paid in, paid out, one commit.
--                        pending    only ever seen on a row written by an older deployment or left by a crash; the
--                                   repair run (services/referral_rewards.release_pending) finishes it. New referrals
--                                   never stop here.
--                        void       refused: self-referral, a founder/issued code, or the 30-a-year allowance of
--                                   that referrer was already full
--                        reversed   a refund or chargeback took the payment back (the credit row is closed by a
--                                   negative one, see services/referral_rewards.py)
--                      A row is never deleted and never rewritten back down the scale.
--
-- Additive and idempotent. Nothing existing changes behaviour: an order without a code writes no row here, and the
-- reward is off only if KEL_REFERRAL_REWARD_INR is set to 0. The default is on, at Rs 100.

CREATE TABLE IF NOT EXISTS referral_rewards (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code              TEXT NOT NULL,                    -- the code as recorded on the order (billing_orders.referral_code)
    referrer_user_id  UUID NOT NULL,                    -- the owner of the personal code; their own wallet is credited
    referred_user_id  UUID NOT NULL,                    -- the buyer who paid with it
    order_id          TEXT NOT NULL,
    payment_id        TEXT NOT NULL,
    amount_paise      BIGINT NOT NULL CHECK (amount_paise > 0),  -- what the referred person actually paid
    reward_inr        BIGINT NOT NULL CHECK (reward_inr > 0),    -- the reward promised, in whole rupees (what was promised)
    reward_usd        NUMERIC(14, 6) NOT NULL CHECK (reward_usd > 0),  -- those rupees at the rate the day it was earned
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'released', 'void', 'reversed')),
    reason            TEXT,                             -- why a void/reversed row is what it is, in words
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at       TIMESTAMPTZ,
    -- Nobody refers themselves. This is a database error, not a policy the application has to remember.
    CONSTRAINT referral_rewards_no_self CHECK (referrer_user_id <> referred_user_id)
);

-- An account is referred once, forever. This is what makes a second code on the same buyer harmless.
CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_reward_referred ON referral_rewards (referred_user_id);

-- The webhook's idempotency: one payment, one order, one row. A redelivery hits this and finds the row it made.
CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_reward_order ON referral_rewards (order_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_reward_payment ON referral_rewards (payment_id);

CREATE INDEX IF NOT EXISTS idx_referral_reward_referrer ON referral_rewards (referrer_user_id, created_at DESC);

-- The 30-per-account allowance, counted on released (paid) referrals only.
CREATE INDEX IF NOT EXISTS idx_referral_reward_released ON referral_rewards (referrer_user_id)
    WHERE status = 'released';

-- The repair run (release_pending) looks for pending rows, and for released rows with no wallet credit; this index
-- keeps both cheap as the table grows.
CREATE INDEX IF NOT EXISTS idx_referral_reward_status ON referral_rewards (status, created_at);

-- The wallet credit a referral pays is a source of its own, so a balance history says where the money came from.
-- (The constraint was last replaced in db/164; repeat its full list with the new member added.)
ALTER TABLE org_wallet_credits DROP CONSTRAINT IF EXISTS org_wallet_credits_source_check;
ALTER TABLE org_wallet_credits ADD CONSTRAINT org_wallet_credits_source_check
    CHECK (source IN ('razorpay', 'manual', 'signup_bonus', 'plan_included', 'referral_reward'));

-- Referrals are written by the payment webhook (no tenant) and read by the signed-in referrer by user id, like
-- billing_orders and free_credit_redemptions, so this table is deliberately NOT row-secured.
