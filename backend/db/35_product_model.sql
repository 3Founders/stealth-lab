-- Migration 35 (final-V1 hardening): the Problem / Benchmark / Solution /
-- Evaluation product layer.
--
-- Next free number: 35 (34 is highest).
--
-- WHY THIS LANDS NOW: the final-V1 directive (§9-§19, §36-§37) makes these
-- four first-class, durable concepts. Everything BELOW them already exists
-- and is authoritative -- procedures / task_nodes / task_graphs /
-- implementations (mig 33) / execution_plans + plan-version binding
-- (mig 23) / executions / evidence (mig 24) / capabilities.py. This
-- migration is an ASSOCIATION + READ-MODEL layer over that substrate, not
-- a second execution engine and not a copy of any target object.
--
-- BOARD NOTE (CLAUDE.md "discrepancies become board notes"): a PRIOR,
-- smaller directive told a previous wave "do not create a `solutions`
-- table -- model it as a read composition" (see the header of
-- app/api/solutions.py). The final-V1 directive §14 explicitly reverses
-- that: a Solution is now a durable association row
-- (id / problem_id / solution_type / target_id / version / status /
-- proposer / provenance / metadata). The existing read-composition
-- (get_solution_view / solution_search) is KEPT and continues to power
-- the /v1/solutions/{procedure_row_id} view; `solutions` here adds the
-- Problem<->target association it could not express. No target object is
-- copied: `target_id` + `target_table` point at the existing row.
--
-- DISCIPLINE (same as mig 33): TEXT + CHECK, never a DB enum (closed
-- vocab lives in Python); scope_type / scope_entity_id / owner_id for the
-- standard scope_predicates() reads; provenance as a first-class column;
-- additive + idempotent, safe on an empty OR populated DB; DO $$-guarded
-- constraints so re-runs never error.
--
-- ANTI-FABRICATION (§12 / §16): an evaluation cannot reach
-- status='completed' without real execution lineage -- enforced by a
-- trigger here, same defense-in-depth shape as mig 30's
-- assert_verified_requires_evidence.

