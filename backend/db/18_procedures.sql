-- Ticket 05 (memory-substrate map): dedicated procedures table, resolving
-- the TASK NODE vs PROCEDURE collapse spec.md's TARGET SEMANTIC SEPARATION
-- forbids. Today procedures are a tagging convention over task_nodes
-- (created_by='htn_method_library', decomposition stuffed into io_schema),
-- which is exactly the "procedure is a previous trajectory" anti-pattern
-- spec.md names. This table is the structure that convention was missing:
-- parameter schema, verification statistics, lifecycle status, family
-- grouping, invariants -- none of which fit io_schema/success_criteria
-- JSONB without becoming exactly what ticket 03 already rejected for
-- claims (tag-based representation for something with distinct
-- shape/volume/lifecycle).
--
-- Field list is ticket 05's own, plus both amendments applied at
-- definition time rather than as a later ALTER (same schema/code-drift
-- risk 17_episode_project_columns.sql's own commentary names for
-- episodes -- add owner_id/visibility and the utility-formula fields
-- now, while the table has no rows, not after).
--
-- Idempotent: safe to re-run, same idiom as every other migration here.

DO $$ BEGIN
    CREATE TYPE procedure_verification_state AS ENUM ('candidate', 'verified', 'retired');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE procedure_staleness AS ENUM ('fresh', 'stale', 'revalidating');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE procedure_availability AS ENUM ('active', 'quarantined', 'disabled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS procedures (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id        UUID NOT NULL DEFAULT gen_random_uuid(),  -- stable across versions; see idx_procedures_procedure_id below
    family_id           UUID REFERENCES procedures(id) ON DELETE SET NULL,  -- self-referencing (ticket 05: "just another procedures row")

    name                TEXT NOT NULL,
    goal                TEXT NOT NULL,

    -- Ticket 05: "steps on the procedure is planner-neutral" -- the HTN-
    -- specific binding (ticket 15) translates this into a concrete DAG at
    -- instantiation time; this column never carries deps/requires
    -- scheduling fields.
    steps               JSONB NOT NULL DEFAULT '[]',

    -- Parameterization (ticket 05): {"slots": {...}, "extraction_method":
    -- "deterministic_v1" | "llm_pass_v1", "extractor_version": "..."} --
    -- same versioned-extractor discipline as trace_events (ticket 06).
    parameter_schema    JSONB NOT NULL DEFAULT '{}',

    -- Ticket 12: hard-constraint preconditions are structured predicates
    -- derived from the source episode's state_before projection (ticket
    -- 10), not hand-authored tags. Predicates are versioned alongside the
    -- procedure (ticket 12's own accepted cost -- brittle to state-schema
    -- drift), which is why this rides the same version chain as
    -- everything else on this row rather than a separate predicate table.
    preconditions       JSONB NOT NULL DEFAULT '[]',
    required_state      JSONB NOT NULL DEFAULT '{}',
    expected_effects     JSONB NOT NULL DEFAULT '[]',
    postconditions      JSONB NOT NULL DEFAULT '[]',
    invariants          JSONB NOT NULL DEFAULT '[]',
    failure_conditions  JSONB NOT NULL DEFAULT '[]',

    -- Ticket 12: "scope and exclusions... machine-writable, not just
    -- human-authored" -- milestone 1 records evidence for narrowing
    -- (version-space learning) without automating it yet.
    scope               JSONB NOT NULL DEFAULT '{}',
    exclusions          JSONB NOT NULL DEFAULT '[]',

    -- Ticket 13: three orthogonal axes, not one status enum -- a
    -- procedure can be simultaneously verified, stale, AND quarantined,
    -- which a flat enum cannot express without overwriting hard-won
    -- evidence-driven verification state on every staleness event.
    verification_state  procedure_verification_state NOT NULL DEFAULT 'candidate',
    staleness           procedure_staleness NOT NULL DEFAULT 'fresh',
    availability        procedure_availability NOT NULL DEFAULT 'active',

    -- Ticket 05's original fields (attempts/successes/mean_steps/
    -- times_reused, carried forward from method_library.py's existing
    -- success_criteria shape) PLUS both amendments:
    --   - match_cost/realised_savings (ticket 13's utility formula:
    --     utility = (application_frequency * average_savings) - match_cost
    --     -- a retirement criterion orthogonal to every failure-driven one)
    --   - the ticket 13 circuit-breaker/quarantine counters, since they
    --     are also "verification statistics" in the same sense and don't
    --     warrant a second JSONB column.
    -- Single JSONB rather than a wall of individual columns: this is
    -- explicitly evolving instrumentation (ticket 13: "must be
    -- instrumented and observed rather than assumed to bite"), and
    -- ticket 05 already specified verification_stats as one field.
    verification_stats  JSONB NOT NULL DEFAULT (
        '{"attempts": 0, "successes": 0, "mean_steps": null, "times_reused": 0, ' ||
        '"distinct_contexts": 0, "context_keys_seen": [], "match_cost_total": 0, ' ||
        '"realised_savings_total": 0, "consecutive_failures": 0, "quarantine_entered_at": null, ' ||
        '"consecutive_successes_since_quarantine": 0}'
    )::jsonb,

    evidence_refs        JSONB NOT NULL DEFAULT '[]',
    source_episode_ids   UUID[] NOT NULL DEFAULT '{}',
    -- Ticket 05: "existing htn_method_library-tagged task_nodes rows
    -- become legacy candidate procedures, migrated... with a
    -- migrated_from_task_node_id provenance pointer -- never silently
    -- dropped." That migration itself is separate, later work (ticket 17
    -- in the map's numbering, not this repo's migration 17) -- this
    -- column is the landing spot for it, added now so the migration
    -- doesn't need a second ALTER.
    migrated_from_task_node_id UUID REFERENCES task_nodes(id) ON DELETE SET NULL,

    provenance          TEXT,
    domain              TEXT,          -- ticket 02: coding-specific realization split
    domain_payload      JSONB NOT NULL DEFAULT '{}',

    version             INTEGER NOT NULL DEFAULT 1,

    -- Standard bitemporal columns, same as every other core table
    -- (01_ontology.sql's knowledge_nodes/task_nodes/edges/episodes) --
    -- ticket 05: "versioning rides the existing pattern, not the
    -- existing table."
    t_valid             TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid           TIMESTAMPTZ,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired           TIMESTAMPTZ,

    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Amendment 1 (ticket 05's own "Amendments" section): ticket 09
    -- requires BOTH owner_id and visibility on every new table --
    -- access.py::visibility_predicate() references visibility in every
    -- non-unrestricted branch, so owner_id alone produces broken SQL.
    -- This ticket's original field list omitted both; corrected here at
    -- definition time.
    visibility           visibility_level NOT NULL DEFAULT 'public',
    owner_id             TEXT
);

-- procedure_id + version together identify one row; procedure_id alone
-- is the stable handle across a version chain (SUPERSEDES edges connect
-- successive versions, same pattern as claims -- ticket 05/13). Not a
-- UNIQUE constraint on procedure_id alone, since multiple versions of
-- the same logical procedure share it by design.
CREATE INDEX IF NOT EXISTS idx_procedures_procedure_id ON procedures(procedure_id);
CREATE INDEX IF NOT EXISTS idx_procedures_family ON procedures(family_id)
    WHERE family_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_procedures_validity ON procedures(t_valid, t_invalid);
CREATE INDEX IF NOT EXISTS idx_procedures_verification ON procedures(verification_state);
CREATE INDEX IF NOT EXISTS idx_procedures_availability ON procedures(availability)
    WHERE availability != 'active';
CREATE INDEX IF NOT EXISTS idx_procedures_public ON procedures(id)
    WHERE visibility = 'public' AND t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_procedures_migrated_from ON procedures(migrated_from_task_node_id)
    WHERE migrated_from_task_node_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_procedures_fts ON procedures
    USING gin (to_tsvector('english', name || ' ' || goal));

-- Ticket 05: "procedures added as a valid source_table/target_table on
-- the polymorphic edges table, so SUPERSEDES works identically to
-- claims." edges.source_table/target_table are free TEXT with a CHECK
-- constraint (01_ontology.sql) -- widen it rather than adding a parallel
-- mechanism.
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'edges'::regclass
      AND pg_get_constraintdef(oid) LIKE '%source_table%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE edges DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE edges ADD CONSTRAINT edges_source_table_check
        CHECK (source_table IN ('knowledge_nodes', 'task_nodes', 'procedures'));
END $$;

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'edges'::regclass
      AND pg_get_constraintdef(oid) LIKE '%target_table%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE edges DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE edges ADD CONSTRAINT edges_target_table_check
        CHECK (target_table IN ('knowledge_nodes', 'task_nodes', 'procedures'));
END $$;
