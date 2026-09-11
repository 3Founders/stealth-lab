-- Migration 77 (B4 STRICT CLOSURE): execution_runs.final_outcome must
-- imply a terminal status, enforced by the DB itself.
--
-- B4's own text: "Invalid transitions MUST fail closed with a typed
-- error; they must not be silently coerced to the nearest valid
-- state." stealth_execution_contract.py's own docstring claims the
-- OUTCOME chain step is "already true of every step" -- real for
-- every EXISTING application-level write site (durable_run.py's three
-- real UPDATE statements all set status and final_outcome together,
-- in the same statement), but that claim was never actually enforced
-- by the schema itself: migration 36's execution_runs_final_outcome_
-- chk only constrains the VALUE of final_outcome ('success'/'failure'/
-- 'needs_rework'/NULL), not its relationship to status. A stray or
-- future write setting final_outcome on a non-terminal run would be
-- silently accepted -- exactly the "silently coerced" failure mode B4
-- forbids, just never exercised.
--
-- Mirrors the existing one-directional
-- execution_runs_final_exec_implies_terminal_chk (migration 37) --
-- same real invariant, applied to final_outcome instead of
-- final_execution_id.
--
-- Additive + idempotent: drop-if-exists then add, no data migration
-- needed (every existing real row already satisfies this -- confirmed
-- by grep of every write site).
--
-- Next free number: 77 (76 is highest).

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'execution_runs_final_outcome_implies_terminal_chk'
    ) THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_final_outcome_implies_terminal_chk
            CHECK (final_outcome IS NULL OR status IN ('succeeded', 'failed'));
    END IF;
END $$;
