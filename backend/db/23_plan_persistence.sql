-- Migration 23 (Band 1.7): persist ExecutionPlan / TaskGraph [D→frozen]
-- and bind every execution to an exact plan version.
--
-- Why this is the purest one-way door in Band 1 (ROADMAP.md 1.7):
-- executions recorded without an exact-plan reference are unrecoverable
-- data loss w.r.t. spec v4 §39 invariants #1–2 — no later band can
-- retrofit history that never named the plan it ran. Today plans live
-- only in memory inside execution/htn_agent.py's run loop
-- (trial_implementation.md Part A, "Execution layer"), so nothing stored
-- can say WHICH compiled plan version produced a trace. This migration
-- is the storage half of the fix; app/execution/plans.py +
-- app/models/plan.py are the boundary half (compile, validate, hash,
-- rebind), and tests/test_band1_7_plans.py proves the contracts offline.
--
-- Fresh-start ruling: applied to an empty/rebuilt database; NO backfills,
-- NO transition machinery, same as migrations 21/22.
--
-- Idempotent: safe to re-run, same idiom as every other migration here.
-- Next free number: 22 was highest before this file.
--
-- Shape authority: schema.md "ExecutionPlan [D → frozen at execution]",
-- "TaskGraph / TaskNode", "Execution [H]" and spec v4 §15/§22. Where the
-- repo already has a native convention for something schema.md renders
-- differently, the repo convention wins (procedure_id + integer version
-- as two typed columns, not the §22 "procedure_42:v7" presentation
-- string) — same reasoning migration 18 used to adopt steps-as-JSONB.

-- ============================================================
-- 0. procedures (procedure_id, version) becomes UNIQUE so the exact
-- version a plan references can be a real composite FOREIGN KEY.
-- Invariant #2 ("Every ExecutionPlan references an exact Procedure
-- version") thus has engine teeth: a plan naming a nonexistent
-- procedure version cannot be written at all, not merely rejected by
-- a validator someone might bypass. Multiple versions of one logical
-- procedure share procedure_id by design (18_procedures.sql), which
-- is why the pair -- not procedure_id alone -- is what's unique.
-- ============================================================
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'procedures_procedure_id_version_key'
    ) THEN
        ALTER TABLE procedures
            ADD CONSTRAINT procedures_procedure_id_version_key
            UNIQUE (procedure_id, version);
    END IF;
END $$;

