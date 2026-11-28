-- The Agent37 sandbox a website run executes in (app/execution/run_sandbox.py). One instance per run, kept for the
-- run's life so every step shares one workspace, deleted when the run ends. NULL: the run has no sandbox (a session
-- run, a run started before this existed, or a deployment with no sandbox configured).
--   {"provider": "agent37", "id": "<instance id>", "ready": true, "created_at": "...", "deleted": false}
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS sandbox jsonb;