-- ============================================================
-- 1. problems (§11)
-- ============================================================
CREATE TABLE IF NOT EXISTS problems (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    description     TEXT,
    objective       TEXT,
    constraints     JSONB NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'open',
    proposer        TEXT,                       -- human/agent identity that raised it
    provenance      TEXT,                       -- 'user' | 'ingested' | 'derived' | ...
    metadata        JSONB NOT NULL DEFAULT '{}',
    -- standard scope pair (mig 21/22 vocabulary) -- private problems stay
    -- private via scope_predicates(), exactly like procedures/claims.
    owner_id        TEXT,
    visibility      TEXT NOT NULL DEFAULT 'public',
    scope_type      TEXT,
    scope_entity_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='problems_status_chk') THEN
        ALTER TABLE problems ADD CONSTRAINT problems_status_chk
            CHECK (status IN ('open','active','solved','archived'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='problems_title_chk') THEN
        ALTER TABLE problems ADD CONSTRAINT problems_title_chk CHECK (length(trim(title)) > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='problems_visibility_chk') THEN
        ALTER TABLE problems ADD CONSTRAINT problems_visibility_chk
            CHECK (visibility IN ('public','private','unlisted'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='problems_scope_type_chk') THEN
        ALTER TABLE problems ADD CONSTRAINT problems_scope_type_chk
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_problems_status ON problems(status);
CREATE INDEX IF NOT EXISTS idx_problems_scope  ON problems(scope_type, scope_entity_id);
CREATE INDEX IF NOT EXISTS idx_problems_owner  ON problems(owner_id) WHERE owner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_problems_fts
    ON problems USING gin (to_tsvector('english', title || ' ' || COALESCE(description,'')));

-- ============================================================
-- 2. benchmarks (§12) -- versioned; frozen once used for a published
-- result. Identity fields never change after freeze (trigger below).
-- ============================================================
CREATE TABLE IF NOT EXISTS benchmarks (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    problem_id                UUID NOT NULL REFERENCES problems(id),
    name                      TEXT NOT NULL,
    description               TEXT,
    version                   INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    evaluation_protocol       JSONB NOT NULL DEFAULT '{}',
    environment_specification JSONB NOT NULL DEFAULT '{}',
    success_criteria          JSONB NOT NULL DEFAULT '{}',
    comparison_policy         JSONB NOT NULL DEFAULT '{}',
    status                    TEXT NOT NULL DEFAULT 'draft',
    frozen_at                 TIMESTAMPTZ,       -- set the first time an evaluation publishes against it
    provenance                TEXT,
    metadata                  JSONB NOT NULL DEFAULT '{}',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (problem_id, name, version)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='benchmarks_status_chk') THEN
        ALTER TABLE benchmarks ADD CONSTRAINT benchmarks_status_chk
            CHECK (status IN ('draft','active','frozen','deprecated'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='benchmarks_name_chk') THEN
        ALTER TABLE benchmarks ADD CONSTRAINT benchmarks_name_chk CHECK (length(trim(name)) > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='benchmarks_frozen_chk') THEN
        ALTER TABLE benchmarks ADD CONSTRAINT benchmarks_frozen_chk
            CHECK ((frozen_at IS NULL) OR (status IN ('frozen','deprecated')));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_benchmarks_problem ON benchmarks(problem_id);
CREATE INDEX IF NOT EXISTS idx_benchmarks_status  ON benchmarks(status);

-- Immutability of a frozen benchmark's meaning (§12): once frozen_at is
-- set, the fields that define what the benchmark MEASURES may not change.
-- description/metadata/status stay mutable (documentation, deprecation).
CREATE OR REPLACE FUNCTION assert_benchmark_frozen_immutable()
RETURNS trigger AS $$
BEGIN
    IF OLD.frozen_at IS NOT NULL THEN
        IF NEW.version           IS DISTINCT FROM OLD.version
        OR NEW.name              IS DISTINCT FROM OLD.name
        OR NEW.evaluation_protocol       IS DISTINCT FROM OLD.evaluation_protocol
        OR NEW.environment_specification IS DISTINCT FROM OLD.environment_specification
        OR NEW.success_criteria          IS DISTINCT FROM OLD.success_criteria
        OR NEW.comparison_policy          IS DISTINCT FROM OLD.comparison_policy
        OR NEW.problem_id        IS DISTINCT FROM OLD.problem_id
        OR NEW.frozen_at         IS DISTINCT FROM OLD.frozen_at THEN
            RAISE EXCEPTION 'benchmark % is frozen (frozen_at=%); its measured meaning is immutable -- create a new version row instead',
                OLD.id, OLD.frozen_at;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_benchmark_frozen_immutable ON benchmarks;
CREATE TRIGGER trg_benchmark_frozen_immutable
    BEFORE UPDATE ON benchmarks
    FOR EACH ROW EXECUTE FUNCTION assert_benchmark_frozen_immutable();

-- ============================================================
-- 3. benchmark_cases (§13) -- the smallest EXISTING executable unit is
-- task_nodes; a case is a task_node + its expected outcome + verification
-- criteria. Not a new executable abstraction.
-- ============================================================
CREATE TABLE IF NOT EXISTS benchmark_cases (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    benchmark_id         UUID NOT NULL REFERENCES benchmarks(id),
    task_node_id         UUID NOT NULL REFERENCES task_nodes(id),
    ordinal              INTEGER NOT NULL DEFAULT 0,
    input_context        JSONB NOT NULL DEFAULT '{}',
    expected_outcome     JSONB NOT NULL DEFAULT '{}',
    verification_criteria JSONB NOT NULL DEFAULT '{}',
    metadata             JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (benchmark_id, task_node_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_benchmark_cases_benchmark ON benchmark_cases(benchmark_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_cases_task      ON benchmark_cases(task_node_id);

-- ============================================================
-- 4. solutions (§14) -- association only. NO target object is copied.
-- target_id is polymorphic across procedures / task_graphs / task_nodes
-- (same reasoning evidence uses for its polymorphic targets); target_table
-- names which, both for clarity and so a reader query needs no guesswork.
-- ============================================================
CREATE TABLE IF NOT EXISTS solutions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    problem_id      UUID NOT NULL REFERENCES problems(id),
    solution_type   TEXT NOT NULL,
    target_id       UUID NOT NULL,
    target_table    TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status          TEXT NOT NULL DEFAULT 'proposed',
    proposer        TEXT,
    provenance      TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    owner_id        TEXT,
    scope_type      TEXT,
    scope_entity_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (problem_id, solution_type, target_id, version)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='solutions_type_chk') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_type_chk
            CHECK (solution_type IN ('procedure','task_graph','task'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='solutions_target_table_chk') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_target_table_chk
            CHECK (target_table IN ('procedures','task_graphs','task_nodes'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='solutions_status_chk') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_status_chk
            CHECK (status IN ('proposed','active','withdrawn','superseded'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='solutions_type_table_chk') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_type_table_chk CHECK (
            (solution_type='procedure'  AND target_table='procedures')  OR
            (solution_type='task_graph' AND target_table='task_graphs') OR
            (solution_type='task'       AND target_table='task_nodes'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='solutions_scope_type_chk') THEN
        ALTER TABLE solutions ADD CONSTRAINT solutions_scope_type_chk
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_solutions_problem ON solutions(problem_id);
CREATE INDEX IF NOT EXISTS idx_solutions_target  ON solutions(target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_solutions_status  ON solutions(status);

-- ============================================================
-- 5. evaluations (§15/§16) -- aggregate/read-model over real executions +
-- evidence. Raw execution payloads are NOT duplicated here; the link is
-- evaluation_executions.
-- ============================================================
CREATE TABLE IF NOT EXISTS evaluations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    problem_id             UUID NOT NULL REFERENCES problems(id),
    benchmark_id           UUID NOT NULL REFERENCES benchmarks(id),
    solution_id            UUID NOT NULL REFERENCES solutions(id),
    -- version pinning (§13/§23): the exact procedure/implementation the
    -- run used. UUID (+ int version) columns, no FK -- a procedure
    -- version row can be tombstoned while this evaluation must stay
    -- interpretable forever (same reasoning mig 32 gives).
    procedure_id           UUID,
    procedure_version      INTEGER,
    implementation_id      UUID REFERENCES implementations(id),
    implementation_version INTEGER,
    environment            JSONB NOT NULL DEFAULT '{}',
    run_count              INTEGER NOT NULL DEFAULT 0 CHECK (run_count >= 0),
    metrics                JSONB NOT NULL DEFAULT '{}',
    aggregate_result       TEXT,          -- 'pass'|'fail'|'partial'|'inconclusive'|NULL(unknown)
    verification_summary   JSONB NOT NULL DEFAULT '{}',
    methodology            JSONB NOT NULL DEFAULT '{}',
    status                 TEXT NOT NULL DEFAULT 'requested',
    provenance             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at           TIMESTAMPTZ
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='evaluations_status_chk') THEN
        ALTER TABLE evaluations ADD CONSTRAINT evaluations_status_chk
            CHECK (status IN ('requested','running','completed','failed','invalidated'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='evaluations_aggregate_result_chk') THEN
        ALTER TABLE evaluations ADD CONSTRAINT evaluations_aggregate_result_chk
            CHECK (aggregate_result IS NULL OR aggregate_result IN ('pass','fail','partial','inconclusive'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='evaluations_completed_at_chk') THEN
        ALTER TABLE evaluations ADD CONSTRAINT evaluations_completed_at_chk
            CHECK ((completed_at IS NULL) = (status NOT IN ('completed','failed','invalidated')));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_evaluations_problem   ON evaluations(problem_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_benchmark ON evaluations(benchmark_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_solution  ON evaluations(solution_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_status    ON evaluations(status);

-- ============================================================
-- 6. evaluation_executions (§15) -- the ONLY link between an aggregate
-- Evaluation and the real Execution rows it summarizes. "nothing more
-- than necessary."
-- ============================================================
CREATE TABLE IF NOT EXISTS evaluation_executions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id  UUID NOT NULL REFERENCES evaluations(id),
    execution_id   UUID NOT NULL,      -- executions.id (UUID since mig 23); no FK, same tombstone reasoning
    role           TEXT NOT NULL DEFAULT 'benchmark_case_run',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (evaluation_id, execution_id)
);
CREATE INDEX IF NOT EXISTS idx_evaluation_executions_evaluation ON evaluation_executions(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_executions_execution  ON evaluation_executions(execution_id);

-- ============================================================
-- 7. anti-fabrication trigger (§12/§16): an evaluation may not become
-- 'completed' without at least one linked execution. A caller cannot POST
-- status='completed' + verified_success_rate=1.0 out of thin air -- the
-- lineage Benchmark -> Solution -> Execution(s) must physically exist.
-- Defense-in-depth: the service layer also enforces this; the DB is the
-- backstop (same split as mig 30).
-- ============================================================
CREATE OR REPLACE FUNCTION assert_evaluation_completed_has_lineage()
RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'completed' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'completed') THEN
        IF NOT EXISTS (SELECT 1 FROM evaluation_executions WHERE evaluation_id = NEW.id) THEN
            RAISE EXCEPTION 'evaluation % cannot be completed with no linked execution -- a completed evaluation must point through real Execution/Verification/Evidence lineage', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_evaluation_completed_has_lineage ON evaluations;
CREATE TRIGGER trg_evaluation_completed_has_lineage
    BEFORE INSERT OR UPDATE ON evaluations
    FOR EACH ROW EXECUTE FUNCTION assert_evaluation_completed_has_lineage();

-- updated_at bump triggers (match the repo's existing convention where present)
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_problems_touch  ON problems;
CREATE TRIGGER trg_problems_touch  BEFORE UPDATE ON problems  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
DROP TRIGGER IF EXISTS trg_benchmarks_touch ON benchmarks;
CREATE TRIGGER trg_benchmarks_touch BEFORE UPDATE ON benchmarks FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
DROP TRIGGER IF EXISTS trg_solutions_touch ON solutions;
CREATE TRIGGER trg_solutions_touch BEFORE UPDATE ON solutions FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
