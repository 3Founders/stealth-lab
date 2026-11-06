-- 184_team_trial_codes.sql
--
-- Next free number: 183 (free_credit_codes) was highest before this file.
--
-- What this adds: a free-trial code can put the person in a TEAM (an organization with its own wallet, policy and admin pages) instead of
-- crediting their own wallet, and can require a verified phone number, one person per number.
--
--   billing_referral_codes.grant_org_id   when set, redeeming makes the person a `member` of this organization (its wallet pays for their
--                                         use under the organization's policy: the per-person daily cap and monthly budget live in org_policies)
--   billing_referral_codes.require_phone  when TRUE, redeeming needs a verified phone number on the person's sign-in (Supabase Auth phone
--                                         confirmation); without one the server answers "verify your phone number first"
--   free_credit_redemptions.phone_hash    a keyed hash of the verified number (never the number itself): the same number cannot redeem the
--                                         same code from a second account
-- Additive and idempotent.

ALTER TABLE billing_referral_codes ADD COLUMN IF NOT EXISTS grant_org_id UUID;
ALTER TABLE billing_referral_codes ADD COLUMN IF NOT EXISTS require_phone BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE free_credit_redemptions ADD COLUMN IF NOT EXISTS phone_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_free_credit_phone ON free_credit_redemptions (code, phone_hash) WHERE phone_hash IS NOT NULL;
