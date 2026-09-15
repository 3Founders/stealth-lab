-- Migration 81: index to support goal-based Implementation lookup.
--
-- WHY
--   Migration 80 added `implementations.goal` (ProcedureStep.goal <->
--   Implementation.goal) but nothing queries by it yet -- confirmed by
--   grepping app/ for by_goal/group_by_goal/list_by_goal: zero hits. The
--   meta-harness's Implementation-selection stage (grouping candidates by
--   a grounded Step's goal, per the founder directive's own "search finds
--   candidates" principle) needs this to be a real, indexed lookup rather
--   than a sequential scan once the implementations table is non-trivial.
--
-- Partial + composite: `goal` is sparse today (migration 80's own docstring:
-- only one writer populates it, for skill-package bundled scripts), and
-- every real lookup also filters on `status` (STATUS_VALUES in
-- implementation_registry.py) -- WHERE goal IS NOT NULL keeps the index
-- small and skips the overwhelming majority of rows that will never be
-- found by this path.
--
-- Additive, idempotent (this repo's hard rule): CREATE INDEX IF NOT EXISTS,
-- no table lock beyond a normal index build, no data changes.
--
-- Next free migration number: 82.

CREATE INDEX IF NOT EXISTS idx_implementations_goal_status
    ON implementations (goal, status)
    WHERE goal IS NOT NULL;
