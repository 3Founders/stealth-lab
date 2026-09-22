-- Migration 103: trust-boundary + economic-integrity hardening for the
-- contribution/verification/ranking/Credits system (migration 102),
-- following the implementation audit's findings.
--
-- This migration adds ONLY what the DB layer must own: idempotency
-- constraints that make double-reward structurally impossible, not just
-- application-logic-avoidable. Everything else in the hardening pass
-- (server-derived identity, evidence/run relationship verification,
-- atomic cap enforcement via advisory locks, authorization on reads,
-- rate limiting) is application code -- see app/api/economy.py,
-- app/economy/{submissions,verification,credits}.py.
--
-- WHY THESE SPECIFIC CONSTRAINTS:
--
-- 1. procedure_usage_events: a given piece of real evidence, or a given
--    execution run, must back AT MOST ONE usage event. Without this, the
--    same evidence_id/execution_run_id could be attached to multiple
--    usage events (Part 11's "must not be silently reused across
--    unrelated Runs"), each independently reward-eligible.
--
-- 2. credit_ledger_events, submission-triggered rewards
--    (new_procedure/improvement): each submission has exactly ONE
--    recipient (its own submitted_by) and must be rewarded at most once
--    per reason. `UNIQUE (submission_id, reason)` is therefore a plain,
--    non-partial-on-recipient constraint.
--
-- 3. credit_ledger_events, reuse-triggered rewards (verified_reuse): a
--    single usage event legitimately produces UP TO TWO reward rows by
--    design (the current contributor, plus a reduced-share single-hop
--    parent attribution -- see app/economy/credits.py::reward_verified_reuse)
--    -- so the constraint cannot be bare (usage_event_id, reason). It
--    must include contributor_id: at most one reward per (usage event,
--    reason, recipient) triple. This still makes the exact failure mode
--    the audit named impossible (the SAME contributor being paid twice
--    for the SAME usage event) while preserving the documented two-
--    recipient attribution design -- not silently collapsing it.
--
-- Both credit_ledger_events constraints are partial (WHERE ... IS NOT
-- NULL) because admin_adjustment/clawback rows legitimately have no
-- submission_id/usage_event_id at all and must not be constrained by
-- either index.
--
-- Idempotent: IF NOT EXISTS throughout, matching every migration since 18/33/35.
--
-- Next free migration number: 104.

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'uq_procedure_usage_events_evidence'
    ) THEN
        CREATE UNIQUE INDEX uq_procedure_usage_events_evidence
            ON procedure_usage_events(evidence_id) WHERE evidence_id IS NOT NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'uq_procedure_usage_events_execution_run'
    ) THEN
        CREATE UNIQUE INDEX uq_procedure_usage_events_execution_run
            ON procedure_usage_events(execution_run_id) WHERE execution_run_id IS NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'uq_credit_ledger_events_submission_reason'
    ) THEN
        CREATE UNIQUE INDEX uq_credit_ledger_events_submission_reason
            ON credit_ledger_events(submission_id, reason) WHERE submission_id IS NOT NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'uq_credit_ledger_events_usage_reason_contributor'
    ) THEN
        CREATE UNIQUE INDEX uq_credit_ledger_events_usage_reason_contributor
            ON credit_ledger_events(usage_event_id, reason, contributor_id) WHERE usage_event_id IS NOT NULL;
    END IF;

    -- An event can be clawed back at most once -- a second clawback attempt
    -- against the same original event is a retry, not a second reversal.
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'uq_credit_ledger_events_reversal_of'
    ) THEN
        CREATE UNIQUE INDEX uq_credit_ledger_events_reversal_of
            ON credit_ledger_events(reversal_of_event_id) WHERE reversal_of_event_id IS NOT NULL;
    END IF;
END $$;

-- Next free migration number: 104.
