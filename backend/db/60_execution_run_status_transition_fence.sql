-- Migration 60 (B4): a real, DB-enforced status-transition guard on
-- execution_runs -- the missing half of "formal state machine with
-- typed transition guards."
--
-- NOT a second state machine: execution_runs.status (migration 36) and
-- its CHECK constraint already ARE the state machine ("pending ->
-- running -> (succeeded|failed|paused|cancelled)", migration 36's own
-- comment). What was missing is enforcement that only THOSE edges can
-- ever actually be taken -- until now, any UPDATE could jump status to
-- any other allowed value in one step (e.g. pending -> succeeded,
-- skipping running entirely) and the DB would not object; only
-- scattered `WHERE status IN (...)` clauses in durable_run.py enforced
-- this, application-side, per call site, not as a single named
-- mechanism. This trigger is that single mechanism, additive to the
-- existing table (no new table/registry -- CLAUDE.md rule: reuse
-- existing infrastructure).
--
-- Edges below are exactly the transitions durable_run.py's own SQL
-- already performs (confirmed by reading every `SET status` on
-- execution_runs in durable_run.py before writing this), plus
-- '-> cancelled' from every non-terminal state since 'cancelled' is
-- already a legal value per migration 36's CHECK constraint even though
-- no caller produces it yet (a future cancel path is not blocked).
--
-- Next free number: 61 (60 is highest).

CREATE OR REPLACE FUNCTION sl_execution_runs_status_transition_fence()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running', 'cancelled')) OR
            (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'paused', 'cancelled')) OR
            (OLD.status = 'paused'  AND NEW.status IN ('running', 'cancelled')) OR
            (OLD.status = 'failed'  AND NEW.status IN ('running', 'cancelled'))
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

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_execution_runs_status_transition_fence'
    ) THEN
        CREATE TRIGGER trg_execution_runs_status_transition_fence
            BEFORE UPDATE ON execution_runs
            FOR EACH ROW
            EXECUTE FUNCTION sl_execution_runs_status_transition_fence();
    END IF;
END $$;
