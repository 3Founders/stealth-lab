-- Migration 98: there is no Implementation object.
--
-- Product decision (2026-09-20): procedures carry source locators and each step carries its own
-- (`steps[].source_locator`, migration 97), and a step carries how it is executed (`steps[].binding`). An
-- "implementation" is therefore just a one-step procedure, or a step of a multi-step procedure. The global
-- `implementations` object (tables, links, telemetry, columns, API, MCP tools) is removed.
--
-- What this migration does
--   1. Adds what replaces it: `execution_run_nodes.binding`, `step_execution_telemetry`, the `step_bound` event,
--      `procedures.source_artifacts`, and artifact preservation columns on `ingested_artifacts` (role, mime type,
--      language, byte size, content_ref, extraction status, execution_allowed -- found source is preserved but
--      NOT executable until screened).
--   2. Preserves history: snapshots every implementation row (+ its procedure links, + which run node pinned it)
--      into `legacy_implementation_fold`. `python -m app.ingestion.admin fold-implementations` converts those
--      snapshots into step bindings / one-step procedures through the normal capture path (embedding, goal
--      identity, dedup) and stamps `folded_procedure_id`. The archive table can be dropped once folded.
--   3. Drops the implementation tables and columns.
--
-- Idempotent: safe to re-run; every step guards on existence.

-- ------------------------------------------------------------------ 1. replacements
ALTER TABLE execution_run_nodes ADD COLUMN IF NOT EXISTS binding JSONB;

