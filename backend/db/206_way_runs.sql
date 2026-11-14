-- Running any retrieved Way (app/execution/way_runs.py, docs/way_executor.md). The Way's steps, with every sub-Way
-- expanded, are copied into `plan` when the run starts, so a Way edited mid-run cannot change the run under it.
-- In "session" mode the caller's own model does the reasoning and tool steps and reports each output back; kell runs
-- scripts and plain fetches itself; a person does human steps. In "headless" mode kell does every automated step.
CREATE TABLE IF NOT EXISTS way_runs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_row_id  uuid NOT NULL REFERENCES procedures (id) ON DELETE CASCADE,
    owner_id          text NOT NULL,                             -- who runs it
    input             text NOT NULL DEFAULT '',
    mode              text NOT NULL DEFAULT 'session' CHECK (mode IN ('session', 'headless')),
    plan              jsonb NOT NULL DEFAULT '[]',               -- the flattened steps, in order
    status            text NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'waiting_session', 'waiting_human', 'done', 'failed', 'cancelled')),
    next_step         integer NOT NULL DEFAULT 1,                -- the plan step (n) to run or wait on next
    outputs           jsonb NOT NULL DEFAULT '[]',               -- one entry per finished step
    error             text NOT NULL DEFAULT '',
    waiting           jsonb,                                     -- what the session or the person must do now
    started_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);

CREATE INDEX IF NOT EXISTS idx_way_runs_owner ON way_runs (owner_id, started_at DESC);
