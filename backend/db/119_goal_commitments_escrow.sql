-- Migration 119: Credit commitments to Goals -- escrow bounty. Next free number: 120.
--
-- Model (owner decision): committing Credits to a Goal LOCKS them (a negative,
-- append-only ledger row, so the committer's balance drops). When the Goal is
-- resolved (the existing verification rule sets goals.resolved_at) the locked
-- Credits are PAID to the contributor of the verifying Procedure; a committer who
-- would pay themselves, or a Procedure with no human contributor, gets them back.
-- Before resolution the committer may withdraw (refund). Each commitment is settled
-- exactly once. Credits never decide resolution, verification or applicability:
-- payout happens after resolution, never before, and nothing reads the ledger to
-- resolve anything.
--
-- Ledger rows (credit_ledger_events stays append-only):
--   goal_commitment          amount < 0  committer   goal_id required
--   goal_commitment_release  amount > 0  committer   reversal_of_event_id = the commitment
--   bounty_payout            amount > 0  solver      reversal_of_event_id = the commitment
-- Control database (A). Idempotent.

ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_reason_chk;
ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_reason_chk
    CHECK (reason IN ('new_procedure', 'improvement', 'verified_reuse', 'clawback', 'admin_adjustment',
                      'goal_commitment', 'goal_commitment_release', 'bounty_payout'));

ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_amount_sign_chk;
ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_amount_sign_chk
    CHECK (
        (reason IN ('new_procedure', 'improvement', 'verified_reuse') AND amount > 0)
        OR (reason = 'clawback' AND amount < 0)
        OR (reason = 'admin_adjustment')
        OR (reason = 'goal_commitment' AND amount < 0)
        OR (reason IN ('goal_commitment_release', 'bounty_payout') AND amount > 0)
    );

ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_clawback_requires_reversal_chk;
ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_clawback_requires_reversal_chk
    CHECK ((reason IN ('clawback', 'goal_commitment_release', 'bounty_payout')) = (reversal_of_event_id IS NOT NULL));

ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_commitment_goal_chk;
ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_commitment_goal_chk
    CHECK (reason NOT IN ('goal_commitment', 'goal_commitment_release', 'bounty_payout') OR goal_id IS NOT NULL);

-- a commitment is settled (released OR paid out) exactly once
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_commitment_settled_once
    ON credit_ledger_events (reversal_of_event_id) WHERE reason IN ('goal_commitment_release', 'bounty_payout');
-- a retried commit request with the same key is the same commitment
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_commitment_idempotency
    ON credit_ledger_events (contributor_id, (metadata ->> 'idempotency_key'))
    WHERE reason = 'goal_commitment' AND metadata ? 'idempotency_key';
CREATE INDEX IF NOT EXISTS idx_credit_commitment_goal
    ON credit_ledger_events (goal_id) WHERE reason = 'goal_commitment';

-- A settlement must return exactly the committed amount, for the same Goal, and a
-- release goes back to the committer.
CREATE OR REPLACE FUNCTION sl_credit_settlement_check() RETURNS trigger AS $$
DECLARE c RECORD;
BEGIN
    IF NEW.reason NOT IN ('goal_commitment_release', 'bounty_payout') THEN
        RETURN NEW;
    END IF;
    SELECT reason, amount, goal_id, contributor_id INTO c FROM credit_ledger_events WHERE id = NEW.reversal_of_event_id;
    IF c.reason IS DISTINCT FROM 'goal_commitment' THEN
        RAISE EXCEPTION 'a % must settle a goal_commitment', NEW.reason USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.amount <> -c.amount OR NEW.goal_id IS DISTINCT FROM c.goal_id THEN
        RAISE EXCEPTION 'settlement must return exactly the committed amount for the same goal'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.reason = 'goal_commitment_release' AND NEW.contributor_id <> c.contributor_id THEN
        RAISE EXCEPTION 'a release returns Credits to the committer only' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS tg_credit_settlement_check ON credit_ledger_events;
CREATE TRIGGER tg_credit_settlement_check BEFORE INSERT ON credit_ledger_events
    FOR EACH ROW EXECUTE FUNCTION sl_credit_settlement_check();

-- Goals can be homed on any shard: the physical FK is replaced by the route-aware
-- check every other cross-database reference uses (migration 96).
ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_goal_id_fkey;
DROP TRIGGER IF EXISTS tg_ref_credit_ledger_events_goal_id ON credit_ledger_events;
CREATE TRIGGER tg_ref_credit_ledger_events_goal_id
    BEFORE INSERT OR UPDATE OF goal_id ON credit_ledger_events
    FOR EACH ROW EXECUTE FUNCTION sl_check_ref('goal_id', 'goal', '');

-- Read model: every commitment with its settlement (open = settlement IS NULL).
CREATE OR REPLACE VIEW goal_commitments AS
SELECT c.id, c.contributor_id, c.goal_id, -c.amount AS credits, c.created_at,
       s.reason AS settlement, s.contributor_id AS settled_to, s.created_at AS settled_at
  FROM credit_ledger_events c
  LEFT JOIN credit_ledger_events s
         ON s.reversal_of_event_id = c.id AND s.reason IN ('goal_commitment_release', 'bounty_payout')
 WHERE c.reason = 'goal_commitment';
