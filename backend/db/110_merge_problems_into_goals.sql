-- Migration 110: collapse the "problems" concept into "goals".
--
-- WHY: Goal (migration 83) and Problem (migration 35) represented the same
-- real-world thing -- a goal worth accomplishing -- as two parallel,
-- DB-unlinked concepts. Goal is the load-bearing one: real production data,
-- a direct FK from procedures.achieves_goal_id, and woven through
-- ingestion/extraction/retrieval/execution/MCP. Problem was always a thin,
-- zero-row, purely-downstream association/read-model layer (its own
-- migration 35 header: "an ASSOCIATION + READ-MODEL layer... not a second
-- execution engine and not a copy of any target object") that nothing
-- upstream ever depended on. The economy/submissions tables (migration
-- 102-103: procedure_submissions, benchmark_submissions,
-- procedure_usage_events, credit_ledger_events) already reference
-- goal_id, not problem_id -- this migration brings the one remaining
-- holdout layer (benchmarks/solutions/evaluations from migration 35) in
-- line with that existing convention.
--
-- Safe to run destructively on the `problems` table itself: confirmed 0
-- rows in problems/benchmarks/solutions/evaluations before writing this
-- (2026-09-23). Nothing here loses real data.
--
-- Idempotent: every step guards on existence, safe to re-run.

-- ------------------------------------------------------------------
-- 1. Give goals the columns problems had that goals didn't.
--    goals.status (candidate/active/deprecated/merged) already means
--    something different from problems.status (open/active/solved/
--    archived) -- NOT reused. "Resolved" becomes its own axis
--    (resolved_at IS NULL = unresolved), independent of goals' existing
--    ingestion-lifecycle status.
-- ------------------------------------------------------------------
ALTER TABLE goals
    ADD COLUMN IF NOT EXISTS objective   TEXT,
    ADD COLUMN IF NOT EXISTS constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS metadata    JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_goals_resolved_at ON goals(resolved_at);

-- ------------------------------------------------------------------
-- 2. Repoint benchmarks/solutions/evaluations at goals instead of
--    problems: rename problem_id -> goal_id (column rename carries
--    UNIQUE constraints and indexes along with it automatically; only
--    the FK's target table needs an explicit drop + re-add).
-- ------------------------------------------------------------------
ALTER TABLE benchmarks RENAME COLUMN problem_id TO goal_id;
ALTER TABLE benchmarks DROP CONSTRAINT IF EXISTS benchmarks_problem_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'benchmarks_goal_id_fkey') THEN
        ALTER TABLE benchmarks ADD CONSTRAINT benchmarks_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;
DROP INDEX IF EXISTS idx_benchmarks_problem;
CREATE INDEX IF NOT EXISTS idx_benchmarks_goal ON benchmarks(goal_id);

ALTER TABLE solutions RENAME COLUMN problem_id TO goal_id;
ALTER TABLE solutions DROP CONSTRAINT IF EXISTS solutions_problem_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'solutions_goal_id_fkey') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;
DROP INDEX IF EXISTS idx_solutions_problem;
CREATE INDEX IF NOT EXISTS idx_solutions_goal ON solutions(goal_id);

ALTER TABLE evaluations RENAME COLUMN problem_id TO goal_id;
ALTER TABLE evaluations DROP CONSTRAINT IF EXISTS evaluations_problem_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluations_goal_id_fkey') THEN
        ALTER TABLE evaluations ADD CONSTRAINT evaluations_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;
DROP INDEX IF EXISTS idx_evaluations_problem;
CREATE INDEX IF NOT EXISTS idx_evaluations_goal ON evaluations(goal_id);

-- ------------------------------------------------------------------
-- 3. Four economy tables (migration 102-103) already had a column named
--    goal_id -- but it was wired to REFERENCES problems(id), not
--    goals(id) (a pre-existing naming/target mismatch this migration
--    also closes, discovered when the DROP TABLE below failed with
--    DependentObjectsStillExistError). Confirmed 0 rows in all four
--    before repointing -- nothing to lose, and consistent with
--    product_model's own layer having 0 rows this whole time too
--    (nothing could ever have inserted a row referencing a real goal
--    through a FK that only pointed at the perpetually-empty problems
--    table).
-- ------------------------------------------------------------------
ALTER TABLE procedure_submissions DROP CONSTRAINT IF EXISTS procedure_submissions_goal_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedure_submissions_goal_id_fkey') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;

ALTER TABLE benchmark_submissions DROP CONSTRAINT IF EXISTS benchmark_submissions_goal_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'benchmark_submissions_goal_id_fkey') THEN
        ALTER TABLE benchmark_submissions ADD CONSTRAINT benchmark_submissions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;

ALTER TABLE procedure_usage_events DROP CONSTRAINT IF EXISTS procedure_usage_events_goal_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedure_usage_events_goal_id_fkey') THEN
        ALTER TABLE procedure_usage_events ADD CONSTRAINT procedure_usage_events_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;

ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_goal_id_fkey;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_ledger_events_goal_id_fkey') THEN
        ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id);
    END IF;
END $$;

-- ------------------------------------------------------------------
-- 4. Drop the now-empty, now-unreferenced problems table. Its own
--    triggers/indexes drop automatically with it.
-- ------------------------------------------------------------------
DROP TABLE IF EXISTS problems;
