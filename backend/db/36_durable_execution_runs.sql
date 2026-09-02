-- Migration 36 (final-V1 hardening §24-§27): durable, restart-safe
-- execution-graph state.
--
-- Next free number: 36 (35 is highest).
--
-- WHY A NEW OBJECT, NOT A COLUMN ON `executions`: migration 23's
-- `executions` is append-only, frozen by trigger (sl_raise_frozen) --
-- "born complete after the run settles", no UPDATE path ever. Durable
-- retry/resume needs a MUTABLE per-run + per-node progress record. So:
--   execution_runs       -- the resumable unit (mutable working state).
--   execution_run_nodes  -- one row per PlanNode.order, mutable.
-- When a run reaches a terminal state, ONE immutable `executions` row is
-- appended via the EXISTING record_plan_execution(); execution_runs.
-- final_execution_id points at it. `executions` stays the append-only
-- testimony; this is the working state beside it. NOT a second scheduler
-- (§27/§33): no queue, no polling loop -- the existing graph_executor
-- still runs nodes; this records what happened so a crash can resume.
--
-- Discipline matches mig 33/35: TEXT + CHECK, DO $$-guarded constraints,
-- additive + idempotent.
--
-- §25 invariants enforced here (DB backstop; the service also enforces):
--   - a 'succeeded' node cannot leave 'succeeded', and its attempt_count /
--     result_ref / implementation binding cannot change  -> terminal fence
--     trigger. ("completed node not rerun", "failure stays visible",
--     "stale workers cannot mutate terminal state").
--   - a node claim carries worker_id + lease_expires_at; the service's
--     UPDATE ... WHERE worker_id = $me fences a stale worker out.

