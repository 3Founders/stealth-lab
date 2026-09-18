-- Migration 91: make BLOCKER and HANDOFF closeable, the same way QUESTION
-- is closed by ANSWER.
--
-- WHY TWO NEW KINDS RATHER THAN RELAXING ANSWER'S OWN TARGET CHECK
--   The alternative (letting a plain ANSWER -- or a NOTE -- point its
--   existing `answers_id` at a BLOCKER/HANDOFF too) would make one kind
--   mean three different things depending on what it points at: "this
--   answers a question" / "this resolves a blocker" / "this accepts a
--   handoff" are three different real-world actions with different
--   verbs, and collapsing them into a generically-overloaded ANSWER
--   would make `run.md`'s own COLLAB lines harder to read at a glance
--   (`grep BLOCKER_RESOLVED run.md` is a real, specific signal; `grep
--   ANSWER run.md` mixed with actual question-answers would not be).
--   `app.execution.run_collaboration`'s existing validation shape
--   (`kind -> the ONE record-kind its `answers_id` must reference`) also
--   extends cleanly to a dict of three entries instead of needing an
--   `if kind == 'ANSWER' or (kind == 'NOTE' and answers_id set to a
--   BLOCKER)` special case. Two new, narrow kinds, not a general
--   lifecycle/state machine -- still exactly the "answers_id points at
--   the record this one is about" mechanism ANSWER already established,
--   reused twice, not reinvented.
--
-- Same fresh-start rule: additive only, no backfill (every row written
-- before this migration used the five original kinds; none needs
-- reclassifying). Constraint replacement, not a new column.

ALTER TABLE run_collaboration_records DROP CONSTRAINT IF EXISTS run_collaboration_records_kind_check;
ALTER TABLE run_collaboration_records ADD CONSTRAINT run_collaboration_records_kind_check
    CHECK (kind IN ('NOTE', 'BLOCKER', 'HANDOFF', 'QUESTION', 'ANSWER', 'BLOCKER_RESOLVED', 'HANDOFF_ACCEPTED'));
