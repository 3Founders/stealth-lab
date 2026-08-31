-- Migration 32 (Lane CORE-A): source provenance + run manifest for the
-- Global Procedural Library ingestion compiler (prompts.md Phase 2, §7,
-- §11, §14; architecture audit §E/§P).
--
-- Next free number: 32 (31 is highest).
--
-- What this is NOT: not `global_procedures`, not `ingested_tasks`, not a
-- parallel procedure store. Public procedural knowledge is still compiled
-- into the EXISTING `procedures` / `task_nodes` substrate. These two
-- tables are the provenance + freshness SIDE record the brief asks to
-- retain (content_hash / source_version / first_seen / last_seen) plus
-- the per-run ingestion manifest (§14). Every real procedure row an
-- ingestion produces still lands via capture_procedure() /
-- supersede_procedure() exactly as any other caller's would.
--
-- No FK from ingested_artifacts.procedure_id/procedure_row_id to
-- procedures: same reasoning 12_trace_ingestion_pipeline.sql gives for
-- trace_id -- a procedure version row can be tombstoned (t_invalid) or a
-- logical procedure retired while the artifact record that fed it must
-- stay queryable forever.
--
-- Idempotent: safe to re-run, same idiom as every other migration here.

-- ============================================================
-- 1. ingested_artifacts -- one row per (source, uri, content_hash) seen.
--
-- A re-ingestion of byte-identical content is a no-op that only bumps
-- last_seen (the UNIQUE constraint below makes the upsert cheap). A
-- changed content_hash for the same (source_type, uri) is a NEW row, and
-- the compiler's staleness path pairs it with a supersede_procedure()
-- call -- the prior artifact row and its procedure_row_id stay exactly as
-- they were.
-- ============================================================
CREATE TABLE IF NOT EXISTS ingested_artifacts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_type       TEXT NOT NULL,        -- 'skill_md_dir' | 'skill_md_repo' | (later) 'github_workflow' | ...
    uri               TEXT NOT NULL,        -- stable locator: file path, raw GitHub URL, ...
    repository        TEXT,                 -- owner/name when the source is a repo
    path              TEXT,                 -- path within the repository
    commit            TEXT,                 -- commit SHA / ref when known (the brief's "source_version")

    content_hash      TEXT NOT NULL,        -- sha256 of the fetched bytes
    extractor_version TEXT,                 -- "name@version" of the extractor that consumed it

    -- The procedure this artifact fed. procedure_id is the stable handle
    -- across a version chain; procedure_row_id is the exact version row
    -- produced/updated for THIS content_hash. Both nullable: a rejected
    -- or duplicate candidate produces an artifact record with neither.
    procedure_id      UUID,
    procedure_row_id  UUID,

    run_id            UUID,                 -- the ingestion_runs row this was observed in

    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Standard bitemporal columns, same as every other core table.
    t_valid           TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid         TIMESTAMPTZ,
    t_created         TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired         TIMESTAMPTZ,

    -- Ticket 09: owner_id + visibility on every new table.
    owner_id          TEXT,
    visibility        visibility_level NOT NULL DEFAULT 'public',

    UNIQUE (source_type, uri, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_content_hash
    ON ingested_artifacts(content_hash);
CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_procedure
    ON ingested_artifacts(procedure_id) WHERE procedure_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_source
    ON ingested_artifacts(source_type, uri);

-- ============================================================
-- 2. ingestion_runs -- one row per compiler invocation (§14 manifest).
--
-- `metrics` holds the brief's exact shape:
--   {"sources_seen", "artifacts_seen", "candidates", "accepted",
--    "duplicates", "rejected", "stale", "errors"}
-- kept as a single JSONB blob (not a wall of columns) for the same reason
-- procedures.verification_stats is -- this is evolving instrumentation,
-- not a fixed contract. `source_spec` records what was asked for
-- (the CLI args / adapter config) so a run is reproducible from its row.
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    metrics      JSONB NOT NULL DEFAULT '{}',
    source_spec  JSONB NOT NULL DEFAULT '{}',
    created_by   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_started
    ON ingestion_runs(started_at DESC);
