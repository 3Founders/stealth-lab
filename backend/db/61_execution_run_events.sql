-- Migration 61 (B7/B8): a durable, append-only event log for
-- execution_runs, plus the ExecutionRecorder facade that writes to it
-- (app/execution/recorder.py).
--
-- NOT a second scheduler/registry (§27/§33, CLAUDE.md rule 2): this
-- table records what durable_run.py's EXISTING transition points
-- already do to execution_runs/execution_run_nodes -- it never decides
-- anything, drives anything, or gates a transition. Every event type
-- below corresponds 1:1 to a real `UPDATE execution_run(_node)s SET
-- status=...` call site already present in durable_run.py before this
-- migration (confirmed by reading every one first, not guessed).
--
-- Reuses `sl_raise_frozen()` (migration 23) for append-only enforcement
-- rather than writing a new frozen-trigger function.
--
-- Next free number: 62 (61 is highest).

CREATE TABLE IF NOT EXISTS execution_run_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_run_id    UUID NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
    node_order          INTEGER,  -- NULL for a run-level event
    event_type          TEXT NOT NULL,
    payload             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'execution_run_events_type_chk') THEN
        ALTER TABLE execution_run_events ADD CONSTRAINT execution_run_events_type_chk
            CHECK (event_type IN (
                'run_created', 'run_claimed', 'run_paused', 'run_finalized',
                'route_decided',
                'node_claimed', 'node_succeeded', 'node_failed'
            ));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_execution_run_events_run
    ON execution_run_events(execution_run_id, created_at);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_execution_run_events_frozen') THEN
        CREATE TRIGGER tg_execution_run_events_frozen
            BEFORE UPDATE OR DELETE ON execution_run_events
            FOR EACH ROW EXECUTE FUNCTION sl_raise_frozen();
    END IF;
END $$;
