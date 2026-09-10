-- Migration 55 (MCP hardening B34/B32): the verification ladder.
--
-- Next free number: 55 (54 is highest).
--
-- WHY A NEW TABLE, NOT A NEW "VerificationPlan" ENTITY: `procedures.
-- postconditions` (db/18) is ALREADY the durable definition of what a
-- Procedure must satisfy to be considered done -- confirmed live, it is
-- a plain array of human-readable statement strings today (2 of 2625
-- procedures even have any). A separate "VerificationPlan" row that
-- just re-copies those statements would be exactly the "second copy of
-- the same fact" CLAUDE.md rule 2 forbids. This table is the missing
-- durable RESULT of evaluating one criterion for one run -- never a
-- restatement of the criterion itself, which stays derived from
-- `procedures.postconditions` at read time
-- (`app/services/verification.py::derive_verification_plan`).
-- `execution_runs.verification_plan_id` (named in B3's own field list)
-- is deliberately STILL not added -- there is still no separate "plan"
-- entity to point at; the plan is (procedure_id, procedure_version)
-- itself, already reachable from execution_runs.
--
-- criterion_id is not a UUID -- there is no durable Criterion row to
-- reference, it is a stable derived string
-- (`f"postcondition:{index}"`, `derive_verification_plan`'s own
-- convention) identifying WHICH element of `postconditions` this result
-- concerns, for a specific (procedure_id, procedure_version).
--
-- THE CORE INVARIANT (B34's own words, enforced in the SERVICE layer,
-- not just documented): "The host cannot directly write the strongest
-- state. It is derived from evidence." There is no UPDATE path in
-- app/services/verification.py that accepts `state` as a caller-supplied
-- value -- each evidence-class-specific record_* function computes the
-- resulting state itself from what that evidence class can actually
-- support (self-report can only ever reach CLAIMED_DONE/FAILED_
-- VERIFICATION, never VERIFIED or stronger).
--
-- Discipline matches mig 24/50/53: TEXT + CHECK, DO $$-guarded
-- constraints, additive + idempotent.

CREATE TABLE IF NOT EXISTS verification_results (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_run_id     UUID NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
    criterion_id         TEXT NOT NULL,
    statement            TEXT NOT NULL,
    method               TEXT NOT NULL,
    required             BOOLEAN NOT NULL DEFAULT TRUE,
    state                TEXT NOT NULL,
    evidence_refs        JSONB NOT NULL DEFAULT '[]',
    -- HUMAN_REVIEW-only fields (B34: "do not accept approved=true
    -- without reviewer identity, reviewed targets, criterion answers,
    -- timestamp, Evidence linkage"). NULL for every other method.
    reviewer             TEXT,
    reviewed_targets     JSONB NOT NULL DEFAULT '[]',
    criterion_answers    JSONB NOT NULL DEFAULT '{}',
    detail               TEXT,
    created_by           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='verification_results_method_chk') THEN
        ALTER TABLE verification_results ADD CONSTRAINT verification_results_method_chk
            CHECK (method IN (
                'self_report','artifact_inspection','deterministic_check',
                'independent_agent','human_review','real_world_outcome'
            ));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='verification_results_state_chk') THEN
        ALTER TABLE verification_results ADD CONSTRAINT verification_results_state_chk
            CHECK (state IN (
                'claimed_done','checked','verified','independently_verified',
                'failed_verification','inconclusive'
            ));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='verification_results_human_review_chk') THEN
        ALTER TABLE verification_results ADD CONSTRAINT verification_results_human_review_chk
            CHECK (method != 'human_review' OR (reviewer IS NOT NULL AND reviewed_targets != '[]'));
    END IF;
END $$;

-- One current result per (run, criterion) -- a re-evaluation UPDATEs
-- the same row (see verification.py's "insert or replace" contract)
-- rather than accumulating a history table; evidence_refs is the audit
-- trail, not a second row per attempt.
CREATE UNIQUE INDEX IF NOT EXISTS idx_verification_results_run_criterion
    ON verification_results(execution_run_id, criterion_id);

-- Reuses migration 36's generic updated_at trigger function (`sl_touch_
-- updated_at_generic`) -- it is already fully table-agnostic (just sets
-- NEW.updated_at = now()), so a second copy of it here would be exactly
-- the "second mechanism" CLAUDE.md rule 2 forbids.
DROP TRIGGER IF EXISTS trg_verification_results_touch ON verification_results;
CREATE TRIGGER trg_verification_results_touch
    BEFORE UPDATE ON verification_results
    FOR EACH ROW EXECUTE FUNCTION sl_touch_updated_at_generic();