-- ============================================================
-- 1. execution_plans -- the compiled instantiation of ONE procedure
-- version for ONE task under ONE starting state (spec §15).
--
-- [D → frozen]: rows are written once by the compiler and never
-- edited -- changed inputs mean compile a NEW plan, never mutate one
-- (schema.md: "Changed inputs mean regenerate a new DAG, never edit
-- one"). The freeze is enforced below by BEFORE UPDATE/DELETE
-- triggers, not convention.
--
-- No bi-temporal quartet, deliberately: t_invalid tombstoning IS an
-- UPDATE, and a frozen row cannot have one. A superseded plan is
-- simply never referenced again; its replacement is a new row. This
-- is the same reason trace_id in migration 12 carries no validity
-- window -- immutability makes validity windows meaningless.
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_plans (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Invariant #2: the exact source procedure version, both halves
    -- NOT NULL. procedure_id is the stable cross-version handle
    -- (18_procedures.sql); procedure_row_id pins the immutable row the
    -- compile actually read, so even a future re-numbered version chain
    -- cannot blur what this plan was instantiated FROM. The composite
    -- FK makes a versionless or dangling reference unwritable.
    procedure_id        UUID NOT NULL,
    procedure_version   INTEGER NOT NULL CHECK (procedure_version >= 1),
    procedure_row_id    UUID NOT NULL REFERENCES procedures(id),
    FOREIGN KEY (procedure_id, procedure_version)
        REFERENCES procedures(procedure_id, version),

    -- Scope of the plan itself (Band 1.3 mandate: scope columns on
    -- every table; §15 puts scope on ExecutionPlan directly). Nodes
    -- inherit this and may narrow it -- narrowing is validated at the
    -- boundary (app/execution/plans.py), per the house split of
    -- enforcement between gate and engine (migration 21 preamble).
    scope_type          TEXT,
    scope_entity_id     TEXT,

    -- task: {description} (schema.md). parameters: the bound values
    -- this instantiation pinned.
    task_description    TEXT NOT NULL,
    parameters          JSONB NOT NULL DEFAULT '{}',

    -- starting_state: → State. The State table itself lands with later
    -- band work; the handle is recorded NOW because a plan compiled
    -- against an unrecorded starting state could never be rebound to
    -- it afterwards -- the same one-way-door logic as the rest of
    -- this table. Nullable until State persistence exists; NOT nullable
    -- is a later migration's decision once that writer is real.
    starting_state_id   UUID,

    -- resolved_claims: [{claim_id, version}] -- claims frozen at
    -- compile time with their exact versions (§16 stores Claim refs,
    -- never embedded claims; invariant #16's discipline applied to
    -- plans). selected_branches: which conditional branches of the
    -- source procedure this instantiation selected.
    resolved_claims     JSONB NOT NULL DEFAULT '[]',
    selected_branches   JSONB NOT NULL DEFAULT '[]',

    -- {step_id → implementation_ref}: implementation pluralism bound
    -- per node/step at plan time (§23 routing output lives here).
    implementations     JSONB NOT NULL DEFAULT '{}',

    safety_check        TEXT CHECK (safety_check IN (
                            'passed', 'failed', 'requires_review')),
    verification_plan   JSONB NOT NULL DEFAULT '{}',

    -- Derived-object birth discipline (V0 gate, invariant #21):
    -- plans are compiled artifacts, so the compiler stamps
    -- "name@version" here -- same vocabulary as
    -- procedures.extracted_by (migration 20). Nullable COLUMN /
    -- required-at-boundary follows migration 21's stated split: the
    -- gate owns the error message, the column exists so direct-SQL
    -- writers have a place to put the truth.
    extractor_version   TEXT,

    -- Freeze fingerprints. procedure_content_hash = canonical hash of
    -- the source procedure version AT COMPILE TIME (invariant #17:
    -- instantiation must never silently modify the source -- a replay
    -- rebind recomputes and compares). content_hash = canonical hash
    -- of the entire semantic payload below (ids/timestamps excluded --
    -- two compiles from identical inputs MUST produce identical hashes,
    -- that is what lets a replay REBIND the identical plan instead of
    -- forking a phantom twin). See app/execution/plans.py::compile_plan.
    procedure_content_hash TEXT NOT NULL,
    content_hash        TEXT NOT NULL,

    created_by          TEXT,
    -- Ticket 09 pair rule: every new table carries BOTH columns
    -- (access.py::visibility_predicate() breaks otherwise).
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_execution_plans_proc
    ON execution_plans(procedure_id, procedure_version);
CREATE INDEX IF NOT EXISTS idx_execution_plans_scope
    ON execution_plans(scope_type, scope_entity_id);
CREATE INDEX IF NOT EXISTS idx_execution_plans_hash
    ON execution_plans(content_hash);

-- ============================================================
-- 2. task_graphs -- the compiled DAG, frozen with its plan (schema.md:
-- TaskGraph [D → frozen at execution], "the only place scheduling is
-- legal"). One graph per plan: UNIQUE (execution_plan_id).
--
-- nodes is a JSONB array of TaskNode objects shaped exactly like
-- schema.md's TaskNode (step_ref {procedure_id, version, order},
-- parameters, node_class, implementation_id, cost_budget,
-- verification_gate, deps). There is deliberately NO separate edges
-- table/column: scheduling edges exist ONLY as node deps (§24), the
-- same placement htn_agent.Node already uses -- a parallel edge store
-- would be a second spelling of the same truth waiting to disagree
-- with itself. Node-level scope narrowing rides inside each node dict
-- and is validated at the boundary, never widened past plan scope.
-- ============================================================
CREATE TABLE IF NOT EXISTS task_graphs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_plan_id   UUID NOT NULL UNIQUE REFERENCES execution_plans(id),

    -- Canonical hash over {"nodes": [...]} alone -- linkage fields are
    -- excluded so graph_hash is reproducible from content, matching
    -- how content_hash behaves above. Replay compares this first when
    -- rebinding a plan to fresh inputs.
    graph_hash          TEXT NOT NULL,

    nodes               JSONB NOT NULL DEFAULT '[]',

    created_by          TEXT,
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_graphs_plan ON task_graphs(execution_plan_id);

-- ============================================================
-- 3. executions [H] -- the actual run of an ExecutionPlan (spec §22),
-- append-only: insert-once-after-run, then frozen by trigger. The
-- harness pattern this mirrors is services/execution.py's own -- it
-- writes the trace row AFTER the skill settles, so the row is born
-- complete and no correction path ever needs UPDATE (invariant #19:
-- correcting means appending a superseding record).
--
-- outcome reuses traces.outcome's exact closed vocabulary
-- ('success' | 'failure' | 'needs_rework') rather than inventing a
-- fourth spelling of success/failure -- the same reuse rule
-- 20_procedure_extraction.sql applied to decomposition statuses.
-- Outcome detail (scores, criteria metrics -- invariant #13) belongs
-- to the Outcome object landing with Band 1.9; this column records
-- only the terminal status the run itself produced.
-- ============================================================
CREATE TABLE IF NOT EXISTS executions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Invariant #1, engine-enforced: there is NO nullable path to an
    -- orphan execution. Every execution names the exact plan AND the
    -- exact graph it ran (schema.md Execution carries both).
    execution_plan_id   UUID NOT NULL REFERENCES execution_plans(id),
    task_graph_id       UUID NOT NULL REFERENCES task_graphs(id),

    -- "Always record the exact Procedure version" (§22) -- denormalized
    -- onto the execution itself so it stays self-describing without a
    -- join. Boundary validation (validate_execution_binding) refuses a
    -- payload whose snapshot disagrees with its plan's.
    procedure_id        UUID NOT NULL,
    procedure_version   INTEGER NOT NULL,

    -- Forward-compatible handles, same reasoning as
    -- execution_plans.starting_state_id: recordable now, joinable when
    -- their writers exist. state_id → State; implementation_id →
    -- Implementation (no such table yet, hence no FK -- the identical
    -- call migration 20 made for extracted_by).
    state_id            UUID,
    implementation_id   UUID,

    parameters          JSONB NOT NULL DEFAULT '{}',

    -- → agent_traces.trace_id / traces.trace_id. Polymorphic target,
    -- so no FK -- same deliberate choice as traces.parent_trace_id
    -- (01_ontology.sql) and extracted_by (migration 20).
    trace_id            TEXT,

    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    outcome             TEXT CHECK (outcome IN ('success', 'failure', 'needs_rework')),

    actor_id            TEXT,
    created_by          TEXT,
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    scope_type          TEXT,
    scope_entity_id     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_executions_plan ON executions(execution_plan_id);
CREATE INDEX IF NOT EXISTS idx_executions_proc
    ON executions(procedure_id, procedure_version);
CREATE INDEX IF NOT EXISTS idx_executions_scope
    ON executions(scope_type, scope_entity_id);

-- ============================================================
-- 4. Scope CHECK constraints for the three new tables -- migration
-- 22's defense-in-depth extended to the tables this migration adds,
-- same greppable one-block-per-table shape so the static contract
-- tests can read them as text.
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_execution_plans'
    ) THEN
        ALTER TABLE execution_plans ADD CONSTRAINT scope_type_chk_execution_plans
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_task_graphs'
    ) THEN
        ALTER TABLE task_graphs ADD CONSTRAINT scope_type_chk_task_graphs
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_executions'
    ) THEN
        ALTER TABLE executions ADD CONSTRAINT scope_type_chk_executions
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

-- ============================================================
-- 5. The freeze itself. All three tables reject UPDATE and DELETE at
-- the engine: [D→frozen] plans/graphs and [H] executions are
-- append-only forever; corrections append new rows. This is Band
-- 1.10's birth discipline applied to these tables from the first
-- ingested row, and the mechanism Appendix C cites for invariant #17
-- ("changes require new version").
--
-- task_graphs has no DELETE exception despite being 1:1 with its plan:
-- if a plan is wrong, it stays as testimony and a corrected plan is
-- compiled -- deleting the evidence is exactly the silent-modification
-- failure mode invariant #17 exists to prevent.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_raise_frozen() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%.% is append-only/frozen (Band 1.7): % rejected -- append a superseding row instead',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_execution_plans_frozen') THEN
        CREATE TRIGGER tg_execution_plans_frozen
            BEFORE UPDATE OR DELETE ON execution_plans
            FOR EACH ROW EXECUTE FUNCTION sl_raise_frozen();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_task_graphs_frozen') THEN
        CREATE TRIGGER tg_task_graphs_frozen
            BEFORE UPDATE OR DELETE ON task_graphs
            FOR EACH ROW EXECUTE FUNCTION sl_raise_frozen();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_executions_frozen') THEN
        CREATE TRIGGER tg_executions_frozen
            BEFORE UPDATE OR DELETE ON executions
            FOR EACH ROW EXECUTE FUNCTION sl_raise_frozen();
    END IF;
END $$;
