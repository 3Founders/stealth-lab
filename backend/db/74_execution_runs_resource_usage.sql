-- Migration 74 (B12): the three real resource-usage counters B12 names
-- but this codebase never tracked -- token budget, execution-cost
-- budget, tool budget (ancestor-chain, max depth, max child executions,
-- and wall-clock were already real, migration 51/52 + recursion_guard.py).
--
-- Accumulated PER RUN (this run's own real usage, atomically
-- incremented as it accrues -- never a guess), then SUMMED across the
-- ancestor chain (same root_run_id, recursion_guard.py::
-- check_recursion_limits) to enforce the actual budget -- the same
-- pattern already established for `max_child_executions` (COUNT(*)
-- WHERE root_run_id = ...).
--
-- Next free number: 75 (74 is highest).

ALTER TABLE execution_runs
    ADD COLUMN IF NOT EXISTS tokens_used INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tool_calls_used INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cost_usd_used NUMERIC(12, 6) NOT NULL DEFAULT 0;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'execution_runs_usage_nonneg_chk') THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_usage_nonneg_chk
            CHECK (tokens_used >= 0 AND tool_calls_used >= 0 AND cost_usd_used >= 0);
    END IF;
END $$;
