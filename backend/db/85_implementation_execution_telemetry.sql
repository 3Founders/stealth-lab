-- Migration 85: real per-execution telemetry for Implementations
-- (meta-harness/execu.md Sec 13/14/32, 2026-09-15).
--
-- WHY
--   Confirmed by direct audit before writing this: there is NO real,
--   queryable cost/latency ledger per Implementation anywhere in this
--   schema today. `evidence` (migration 24) records belief/verification
--   support (strength_score, outcome_status) but has no cost/token/
--   latency columns at all -- it answers "did this work", not "what did
--   it cost". `execution_runs.resource_usage` (migration 74) is real but
--   RUN-scoped, not per-implementation, and not queryable by
--   implementation_id.
--
--   The founder directive's own instruction (2026-09-15, in response to
--   the disclosed gap): "don't say we can estimate cause we can't --
--   after some runs and accumulation of evidence we will, bake this
--   into the system." This migration is that baking-in: a real,
--   append-only table, wired as an AUTOMATIC side effect of
--   app/execution/implementation_executor.py::execute_implementation()
--   (the one real chokepoint every dispatch already passes through --
--   same "wire it at the chokepoint, not per caller" discipline
--   trace_redaction.py already established for a different concern).
--
-- WHAT THIS DOES NOT DO
--   No monetary_cost_usd column with real values -- there is no pricing
--   table anywhere in this codebase (confirmed: `record_run_usage`'s own
--   docstring already says "cost_usd stays 0 here -- an honest 'not
--   tracked', never a guessed dollar figure"). This migration only
--   captures what is ACTUALLY measurable today: real tokens, real wall-
--   clock seconds, real outcome. A monetary figure stays a real, later,
--   separate decision (a pricing table + join), not fabricated here.
--
-- Append-only by convention (matches evidence/execution_run_events'
-- own posture for immutable observations) -- no UPDATE path, no DELETE
-- trigger needed since nothing in this codebase ever writes one twice
-- for the same real execution attempt.
--
-- Next free migration number: 86.

CREATE TABLE IF NOT EXISTS implementation_execution_telemetry (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    implementation_id      UUID NOT NULL REFERENCES implementations(id),
    -- The Goal this execution was realizing, when known (the recursive
    -- Goal compiler's own case) -- nullable, since execute_implementation
    -- has real callers outside Goal resolution too (Procedure-based
    -- plans via find_best_way).
    goal_id                 UUID REFERENCES goals(id),

    -- Durable-run linkage, when this execution happened inside one --
    -- both nullable (a direct/ad-hoc execute_implementation call has
    -- neither).
    execution_run_id        UUID REFERENCES execution_runs(id),
    execution_run_node_id    UUID REFERENCES execution_run_nodes(id),

    executor                 TEXT NOT NULL,   -- the real kind that ran: frontier/deterministic/api/tool
    outcome_status           TEXT NOT NULL CHECK (outcome_status IN ('success', 'failure')),

    -- Real, measured quantities only -- every one nullable, since not
    -- every executor kind produces every field (a deterministic script
    -- has no LLM token count; an API call has no wall_time_seconds
    -- breakdown beyond total latency). NULL means "not measured for
    -- this execution", never a fabricated zero.
    prompt_tokens            INTEGER,
    completion_tokens        INTEGER,
    llm_calls                INTEGER,
    wall_seconds              REAL,

    -- Forward-compatible, honestly empty until a real pricing mechanism
    -- exists (see module docstring) -- never populated by this
    -- migration's own writer.
    monetary_cost_usd         REAL,

    created_by                TEXT,
    t_created                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_implementation_execution_telemetry_impl
    ON implementation_execution_telemetry (implementation_id, t_created);

CREATE INDEX IF NOT EXISTS idx_implementation_execution_telemetry_goal
    ON implementation_execution_telemetry (goal_id)
    WHERE goal_id IS NOT NULL;
