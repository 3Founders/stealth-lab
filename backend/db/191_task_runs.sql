-- 191_task_runs.sql
--
-- Next free number: 187 (security goals) was highest before this file; 188-190 are allocated to the policy store,
-- intent certificates and the signed audit (docs/goal_router.md P1-05..P1-07).
--
-- What this adds (docs/goal_router.md P1-10): the record both arms of a comparison write, so Phase 2 is measurement
-- only.
--
--   * task_runs: one row per (task, arm, attempt). The procedure arm and the tool arm write the same columns: the goal,
--     the procedure or the card that ran, latency, cost, tokens, status, score, the artifact's hash, the error class and
--     a trace reference. A refused call is still a row (status 'error', error_class 'gate:deny' or 'gate:approve'), so a
--     refusal counts against an arm instead of vanishing. Never the prompt, arguments or output: only their hashes.
--   * tool_gateway_calls (163) gains the task, the certificate, the gate's decision and the cost of each call.
--     Nullable, so rows written before this file stay valid (backfill-safe).
--
-- P2-06 adds the comparison table to this file (coordinated in docs/goal_router.md).
--
-- Access: application only, like 160/162/163 (RLS on, no policies, the Supabase API roles revoked).
-- Idempotent.

CREATE TABLE IF NOT EXISTS task_runs (
    run_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id        text NOT NULL,
    arm            text NOT NULL CHECK (arm IN ('procedure', 'tool', 'baseline')),
    attempt        integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    goal_id        text,                                   -- a taxonomy key ("web") or a Goal uuid
    procedure_id   text,
    card_id        text,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    latency_ms     integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    cost_usd       numeric(12, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    tokens_in      integer CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out     integer CHECK (tokens_out IS NULL OR tokens_out >= 0),
    status         text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'pass', 'fail', 'error')),
    score          real,
    artifact_hash  text,
    error_class    text,
    trace_ref      text,
    UNIQUE (task_id, arm, attempt)
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs (task_id, arm, attempt);
CREATE INDEX IF NOT EXISTS idx_task_runs_goal ON task_runs (goal_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_runs_started ON task_runs (started_at DESC);

ALTER TABLE tool_gateway_calls ADD COLUMN IF NOT EXISTS task_id  text;
ALTER TABLE tool_gateway_calls ADD COLUMN IF NOT EXISTS cert_id  text;
ALTER TABLE tool_gateway_calls ADD COLUMN IF NOT EXISTS decision text;          -- allow | deny | approve
ALTER TABLE tool_gateway_calls ADD COLUMN IF NOT EXISTS cost_usd numeric(12, 6);
CREATE INDEX IF NOT EXISTS idx_tool_gateway_calls_task ON tool_gateway_calls (task_id) WHERE task_id IS NOT NULL;

ALTER TABLE task_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON task_runs FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON task_runs FROM authenticated;
    END IF;
END $$;
