-- Migration 39: versioned structured-skill corpus metadata.
--
-- This is additive to the existing SourceArtifact -> Procedure substrate.
-- It deliberately does not introduce a Skill runtime object and does not
-- materialize generic source steps as task_nodes. TaskNodes remain runtime
-- instantiations of abstract Procedure steps.

ALTER TABLE ingested_artifacts
    ADD COLUMN IF NOT EXISTS source_id TEXT,
    ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS license_metadata JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS bundle_hash TEXT,
    ADD COLUMN IF NOT EXISTS resource_manifest JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS parsed_metadata JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS dependencies JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS requirements JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_source_id
    ON ingested_artifacts(source_id, commit);
CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_bundle_hash
    ON ingested_artifacts(bundle_hash) WHERE bundle_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingestion_source_snapshots (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id         TEXT NOT NULL,
    repo_url          TEXT NOT NULL,
    resolved_commit   TEXT NOT NULL CHECK (resolved_commit ~ '^[0-9a-f]{40}$'),
    retrieved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    license_metadata  JSONB NOT NULL DEFAULT '{}',
    source_path       TEXT,
    content_hash      TEXT NOT NULL,
    expected_format   TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'candidate'
                      CHECK (status IN ('candidate', 'blocked', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_source_snapshots_identity
    ON ingestion_source_snapshots(source_id, resolved_commit, COALESCE(source_path, ''));

CREATE TABLE IF NOT EXISTS procedure_dependencies (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id          UUID NOT NULL,
    dependency_ref        TEXT NOT NULL,
    target_procedure_id   UUID,
    resolution_status     TEXT NOT NULL DEFAULT 'unresolved'
                          CHECK (resolution_status IN ('resolved', 'unresolved')),
    source_path           TEXT,
    created_by            TEXT,
    t_created             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (procedure_id, dependency_ref)
);

CREATE INDEX IF NOT EXISTS idx_procedure_dependencies_source
    ON procedure_dependencies(procedure_id);
CREATE INDEX IF NOT EXISTS idx_procedure_dependencies_target
    ON procedure_dependencies(target_procedure_id)
    WHERE target_procedure_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS procedure_implementations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id          UUID NOT NULL,
    implementation_id     UUID NOT NULL REFERENCES implementations(id),
    resource_path         TEXT NOT NULL,
    created_by            TEXT,
    t_created             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (procedure_id, implementation_id)
);

CREATE INDEX IF NOT EXISTS idx_procedure_implementations_procedure
    ON procedure_implementations(procedure_id);