CREATE TABLE IF NOT EXISTS step_execution_telemetry (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id          UUID NOT NULL,          -- stable procedure id (no FK: procedures may live on another shard)
    step_order            INTEGER NOT NULL,
    goal_id               UUID,
    execution_run_id      UUID REFERENCES execution_runs(id),
    execution_run_node_id UUID REFERENCES execution_run_nodes(id),
    executor              TEXT NOT NULL,
    outcome_status        TEXT NOT NULL CHECK (outcome_status IN ('success', 'failure')),
    prompt_tokens         INTEGER,
    completion_tokens     INTEGER,
    llm_calls             INTEGER,
    wall_seconds          REAL,
    monetary_cost_usd     REAL,
    created_by            TEXT,
    t_created             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_step_execution_telemetry_step ON step_execution_telemetry (procedure_id, step_order, t_created);

ALTER TABLE execution_run_events DROP CONSTRAINT IF EXISTS execution_run_events_type_chk;
ALTER TABLE execution_run_events ADD CONSTRAINT execution_run_events_type_chk
    CHECK (event_type IN (
        'run_created', 'run_claimed', 'run_paused', 'run_finalized',
        'route_decided',
        'node_claimed', 'node_succeeded', 'node_failed',
        'run_started', 'procedure_retrieved', 'applicability_checked',
        'plan_created', 'implementation_bound', 'node_started',
        'tool_called', 'tool_result', 'knowledge_requested',
        'child_run_created', 'node_waiting', 'child_run_completed',
        'node_resumed', 'verification_started', 'verification_completed',
        'run_failed',
        'artifact_recorded',
        'claims_retrieved', 'candidates_reranked', 'candidate_rejected',
        -- migration 98 (implementation_bound stays legal so historical rows remain valid)
        'step_bound'
    ));

ALTER TABLE procedures ADD COLUMN IF NOT EXISTS source_artifacts JSONB NOT NULL DEFAULT '[]'::jsonb;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedures_source_artifacts_chk') THEN
        ALTER TABLE procedures ADD CONSTRAINT procedures_source_artifacts_chk CHECK (jsonb_typeof(source_artifacts) = 'array');
    END IF;
END $$;

-- Preserved source material. Byte-identical content is keyed by content_hash (UNIQUE(source_type, uri, content_hash)
-- from migration 32); the bytes live in object storage (`content_ref` = {sha256, locator, size}). Screening state is
-- `admission_decision` (migration 49); `execution_allowed` is a separate, explicit authorisation and defaults false.
ALTER TABLE ingested_artifacts
    ADD COLUMN IF NOT EXISTS role              TEXT,
    ADD COLUMN IF NOT EXISTS mime_type         TEXT,
    ADD COLUMN IF NOT EXISTS language          TEXT,
    ADD COLUMN IF NOT EXISTS byte_size         BIGINT,
    ADD COLUMN IF NOT EXISTS content_ref       JSONB,
    ADD COLUMN IF NOT EXISTS extraction_status TEXT,
    ADD COLUMN IF NOT EXISTS execution_allowed BOOLEAN NOT NULL DEFAULT false;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ingested_artifacts_role_chk') THEN
        ALTER TABLE ingested_artifacts ADD CONSTRAINT ingested_artifacts_role_chk
            CHECK (role IS NULL OR role IN ('executable_source', 'style_reference', 'design_reference', 'documentation',
                                            'dependency_manifest', 'test_fixture'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ingested_artifacts_extraction_status_chk') THEN
        ALTER TABLE ingested_artifacts ADD CONSTRAINT ingested_artifacts_extraction_status_chk
            CHECK (extraction_status IS NULL OR extraction_status IN ('pending', 'stored', 'metadata_only', 'extracted', 'failed'));
    END IF;
    -- Only an executable_source may ever be execution_allowed; a style/design reference never is.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ingested_artifacts_exec_role_chk') THEN
        ALTER TABLE ingested_artifacts ADD CONSTRAINT ingested_artifacts_exec_role_chk
            CHECK (NOT execution_allowed OR role = 'executable_source');
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_role ON ingested_artifacts(role) WHERE role IS NOT NULL;

-- ------------------------------------------------------------------ 2. preserve history
CREATE TABLE IF NOT EXISTS legacy_implementation_fold (
    implementation_id   UUID PRIMARY KEY,
    implementation      JSONB NOT NULL,                       -- the full implementations row
    links               JSONB NOT NULL DEFAULT '[]'::jsonb,   -- procedure_implementations rows (+ task links)
    pinned_run_nodes    JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{execution_run_node_id, implementation_version}]
    archived_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    folded_procedure_id UUID,                                 -- set by `admin fold-implementations`
    folded_at           TIMESTAMPTZ,
    fold_note           TEXT
);

DO $$
BEGIN
    IF to_regclass('public.implementations') IS NOT NULL THEN
        INSERT INTO legacy_implementation_fold (implementation_id, implementation, links, pinned_run_nodes)
        SELECT i.id,
               to_jsonb(i),
               COALESCE((SELECT jsonb_agg(to_jsonb(pi)) FROM procedure_implementations pi
                          WHERE pi.implementation_id = i.id AND pi.t_invalid IS NULL), '[]'::jsonb),
               COALESCE((SELECT jsonb_agg(jsonb_build_object('execution_run_node_id', n.id,
                                                             'implementation_version', n.implementation_version))
                          FROM execution_run_nodes n WHERE n.implementation_id = i.id), '[]'::jsonb)
          FROM implementations i
        ON CONFLICT (implementation_id) DO NOTHING;
    END IF;
END $$;

-- ------------------------------------------------------------------ 3. remove the object
-- The terminal fence trigger compares the dropped columns; recreate it over the surviving ones first.
CREATE OR REPLACE FUNCTION sl_execution_run_node_terminal_fence()
RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'succeeded' THEN
        IF NEW.status IS DISTINCT FROM 'succeeded'
        OR NEW.attempt_count      IS DISTINCT FROM OLD.attempt_count
        OR NEW.result_ref         IS DISTINCT FROM OLD.result_ref
        OR NEW.binding            IS DISTINCT FROM OLD.binding
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

DROP TABLE IF EXISTS implementation_execution_telemetry CASCADE;
DROP TABLE IF EXISTS procedure_implementation_bindings CASCADE;
DROP TABLE IF EXISTS procedure_implementations CASCADE;
DROP TABLE IF EXISTS implementation_tasks CASCADE;
DROP TABLE IF EXISTS implementations CASCADE;

ALTER TABLE execution_run_nodes DROP COLUMN IF EXISTS implementation_id, DROP COLUMN IF EXISTS implementation_version;
ALTER TABLE executions          DROP COLUMN IF EXISTS implementation_id;
ALTER TABLE execution_plans     DROP COLUMN IF EXISTS implementations;
ALTER TABLE evaluations         DROP COLUMN IF EXISTS implementation_id, DROP COLUMN IF EXISTS implementation_version;

-- Evidence / extraction rows that targeted an implementation have no target any more.
-- `evidence` is append-only (Band 1.9a, invariant #19, db/24_evidence.sql's
-- tg_evidence_append_only trigger): DELETE is rejected outright, and the
-- one legal UPDATE shape is the t_invalid retraction tombstone (every
-- other column must stay byte-identical). Retracting instead of deleting
-- keeps these rows queryable as historical testimony while dropping them
-- out of every live statistic (same t_invalid IS NULL filters every
-- other evidence read already uses) -- functionally equivalent to the
-- delete this replaces, without violating the invariant.
UPDATE evidence SET t_invalid = now() WHERE target_type = 'implementation' AND t_invalid IS NULL;
ALTER TABLE evidence DROP CONSTRAINT IF EXISTS evidence_target_type_chk;
-- NOT VALID, not a blanket CHECK: the retraction above can drop these
-- rows out of every live query (t_invalid IS NULL filters) but, per the
-- append-only trigger above, can never change target_type itself on an
-- existing row -- so a validated CHECK here would fail permanently on
-- any environment that ever recorded implementation-targeted evidence
-- (it did, in the real database this migration was written against).
-- NOT VALID enforces the rule for every future insert/update while
-- leaving already-written rows exactly as history recorded them --
-- the standard Postgres idiom for tightening a constraint without
-- touching or breaking existing data.
ALTER TABLE evidence ADD CONSTRAINT evidence_target_type_chk CHECK (target_type IN ('claim', 'procedure')) NOT VALID;

DELETE FROM trajectory_extraction_objects WHERE object_type = 'implementation';
DO $$
DECLARE c TEXT;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'trajectory_extraction_objects'::regclass AND contype = 'c'
                AND pg_get_constraintdef(oid) LIKE '%implementation%' LOOP
        EXECUTE format('ALTER TABLE trajectory_extraction_objects DROP CONSTRAINT %I', c);
    END LOOP;
    ALTER TABLE trajectory_extraction_objects DROP CONSTRAINT IF EXISTS trajectory_extraction_objects_type_chk;
    ALTER TABLE trajectory_extraction_objects ADD CONSTRAINT trajectory_extraction_objects_type_chk
        CHECK (object_type IN ('goal', 'claim', 'procedure'));
END $$;
