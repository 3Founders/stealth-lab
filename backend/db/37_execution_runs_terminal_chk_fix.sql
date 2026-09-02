-- Migration 37 (final-V1 hardening §24): relax execution_runs_terminal_chk.
--
-- Next free number: 37 (36 is highest).
--
-- Migration 36 wrote:
--   CHECK ((status IN ('succeeded','failed')) = (final_execution_id IS NOT NULL))
-- which forces a terminal run to ALWAYS carry an appended `executions`
-- row. But appending that row needs a CompiledPlan the caller may not
-- have (the durable-run layer is usable standalone, and record_plan_
-- execution is only called when `compiled` is supplied). The real
-- invariant is one-directional: if `final_execution_id` is set, the run
-- must be terminal -- not the reverse.
--
-- Additive + idempotent: drop the old constraint if present, add the
-- corrected one. No data migration (execution_runs is new in 36 and has
-- no rows that would violate the looser rule).

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='execution_runs_terminal_chk') THEN
        ALTER TABLE execution_runs DROP CONSTRAINT execution_runs_terminal_chk;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='execution_runs_final_exec_implies_terminal_chk') THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_final_exec_implies_terminal_chk
            CHECK (final_execution_id IS NULL OR status IN ('succeeded','failed'));
    END IF;
END $$;
