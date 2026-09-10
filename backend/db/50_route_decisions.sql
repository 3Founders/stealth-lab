-- Migration 50 (MCP hardening B1/B2): persisted RouteDecision.
--
-- Next free number: 50 (49 is highest).
--
-- WHY A NEW TABLE, NOT A REUSE OF execution_runs/executions: a
-- RouteDecision is recorded for EVERY find_best_way call, including the
-- ones that never reach an execution at all (assist/refused/needs
-- clarification/no_applicable_procedure never create an execution_plan,
-- execution_run, or executions row). execution_runs' own CHECK
-- (execution_runs_status_chk) and its required FKs to
-- execution_plans/task_graphs make it structurally impossible to record
-- a decision that never produced a plan -- this is not a second
-- execution/scheduler substrate, it is the missing "why did the router
-- decide what it decided" record that has no home in the existing
-- execution tables. Once a route produces a real execution
-- (procedure_row_id/execution_plan_id are populated), this row and the
-- resulting execution_plans/execution_runs rows are correlated by
-- procedure_row_id + created_at proximity, not by a new FK -- B3's
-- ProcedureRun identity work (separate gate) is where a real
-- route_decision_id -> execution_run linkage belongs, not invented here
-- ahead of that gate's own schema.
--
-- Discipline matches mig 33/35/36: TEXT + CHECK, DO $$-guarded
-- constraints, additive + idempotent.

CREATE TABLE IF NOT EXISTS route_decisions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    route                       TEXT NOT NULL,
    -- needs_clarification | no_applicable_procedure | assist |
    -- plan_ready | execution_ready | refused
    reason                      TEXT NOT NULL,
    confidence                  DOUBLE PRECISION,
    intent                      TEXT,
    -- assist | plan | execute | ambiguous -- classify_intent()'s own
    -- output, kept even when it did not end up driving the route (e.g.
    -- a decision-critical unknown overrides intent -> needs_clarification
    -- regardless of what the intent classifier said).

    task_description            TEXT NOT NULL,
    mode                        TEXT NOT NULL,
    repo_path                   TEXT,
    session_id                  TEXT,
    workspace_id                TEXT,

    procedure_row_id            UUID,
    procedure_id                UUID,
    procedure_version           INTEGER,
    applicable                  BOOLEAN,
    failed_constraints          JSONB NOT NULL DEFAULT '[]',
    decision_critical_unknowns  JSONB NOT NULL DEFAULT '[]',

    environment                 JSONB NOT NULL DEFAULT '{}',
    -- NOTE: "authorization" is a reserved SQL keyword (used by
    -- SET SESSION AUTHORIZATION); the column is named authorization_detail
    -- to avoid it entirely rather than quoting it everywhere downstream.
    authorization_detail        JSONB NOT NULL DEFAULT '{}',
    requires_repository         BOOLEAN NOT NULL DEFAULT FALSE,
    requires_confirmation       BOOLEAN NOT NULL DEFAULT FALSE,

    created_by                  TEXT,
    scope_type                  TEXT,
    scope_entity_id             TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='route_decisions_route_chk') THEN
        ALTER TABLE route_decisions ADD CONSTRAINT route_decisions_route_chk
            CHECK (route IN (
                'needs_clarification','no_applicable_procedure','assist',
                'plan_ready','execution_ready','refused'
            ));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='route_decisions_intent_chk') THEN
        ALTER TABLE route_decisions ADD CONSTRAINT route_decisions_intent_chk
            CHECK (intent IS NULL OR intent IN ('assist','plan','execute','ambiguous'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='route_decisions_confidence_chk') THEN
        ALTER TABLE route_decisions ADD CONSTRAINT route_decisions_confidence_chk
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='route_decisions_scope_type_chk') THEN
        ALTER TABLE route_decisions ADD CONSTRAINT route_decisions_scope_type_chk
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_route_decisions_created_at ON route_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_route_decisions_route      ON route_decisions(route);
CREATE INDEX IF NOT EXISTS idx_route_decisions_procedure  ON route_decisions(procedure_row_id)
    WHERE procedure_row_id IS NOT NULL;
