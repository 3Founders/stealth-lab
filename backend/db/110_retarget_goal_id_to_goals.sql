-- Migration 110. Next free number: 111.
--
-- WHY THIS LANDS: `problems` (migration 35, "final-V1 hardening") is a
-- second, disconnected object with its own gen_random_uuid() identity --
-- zero FK relationship to `goals` (migration 83, the real canonical Goal
-- object). Nothing in the real ingestion pipeline (SKILL.md/GitHub/HTML/
-- PDF/DOCX -> goals/procedures/claims/evidence) ever writes a `problems`
-- row, so every "goal" surfaced through app/api/economy.py's
-- /v1/economy/goals/{goal_id}/... routes and prod_frontend's /problems
-- pages was structurally unreachable from real ingested data.
--
-- Compounding it: migration 102 already named its own FK columns
-- `goal_id` on procedure_submissions / benchmark_submissions /
-- procedure_usage_events / credit_ledger_events -- but pointed every one
-- of them at problems(id), not goals(id). This migration is that fix,
-- plus renaming the 3 migration-35 `problem_id` columns to `goal_id` for
-- the same reason (a `problem_id` column that means "this Goal" is the
-- exact naming confusion that hid the original bug).
--
-- SAFE, NOT A BACKFILL: every one of these 7 tables has zero rows in
-- every environment checked before this migration was written (confirmed
-- via `select count(*)` against the live DB) -- there is no data to
-- migrate, only constraints/columns to retarget. `problems` itself is
-- left in place (not dropped) since product_model.py's Benchmark/
-- Evaluation/leaderboard machinery (create_benchmark/complete_evaluation/
-- problem_leaderboard) still reads/writes it and is out of scope for this
-- pass -- see the board note this migration's own commit references.
--
-- Idempotent: every statement is a DO $$ guard or IF EXISTS/IF NOT EXISTS,
-- safe to re-run.

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'benchmarks_problem_id_fkey') THEN
        ALTER TABLE benchmarks DROP CONSTRAINT benchmarks_problem_id_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'solutions_problem_id_fkey') THEN
        ALTER TABLE solutions DROP CONSTRAINT solutions_problem_id_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluations_problem_id_fkey') THEN
        ALTER TABLE evaluations DROP CONSTRAINT evaluations_problem_id_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedure_submissions_goal_id_fkey') THEN
        ALTER TABLE procedure_submissions DROP CONSTRAINT procedure_submissions_goal_id_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'benchmark_submissions_goal_id_fkey') THEN
        ALTER TABLE benchmark_submissions DROP CONSTRAINT benchmark_submissions_goal_id_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedure_usage_events_goal_id_fkey') THEN
        ALTER TABLE procedure_usage_events DROP CONSTRAINT procedure_usage_events_goal_id_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_ledger_events_goal_id_fkey') THEN
        ALTER TABLE credit_ledger_events DROP CONSTRAINT credit_ledger_events_goal_id_fkey;
    END IF;
END $$;

-- Rename the 3 migration-35 columns so every table in this family agrees
-- on the name `goal_id` (matches migration 102's own, already-correct
-- naming intent -- only the FK target was wrong).
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'benchmarks' AND column_name = 'problem_id') THEN
        ALTER TABLE benchmarks RENAME COLUMN problem_id TO goal_id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'solutions' AND column_name = 'problem_id') THEN
        ALTER TABLE solutions RENAME COLUMN problem_id TO goal_id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'evaluations' AND column_name = 'problem_id') THEN
        ALTER TABLE evaluations RENAME COLUMN problem_id TO goal_id;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'benchmarks_goal_id_fkey') THEN
        ALTER TABLE benchmarks ADD CONSTRAINT benchmarks_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'solutions_goal_id_fkey') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluations_goal_id_fkey') THEN
        ALTER TABLE evaluations ADD CONSTRAINT evaluations_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedure_submissions_goal_id_fkey') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'benchmark_submissions_goal_id_fkey') THEN
        ALTER TABLE benchmark_submissions ADD CONSTRAINT benchmark_submissions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedure_usage_events_goal_id_fkey') THEN
        ALTER TABLE procedure_usage_events ADD CONSTRAINT procedure_usage_events_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_ledger_events_goal_id_fkey') THEN
        ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Indexes named after the old column: drop + recreate under the new name
-- (idx_*_problem -> idx_*_goal) so a fresh read of this schema doesn't
-- see a `_problem` index sitting on a `goal_id` column.
DROP INDEX IF EXISTS idx_benchmarks_problem;
CREATE INDEX IF NOT EXISTS idx_benchmarks_goal ON benchmarks(goal_id);
DROP INDEX IF EXISTS idx_solutions_problem;
CREATE INDEX IF NOT EXISTS idx_solutions_goal ON solutions(goal_id);
DROP INDEX IF EXISTS idx_evaluations_problem;
CREATE INDEX IF NOT EXISTS idx_evaluations_goal ON evaluations(goal_id);
