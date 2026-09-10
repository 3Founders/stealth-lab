-- Migration 53 (MCP hardening B23/B24): the real Procedure<->Implementation
-- many-to-many relation.
--
-- Next free number: 53 (52 is highest).
--
-- WHY A NEW TABLE, NOT A NEW REGISTRY: `implementations` (migration 33)
-- is already the single durable Implementation identity table --
-- CLAUDE.md rule 2 forbids a second one, and this migration adds none.
-- `implementation_tasks` (migration 33) already links Implementations to
-- `task_nodes` -- but NOT to `procedures`, which is the reusable
-- action/skill object this whole hardening pass is built around (rule 8:
-- "TaskGraph/PlanNode are runtime orchestration state; Procedure is the
-- reusable action/skill object"). A step's `implementation_hint` field is
-- unstructured, advisory JSON with no FK -- real for a hint, not a real
-- relation. This table is that missing relation, and nothing else.
--
-- NAMING NOTE (found live, while writing this migration): a table
-- literally named `procedure_implementations` ALREADY EXISTS in the real
-- database, with 482 real rows and a DIFFERENT schema (id, procedure_id,
-- implementation_id, resource_path, created_by, t_created) -- it is what
-- `app/services/publication.py`'s `_IMPL_SELECT` genuinely queries
-- (confirmed correct, not dead code as an earlier audit pass in this
-- session speculated). It has NO committed migration file anywhere in
-- backend/db/ -- a real, separate, flagged gap (a fresh environment
-- provisioned from this repo's migrations alone would never create it).
-- This migration does NOT touch that table, rename it, or attempt to
-- backfill its missing migration (out of this hardening pass's scope --
-- publication/ingestion, not MCP/execution). The new relation this B23
-- gate needs is named `procedure_implementation_bindings` instead, to
-- avoid any collision with that existing table.
--
--   procedure_implementation_bindings
--     procedure_id          -- the STABLE family id (procedures.procedure_id),
--                              matching how procedure identity is already
--                              referenced everywhere else in this schema
--                              (e.g. execution_runs.procedure_id) -- never
--                              the version-specific row id.
--     procedure_version     -- NULL = applies to any version of this
--                              Procedure family; a real integer = pinned
--                              to exactly that version. Both are legitimate,
--                              real states (B23: "environment-specific
--                              Implementations... verification-only
--                              Implementations").
--     implementation_id     -- FK to implementations(id) -- the exact,
--                              versioned Implementation row (never a
--                              (name, provider) pair without a version,
--                              matching implementations' own identity).
--     role                  -- primary | supporting | partial | verification
--                              (B23's own vocabulary, verbatim).
--     supported_steps       -- JSONB array of step orders this
--                              Implementation actually covers. Empty
--                              array is a real, honest state ("applies to
--                              the whole Procedure, not narrowed to
--                              specific steps") -- NOT the same as "covers
--                              zero steps"; the resolver (procedure_
--                              implementations.py) treats empty as
--                              "unrestricted", never as "matches nothing".
--     applicability         -- JSONB, same free-form condition shape
--                              procedures.scope/exclusions already use --
--                              no new condition language invented here.
--     interface_binding     -- JSONB: how this Procedure's inputs map to
--                              this Implementation's input_schema. Real,
--                              caller-authored structure; storage does not
--                              police its shape (same posture
--                              implementations.auth_requirements already
--                              has, per that table's own migration).
--     evidence_refs         -- JSONB array of Evidence ids that concern
--                              how well THIS RELATION (not the Procedure
--                              or Implementation alone) performs --
--                              B23: "Evidence belongs to the relation
--                              when it concerns how well a particular
--                              Implementation realizes a particular
--                              Procedure."
--     status                -- candidate | active | deprecated | disabled.
--                              A SEPARATE lifecycle axis from either
--                              endpoint's own status -- a relation can be
--                              deprecated while both the Procedure and the
--                              Implementation remain active (e.g. a better
--                              relation superseded it), and vice versa.
--                              Always born 'candidate' (register()'s own
--                              "nothing is born trusted" posture, kept).
--
-- No numeric capability/coverage score column exists here -- B23's own
-- rule ("Do not invent numeric coverage/quality scores unless they come
-- from recorded evaluation") is already satisfied by the EXISTING
-- Wilson-lower-bound machinery over `evidence` rows keyed by
-- `evidence_refs` above; a second, made-up score column would violate
-- that rule, not satisfy it.
--
-- Discipline matches mig 33/50/51/52: TEXT + CHECK, DO $$-guarded
-- constraints, additive + idempotent.

CREATE TABLE IF NOT EXISTS procedure_implementation_bindings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id        UUID NOT NULL,
    procedure_version   INTEGER,
    implementation_id   UUID NOT NULL REFERENCES implementations(id),
    role                TEXT NOT NULL,
    supported_steps     JSONB NOT NULL DEFAULT '[]',
    applicability        JSONB NOT NULL DEFAULT '{}',
    interface_binding    JSONB NOT NULL DEFAULT '{}',
    evidence_refs        JSONB NOT NULL DEFAULT '[]',
    status               TEXT NOT NULL DEFAULT 'candidate',
    created_by           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_implementation_bindings_role_chk') THEN
        ALTER TABLE procedure_implementation_bindings ADD CONSTRAINT procedure_implementation_bindings_role_chk
            CHECK (role IN ('primary','supporting','partial','verification'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_implementation_bindings_status_chk') THEN
        ALTER TABLE procedure_implementation_bindings ADD CONSTRAINT procedure_implementation_bindings_status_chk
            CHECK (status IN ('candidate','active','deprecated','disabled'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_implementation_bindings_version_chk') THEN
        ALTER TABLE procedure_implementation_bindings ADD CONSTRAINT procedure_implementation_bindings_version_chk
            CHECK (procedure_version IS NULL OR procedure_version >= 1);
    END IF;
END $$;

-- A given (procedure family [+ optional pinned version], implementation,
-- role) pair is registered at most once -- calling submit_implementation
-- again for the identical relation must reuse it, never duplicate it.
-- Postgres treats NULLs as distinct for uniqueness purposes, so a
-- version-agnostic relation (procedure_version IS NULL) and a
-- version-pinned one for the SAME implementation/role can coexist as
-- two real, different rows -- deliberate, matching B23's own "candidate
-- Implementations... environment-specific Implementations" distinction.
CREATE UNIQUE INDEX IF NOT EXISTS idx_procedure_implementation_bindings_identity
    ON procedure_implementation_bindings(procedure_id, procedure_version, implementation_id, role);

CREATE INDEX IF NOT EXISTS idx_procedure_implementation_bindings_procedure
    ON procedure_implementation_bindings(procedure_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_procedure_implementation_bindings_implementation
    ON procedure_implementation_bindings(implementation_id);
