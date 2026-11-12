-- 204_goal_submissions.sql
--
-- Next free number: 203 (tool card vectors HNSW) was highest before this file.
--
-- What this adds (docs/goal_submission.md): a person submits a goal and the Way to do it, step by step.
--
--   * goal_submissions: one submission -- who sent it, the goal, the mode worked out from its steps (fully_human,
--     human_ai, fully_ai), its state, and the parent Way once every step is approved.
--   * goal_submission_steps: its ordered steps. A step is `human` (a description of what the person does) or
--     `automated` (a prompt to kell, a locator, or both). Every step is also its own goal with a one-step Way
--     (goal_id, procedure_row_id). Each step is reviewed on its own: the checks, the AI reviewer's verdict and the
--     reason for a rejection are kept, and `revision` counts the fixes.
--   * goal_submission_runs: a run of an approved Way. It pauses at a human step (waiting_human) until the person
--     marks the step done, then continues.
--   * procedures.approval_status gains `ai_approved`: passed the checks and the AI reviewer, not yet seen by a person.
--     The ladder is submitted < proposed < ai_approved < approved (rejected is never shown or run).
--
-- Until a submission's steps are approved, their Ways are quarantined (availability) and private, so retrieval,
-- which reads only active Ways, never returns them; app/submissions/review.py activates them on approval.
--
-- Access: application only, like 195/196.
-- Idempotent.

ALTER TABLE procedures DROP CONSTRAINT IF EXISTS procedures_approval_status_check;
ALTER TABLE procedures ADD CONSTRAINT procedures_approval_status_check
    CHECK (approval_status IN ('submitted', 'proposed', 'ai_approved', 'approved', 'rejected'));

CREATE TABLE IF NOT EXISTS goal_submissions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id             text NOT NULL,
    goal_id              uuid,
    goal_name            text NOT NULL,
    description          text NOT NULL DEFAULT '',
    created_goal         boolean NOT NULL DEFAULT false,      -- this submission made the goal (so approval publishes it)
    mode                 text NOT NULL CHECK (mode IN ('fully_human', 'human_ai', 'fully_ai')),
    status               text NOT NULL DEFAULT 'reviewing'
                         CHECK (status IN ('reviewing', 'needs_changes', 'ai_approved', 'approved', 'withdrawn')),
    parent_procedure_id  uuid,                                -- procedures.id of the parent Way, once every step passed
    notified_at          timestamptz,                         -- the last rejection email
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_goal_submissions_owner ON goal_submissions (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_goal_submissions_status ON goal_submissions (status, created_at);

CREATE TABLE IF NOT EXISTS goal_submission_steps (
    id                bigserial PRIMARY KEY,
    submission_id     uuid NOT NULL REFERENCES goal_submissions (id) ON DELETE CASCADE,
    step_order        integer NOT NULL CHECK (step_order >= 1),
    kind              text NOT NULL CHECK (kind IN ('human', 'automated')),
    title             text NOT NULL,
    description       text NOT NULL DEFAULT '',
    prompt            text NOT NULL DEFAULT '',
    locator           jsonb,                                  -- {type: url|file|script|tool, value}
    goal_id           uuid,
    created_goal      boolean NOT NULL DEFAULT false,
    procedure_row_id  uuid,
    status            text NOT NULL DEFAULT 'reviewing'
                      CHECK (status IN ('reviewing', 'ai_approved', 'approved', 'rejected')),
    reason            text NOT NULL DEFAULT '',               -- why it was rejected, as the submitter reads it
    checks            jsonb NOT NULL DEFAULT '{}',            -- the deterministic checks and the reviewer's answer
    reviewer          text NOT NULL DEFAULT '',               -- the model, or the person, that decided
    revision          integer NOT NULL DEFAULT 1,
    reviewed_at       timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (submission_id, step_order),
    CHECK (kind <> 'human' OR description <> ''),
    CHECK (kind <> 'automated' OR prompt <> '' OR locator IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_goal_submission_steps_status ON goal_submission_steps (status);

CREATE TABLE IF NOT EXISTS goal_submission_runs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id  uuid NOT NULL REFERENCES goal_submissions (id) ON DELETE CASCADE,
    owner_id       text NOT NULL,                             -- who runs it
    input          text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'waiting_human', 'done', 'failed')),
    next_step      integer NOT NULL DEFAULT 1,                -- the step_order to run (or wait on) next
    outputs        jsonb NOT NULL DEFAULT '[]',               -- one entry per finished step
    error          text NOT NULL DEFAULT '',
    started_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);

CREATE INDEX IF NOT EXISTS idx_goal_submission_runs_owner ON goal_submission_runs (owner_id, started_at DESC);

ALTER TABLE goal_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE goal_submission_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE goal_submission_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON goal_submissions, goal_submission_steps, goal_submission_runs FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON goal_submissions, goal_submission_steps, goal_submission_runs FROM authenticated;
    END IF;
END $$;
