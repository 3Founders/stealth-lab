-- What a step produces, as a file (docs/procedure_outputs.md, app/submissions/artifacts.py).
--
-- Measured 2026-11-22: a run stored text only. A step whose prompt says "write this into literaturereview.md" had its
-- paragraph stored as the step's output, and the person who paid received the paragraph — never a file. Nothing in the
-- run wrote or served bytes.
--
-- This adds the one field that fixes it for every existing step: the author names the file the step makes, and kell
-- writes the step's own output as that file, stores it in object storage, and serves it licence-gated.
--
--   produces = 'main.tex'                  one file
--   produces = '["main.tex","refs.bib"]'   several
--   produces = NULL                        the step's output stays prose (what every stored step does today)
--
-- A `script` step is different: it writes into /out inside its sandbox and the files are captured before the
-- instance is deleted, so `produces` there names what to read back rather than what to derive.
--
-- Kept apart from `locator` on purpose: a locator is what a step READS (a url, a tool, a script); `produces` is what
-- it HANDS BACK. Storing one in the other's column would have been a silent lie in the schema.
--
-- Additive and idempotent. Next free number: 223.

ALTER TABLE goal_submission_steps
    ADD COLUMN IF NOT EXISTS produces jsonb;

-- A step that declares no file keeps NULL, so `artifacts.produced_names()` returns [] and the step behaves exactly
-- as it did before this migration. No backfill: there is nothing to guess.
COMMENT ON COLUMN goal_submission_steps.produces IS
    'The file(s) this step produces, as a name or a JSON array of names. NULL = the step''s output is prose.';
