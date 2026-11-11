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
--   * comparison_results (P2-06): one row per (task, procedure, card): each arm's mean score and pass rate, the paired
--     score delta (tool - procedure), the cost and latency deltas, the direction, and the runs behind it. Re-running a
--     comparison updates its row (scripts/compare/store.py save()), never duplicates it.
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

CREATE TABLE IF NOT EXISTS comparison_results (
    id                bigserial PRIMARY KEY,
    task_id           text NOT NULL,
    goal_id           text,
    procedure_id      text,
    card_id           text,
    n_pairs           integer NOT NULL DEFAULT 0 CHECK (n_pairs >= 0),
    scores            jsonb NOT NULL DEFAULT '{}'::jsonb,      -- arm -> mean score
    pass_rates        jsonb NOT NULL DEFAULT '{}'::jsonb,      -- arm -> share of attempts that passed
    paired_delta      real NOT NULL DEFAULT 0,                 -- mean(tool - procedure) over paired attempts
    cost_delta_usd    numeric(12, 6) NOT NULL DEFAULT 0,
    latency_delta_ms  real NOT NULL DEFAULT 0,
    winner            text NOT NULL DEFAULT 'undecided'
                      CHECK (winner IN ('tool', 'procedure', 'tie', 'undecided')),
    run_ids           text[] NOT NULL DEFAULT '{}',
    evidence_ref      text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- one row per (task, procedure, card); a missing procedure or card is one key, not many
CREATE UNIQUE INDEX IF NOT EXISTS uq_comparison_results_key
    ON comparison_results (task_id, COALESCE(procedure_id, ''), COALESCE(card_id, ''));
CREATE INDEX IF NOT EXISTS idx_comparison_results_goal ON comparison_results (goal_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_comparison_results_updated ON comparison_results (updated_at DESC);

ALTER TABLE task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE comparison_results ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON task_runs FROM anon;
        REVOKE ALL ON comparison_results FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON task_runs FROM authenticated;
        REVOKE ALL ON comparison_results FROM authenticated;
    END IF;
END $$;
