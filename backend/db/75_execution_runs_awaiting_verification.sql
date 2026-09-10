-- Migration 75 (B4/B16/B33 strict closure): a real, additive terminal-
-- adjacent status -- 'awaiting_verification' -- and the DB-enforced
-- transition edges around it.
--
-- WHY THIS IS THE FIX, not a new competing ontology: B16 requires
-- "Successful execution without persisted terminal reporting is a
-- system failure" and B33 requires "refusing to mark the Procedure
-- complete until required verification is satisfied" -- both literal
-- MUSTs. Before this migration, `durable_run.py::_finalize` computed
-- `status='succeeded'` from node statuses ALONE, with no reference to
-- verification/evidence at all -- `evaluate_run_completion`'s own real,
-- honest `procedure_run_complete` answer (B16/B33's earlier real work)
-- was a COMPUTED ANSWER a caller could consult, never an ENFORCED gate
-- on the real terminal transition itself.
--
-- The one real, DELIBERATE change this requires: the existing coarse
-- state machine (migration 36, guarded by migration 60's transition
-- fence) already IS "the state machine" -- CLAUDE.md rule 2 forbids a
-- second one. So this is NOT a second ontology: it is ONE additive
-- value on the SAME column, plus the same transition-fence trigger
-- extended with the real edges around it. A run whose nodes have all
-- succeeded but whose Procedure has real, required, not-yet-satisfied
-- verification criteria now stops at 'awaiting_verification' instead of
-- silently becoming 'succeeded' -- the honest, literal reflection of
-- B4's own named VERIFICATION stage sitting between EXECUTION_EVENTS
-- and OUTCOME. `verify_completion` (already the real, only caller-
-- facing verification-advancement path) is the ONLY thing that can move
-- a run out of 'awaiting_verification', once real evidence makes
-- `procedure_run_complete` literally true.
--
-- Next free number: 75 (74 was highest before this file).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'execution_runs_status_chk'
    ) THEN
        RAISE EXCEPTION 'execution_runs_status_chk not found -- migration order issue';
    END IF;
    ALTER TABLE execution_runs DROP CONSTRAINT execution_runs_status_chk;
    ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_status_chk
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'paused', 'cancelled', 'awaiting_verification'));
END $$;

CREATE OR REPLACE FUNCTION sl_execution_runs_status_transition_fence()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running', 'cancelled')) OR
            (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'paused', 'cancelled', 'awaiting_verification')) OR
            (OLD.status = 'paused'  AND NEW.status IN ('running', 'cancelled')) OR
            (OLD.status = 'failed'  AND NEW.status IN ('running', 'cancelled')) OR
            -- B16/B33: the ONLY two real outgoing edges from
            -- 'awaiting_verification' -- verify_completion advancing it
            -- once real evidence satisfies every required criterion, or
            -- a caller explicitly cancelling a run stuck waiting on
            -- verification that will never arrive (e.g. an abandoned
            -- human-review request). No edge back to 'running': once
            -- every node has genuinely succeeded, there is nothing left
            -- to run -- only verification remains.
            (OLD.status = 'awaiting_verification' AND NEW.status IN ('succeeded', 'failed', 'cancelled'))
            -- 'succeeded' and 'cancelled' are terminal: no outgoing edge.
        ) THEN
            RAISE EXCEPTION
                'invalid execution_runs status transition: % -> % (run %)',
                OLD.status, NEW.status, OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