-- ============================================================
-- 1. execution_runs -- the resumable unit
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_plan_id   UUID NOT NULL REFERENCES execution_plans(id),
    task_graph_id       UUID NOT NULL REFERENCES task_graphs(id),
    procedure_id        UUID NOT NULL,
    procedure_version   INTEGER NOT NULL CHECK (procedure_version >= 1),

    status              TEXT NOT NULL DEFAULT 'pending',
    -- pending -> running -> (succeeded | failed | paused | cancelled)
    -- 'paused' = at least one side-effecting node crashed mid-flight and
    -- was parked (§32): needs a human/explicit decision, not a blind rerun.
    resume_count        INTEGER NOT NULL DEFAULT 0,

    -- terminal-state append: the one immutable executions row for this run.
    final_execution_id  UUID REFERENCES executions(id),
    final_outcome       TEXT,        -- mirrors executions.outcome once terminal

    -- concurrency: a resume holds this run via a transaction-scoped
    -- advisory lock keyed on id; these two are informational + for the
    -- stale-worker fence on the nodes.
    worker_id           TEXT,
    lease_expires_at    TIMESTAMPTZ,

    parameters          JSONB NOT NULL DEFAULT '{}',
    created_by          TEXT,
    scope_type          TEXT,
    scope_entity_id     TEXT,
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='execution_runs_status_chk') THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_status_chk
            CHECK (status IN ('pending','running','succeeded','failed','paused','cancelled'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='execution_runs_final_outcome_chk') THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_final_outcome_chk
            CHECK (final_outcome IS NULL OR final_outcome IN ('success','failure','needs_rework'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='execution_runs_terminal_chk') THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_terminal_chk
            CHECK ((status IN ('succeeded','failed')) = (final_execution_id IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='execution_runs_scope_type_chk') THEN
        ALTER TABLE execution_runs ADD CONSTRAINT execution_runs_scope_type_chk
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_execution_runs_plan   ON execution_runs(execution_plan_id);
CREATE INDEX IF NOT EXISTS idx_execution_runs_status ON execution_runs(status);
CREATE INDEX IF NOT EXISTS idx_execution_runs_resumable
    ON execution_runs(status) WHERE status IN ('running','pending','paused');

-- ============================================================
-- 2. execution_run_nodes -- one mutable row per PlanNode.order
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_run_nodes (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_run_id       UUID NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
    node_order             INTEGER NOT NULL,

    status                 TEXT NOT NULL DEFAULT 'pending',
    -- pending -> running -> (succeeded | failed | blocked | cancelled | resumable)
    -- 'blocked'   : a dependency failed/was skipped -- never attempted.
    -- 'resumable' : crashed mid-node (lease expired while 'running'); the
    --               service decides retry vs park based on side_effecting.
    attempt_count          INTEGER NOT NULL DEFAULT 0,
    max_attempts           INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),

    -- pinned at first resolve, NEVER re-resolved on resume (§23/§26).
    implementation_id      UUID,
    implementation_version INTEGER,

    side_effecting         BOOLEAN NOT NULL DEFAULT FALSE,
    result_ref             JSONB NOT NULL DEFAULT '{}',   -- pointer/summary, not payload
    error_ref              JSONB NOT NULL DEFAULT '{}',
    error_class            TEXT,        -- retryable classification (service vocab)
    verification_state     TEXT NOT NULL DEFAULT 'unverified',

    worker_id              TEXT,
    lease_expires_at       TIMESTAMPTZ,
    started_at             TIMESTAMPTZ,
    ended_at               TIMESTAMPTZ,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (execution_run_id, node_order)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ern_status_chk') THEN
        ALTER TABLE execution_run_nodes ADD CONSTRAINT ern_status_chk
            CHECK (status IN ('pending','running','succeeded','failed','blocked','cancelled','resumable'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ern_verification_state_chk') THEN
        ALTER TABLE execution_run_nodes ADD CONSTRAINT ern_verification_state_chk
            CHECK (verification_state IN ('unverified','verified','failed'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_ern_run    ON execution_run_nodes(execution_run_id);
CREATE INDEX IF NOT EXISTS idx_ern_status ON execution_run_nodes(execution_run_id, status);

-- ============================================================
-- 3. terminal-state fence (§25) -- once a node is 'succeeded', its
-- outcome is immutable. Nothing (a stale worker, a double resume, a
-- retry bug) can un-succeed it, re-run it, or rewrite what it produced.
-- A 'cancelled' node is likewise terminal. Everything else stays mutable.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_execution_run_node_terminal_fence()
RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'succeeded' THEN
        IF NEW.status IS DISTINCT FROM 'succeeded'
        OR NEW.attempt_count      IS DISTINCT FROM OLD.attempt_count
        OR NEW.result_ref         IS DISTINCT FROM OLD.result_ref
        OR NEW.implementation_id  IS DISTINCT FROM OLD.implementation_id
        OR NEW.implementation_version IS DISTINCT FROM OLD.implementation_version
        OR NEW.verification_state IS DISTINCT FROM OLD.verification_state THEN
            RAISE EXCEPTION 'execution_run_node %/% is succeeded -- terminal, cannot be rerun or rewritten (§25)',
                OLD.execution_run_id, OLD.node_order;
        END IF;
    ELSIF OLD.status = 'cancelled' AND NEW.status IS DISTINCT FROM 'cancelled' THEN
        RAISE EXCEPTION 'execution_run_node %/% is cancelled -- terminal',
            OLD.execution_run_id, OLD.node_order;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ern_terminal_fence ON execution_run_nodes;
CREATE TRIGGER trg_ern_terminal_fence
    BEFORE UPDATE ON execution_run_nodes
    FOR EACH ROW EXECUTE FUNCTION sl_execution_run_node_terminal_fence();

-- updated_at bump for the run row
CREATE OR REPLACE FUNCTION sl_touch_updated_at_generic() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_execution_runs_touch ON execution_runs;
CREATE TRIGGER trg_execution_runs_touch
    BEFORE UPDATE ON execution_runs
    FOR EACH ROW EXECUTE FUNCTION sl_touch_updated_at_generic();
