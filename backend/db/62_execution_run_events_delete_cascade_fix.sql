-- Migration 62: fix migration 61's append-only trigger on
-- execution_run_events -- it must forbid UPDATE, but NOT DELETE.
--
-- Confirmed live (this session's own recorder e2e tests): unlike
-- execution_plans/task_graphs/executions (true permanent testimony,
-- migration 23, correctly frozen against both UPDATE and DELETE forever),
-- execution_run_events is scoped to its execution_runs parent's own
-- lifecycle -- same as execution_run_nodes (migration 36), which is
-- `ON DELETE CASCADE` and was never frozen against DELETE. Tests (and
-- any real cleanup of a run) routinely DELETE execution_runs rows;
-- migration 61's DELETE-frozen child made that cascade fail with
-- "execution_run_events is append-only/frozen: DELETE rejected" instead
-- of removing the run's whole history together -- which is not "silently
-- rewriting history", it's deleting the run. What must never happen is
-- an event being REWRITTEN in place while its run still exists, which is
-- exactly what the UPDATE-only fence below still forbids.
--
-- Next free number: 63 (62 is highest).

DROP TRIGGER IF EXISTS tg_execution_run_events_frozen ON execution_run_events;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_execution_run_events_immutable') THEN
        CREATE TRIGGER tg_execution_run_events_immutable
            BEFORE UPDATE ON execution_run_events
            FOR EACH ROW EXECUTE FUNCTION sl_raise_frozen();
    END IF;
END $$;
