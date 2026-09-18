-- Migration 90: run collaboration records (NOTE/BLOCKER/HANDOFF/QUESTION/
-- ANSWER) -- the structured collaboration record type `.stealth/run.md`
-- has never had.
--
-- WHY A NEW TABLE, NOT THE WORKSPACE-LOCAL JOURNAL
--   The closest existing pattern is `app/stealth/journal.py` +
--   `app/stealth/exploration.py` (`open_exploration`/`close_exploration`):
--   an append-only event log folded on read into `exploration.md`. That
--   journal is deliberately workspace-LOCAL (`.stealth/events.jsonl`
--   under one caller's own `repo_path`) -- exploration.py's own docstring
--   says so explicitly: "the journal alone is NOT durable knowledge -- it
--   lives only in this one workspace's `.stealth/` directory."
--
--   Collaboration records exist specifically so a SECOND agent -- almost
--   always a different `repo_path`/workspace checkout of the SAME
--   `execution_run_id` (the same reason `declare_file_intent`/migration 56
--   put file-intent leases on `execution_run_nodes` itself, not in the
--   journal) -- can see an open BLOCKER or a pending HANDOFF left by the
--   first agent. A workspace-local journal cannot do that: two agents
--   working the same run from two different workspaces do not share a
--   `.stealth/events.jsonl`. So this reuses the SAME durable-Postgres
--   pattern `execution_run_nodes`'s file-intent columns already
--   established for exactly this "must be visible cross-workspace" need,
--   rather than extending the local journal to do something it was
--   deliberately scoped not to do.
--
--   It is a NEW TABLE rather than new `execution_run_nodes` columns
--   because a run can accumulate many records over its lifetime (an
--   append-only history, like `execution_run_events` already is for
--   execution telemetry) -- not a single current-value column that a new
--   record would overwrite.
--
-- Modeled directly on migration 89's applicability_judgment_cache
-- structure/comment style. Fresh-start rule: additive, NO in-migration
-- backfill. Idempotent: CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS run_collaboration_records (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_run_id  UUID NOT NULL REFERENCES execution_runs(id),
    node_order        INT,
    kind              TEXT NOT NULL CHECK (kind IN ('NOTE', 'BLOCKER', 'HANDOFF', 'QUESTION', 'ANSWER')),
    actor_agent_id    TEXT NOT NULL,
    body              TEXT NOT NULL,
    -- ANSWER-only: the QUESTION record this answers. NULL for every other
    -- kind -- enforced in app.execution.run_collaboration, not by a
    -- CHECK, because validating "answers_id must point at a real QUESTION
    -- row in the SAME run" needs a lookup a CHECK constraint cannot do.
    answers_id        UUID REFERENCES run_collaboration_records(id),
    -- HANDOFF-only, best-effort: the agent id the work is being handed
    -- to, when the handing-off agent already knows it. NULL is honest
    -- ("no specific target named yet"), never fabricated.
    target_agent_id   TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_collaboration_records_run
    ON run_collaboration_records (execution_run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_run_collaboration_records_answers
    ON run_collaboration_records (answers_id) WHERE answers_id IS NOT NULL;
