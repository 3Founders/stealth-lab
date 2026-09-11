-- Migration 58: captures TWO real, live, already-in-production tables
-- that had NO committed migration anywhere in this repo --
-- `procedure_implementations` (482 real rows) and `procedure_dependencies`
-- (439 real rows). Both are written by `app/services/skill_ingestion.py`
-- (the real global-skill-package ingestion path, A8 in the ingestion
-- hardening plan) and `procedure_implementations` is read by
-- `app/services/publication.py`'s dependency/implementation traversal.
--
-- Confirmed live (this session): neither table appears in ANY file
-- under backend/db/ before this one, yet both exist with real data on
-- the shared database. This migration is a faithful, exact capture of
-- their REAL, already-relied-upon schema (introspected via
-- information_schema.columns / pg_constraint / pg_indexes against the
-- live database), not a redesign -- CREATE TABLE IF NOT EXISTS is a
-- true no-op against the already-existing tables in every environment
-- that already has them (this shared dev DB), and creates them exactly
-- as-is in any environment that doesn't (a fresh CI database, a new
-- developer's local Postgres) -- closing the real gap: those
-- environments would otherwise never get these tables at all, breaking
-- `publish_procedure()`'s dependency traversal and skill_ingestion's
-- own implementation-linking silently (a bare `INSERT INTO` against a
-- nonexistent table, not a caught/handled condition anywhere in either
-- caller).
--
-- IMPORTANT (discovered while building THIS gate's own B23/B24 Procedure
-- <->Implementation many-to-many work, migration 53): this table
-- ALREADY IS that relation -- richer than the new one built in
-- migration 53 (`procedure_implementation_bindings`), with real
-- bi-temporal versioning (t_valid/t_invalid, the same pattern
-- `procedures`/`knowledge_nodes` already use) and an `implementation_
-- version`/`implementation_version_constraint` pair migration 53 didn't
-- have. Migration 59 (this same batch) migrates
-- `app/services/procedure_implementation_bindings.py` to operate
-- against THIS table instead and drops the now-redundant, empty
-- (outside test debris, already cleaned) migration-53 table --
-- CLAUDE.md rule 2 forbids leaving two tables for the same relation
-- once the duplication is discovered, and it was discovered mid-session.

CREATE TABLE IF NOT EXISTS procedure_implementations (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id                    UUID NOT NULL,
    implementation_id               UUID NOT NULL REFERENCES implementations(id),
    resource_path                   TEXT,
    created_by                      TEXT,
    t_created                       TIMESTAMPTZ NOT NULL DEFAULT now(),
    role                            TEXT NOT NULL DEFAULT 'primary',
    implementation_version          INTEGER,
    implementation_version_constraint TEXT,
    supported_steps                 JSONB NOT NULL DEFAULT '[]',
    supported_capabilities          JSONB NOT NULL DEFAULT '[]',
    applicability                   JSONB NOT NULL DEFAULT '{}',
    interface_binding               JSONB NOT NULL DEFAULT '{}',
    evidence_refs                   JSONB NOT NULL DEFAULT '[]',
    status                          TEXT NOT NULL DEFAULT 'active',
    ingestion_context_id            UUID,
    t_valid                         TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid                       TIMESTAMPTZ
);

-- `procedure_implementations` also already exists, with an OLDER, simpler
-- shape, from db/39_structured_skill_ingestion.sql's own earlier
-- `CREATE TABLE IF NOT EXISTS` of the SAME table name (id, procedure_id,
-- implementation_id, resource_path, created_by, t_created only) --
-- unnoticed at the time because the shared dev DB already had the full
-- shape below from a pre-migration ad-hoc creation, making BOTH 39's and
-- this file's own CREATE TABLE no-ops there. On a genuinely fresh
-- bootstrap (a fresh CI database, this repo's own disposable-Postgres
-- migration-upgrade test), 39 runs first and wins, leaving this table
-- missing every column below -- these ADD COLUMN IF NOT EXISTS calls are
-- true no-ops everywhere this file's own CREATE TABLE already succeeded
-- (this shared dev DB included), and bring migration 39's older shape up
-- to this one wherever it did not.
ALTER TABLE procedure_implementations
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'primary',
    ADD COLUMN IF NOT EXISTS implementation_version INTEGER,
    ADD COLUMN IF NOT EXISTS implementation_version_constraint TEXT,
    ADD COLUMN IF NOT EXISTS supported_steps JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS supported_capabilities JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS applicability JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS interface_binding JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS evidence_refs JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS ingestion_context_id UUID,
    ADD COLUMN IF NOT EXISTS t_valid TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS t_invalid TIMESTAMPTZ;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_implementations_role_check') THEN
        ALTER TABLE procedure_implementations ADD CONSTRAINT procedure_implementations_role_check
            CHECK (role = ANY (ARRAY['primary','supporting','partial','verification']));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_implementations_status_check') THEN
        ALTER TABLE procedure_implementations ADD CONSTRAINT procedure_implementations_status_check
            CHECK (status = ANY (ARRAY['candidate','active','deprecated','disabled','quarantined']));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_procedure_implementations_procedure
    ON procedure_implementations(procedure_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_procedure_implementations_identity
    ON procedure_implementations(procedure_id, implementation_id, role) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_procedure_implementations_by_role
    ON procedure_implementations(procedure_id, role) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_procedure_implementations_reverse
    ON procedure_implementations(implementation_id) WHERE t_invalid IS NULL;


CREATE TABLE IF NOT EXISTS procedure_dependencies (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id          UUID NOT NULL,
    dependency_ref        TEXT NOT NULL,
    target_procedure_id   UUID,
    resolution_status     TEXT NOT NULL DEFAULT 'unresolved',
    source_path           TEXT,
    created_by            TEXT,
    t_created             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (procedure_id, dependency_ref)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_dependencies_resolution_status_check') THEN
        ALTER TABLE procedure_dependencies ADD CONSTRAINT procedure_dependencies_resolution_status_check
            CHECK (resolution_status = ANY (ARRAY['resolved','unresolved']));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_procedure_dependencies_source ON procedure_dependencies(procedure_id);
CREATE INDEX IF NOT EXISTS idx_procedure_dependencies_target
    ON procedure_dependencies(target_procedure_id) WHERE target_procedure_id IS NOT NULL;
