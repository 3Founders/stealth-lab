-- 218: the rupee reward column and its index, moved out of the already-applied 212.
--
-- 212_referral_rewards.sql was edited after it had been applied (commit be5394c9 added
-- reward_inr and an index). migrate.py correctly refuses to start on a checksum mismatch, so
-- the deployed container could not boot. 212 is restored to the applied version; this file
-- carries the schema change forward, additively and idempotently.
--
-- referral_rewards has no rows at the time of writing; the UPDATE is here so a deployment
-- that does have old rows can still add the NOT NULL column (the old reward_usd is converted
-- at the approximate rate the wallet uses, never left NULL).

ALTER TABLE referral_rewards ADD COLUMN IF NOT EXISTS reward_inr BIGINT;

UPDATE referral_rewards
   SET reward_inr = GREATEST(1, ROUND(reward_usd * 83))::bigint
 WHERE reward_inr IS NULL;

ALTER TABLE referral_rewards ALTER COLUMN reward_inr SET NOT NULL;

ALTER TABLE referral_rewards DROP CONSTRAINT IF EXISTS referral_rewards_reward_inr_check;
ALTER TABLE referral_rewards ADD CONSTRAINT referral_rewards_reward_inr_check CHECK (reward_inr > 0);

-- The repair run (services/referral_rewards.release_pending) looks for pending rows, and for
-- released rows with no wallet credit; this index keeps both cheap as the table grows.
CREATE INDEX IF NOT EXISTS idx_referral_reward_status ON referral_rewards (status, created_at);
