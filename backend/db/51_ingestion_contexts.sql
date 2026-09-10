-- Migration 51 (Ingestion + Knowledge hardening, Plan A / G1 / audit B3):
-- IngestionContext -- the one durable row every ingestion opens, that
-- every object it derives can be traced back to.
--
-- Next free migration number: 52.
--
-- WHY THIS EXISTS
--   Today the "who / under what scope / by which extractor / from what
--   input" facts are scattered: `ingestion_runs` (migration 32) has a
--   per-run manifest but no actor / workspace / scope; `ingested_artifacts`
--   is per-artifact; `extractor_version` is a lone TEXT column on four
--   different tables and is never bundled. No derived object can answer
--   V4-hardening §32's provenance invariant in one traversal.
--
-- WHAT THIS IS NOT
--   Not a replacement for `ingestion_runs`. A run is the OPERATIONAL unit
--   (one adapter sweep, its metrics). A context is the PROVENANCE unit
--   (one logical ingestion request with an actor, a workspace, a scope, a
--   classification, an extractor identity). `ingestion_runs.run_id` and
--   `ingestion_contexts.id` are 1:1 in the common case; the split exists
--   so a single long-lived context (e.g. a streaming trace ingestion) can
--   span several runs, and a run kicked off with no request context still
--   works (context_ref NULL).
--
-- Fresh-start rule: additive, nullable back-links, NO backfill.
-- Idempotent: same CREATE/ALTER ... IF NOT EXISTS idiom as the rest of db/.

-- ============================================================
-- ingestion_contexts -- V4-hardening §A1 field list, one column each.
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_contexts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- WHAT is being ingested.
    source_ref          UUID REFERENCES sources(id),   -- the origin (migration 50)
    source_type         TEXT NOT NULL,                 -- 'skill_md' | 'trace' | 'benchmark' | ...
    source_uri          TEXT,                          -- resolved locator at ingestion time
    source_hash         TEXT,                          -- content hash of exactly what was consumed

    -- WHO / WHERE.
    actor_id            TEXT NOT NULL,                  -- user / agent / worker that opened it
    workspace_id        UUID,                          -- registered_workspaces(id) when hosted
    environment_id      TEXT,                          -- runtime environment when relevant

    -- UNDER WHAT SCOPE the derived objects are born. V0 vocabulary.
    scope_type          TEXT NOT NULL,
    scope_entity_id     TEXT,
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    tenant_id           UUID,

    -- HOW it was screened and by which extractor.
    classification      TEXT,                          -- classification.DataClass value
    extractor_id        TEXT NOT NULL,                 -- e.g. 'skill_md_ingestion'
    extractor_version   TEXT NOT NULL,                 -- e.g. 'skill_md_grounded_v5'

    -- OPERATIONAL link (nullable -- see header).
    run_ref             UUID,                          -- ingestion_runs(run_id)

    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'completed', 'failed', 'rejected')),

    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_ingestion_contexts') THEN
        ALTER TABLE ingestion_contexts ADD CONSTRAINT scope_type_chk_ingestion_contexts
            CHECK (scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity'));
    END IF;
    -- A non-global scope must say what it points at (V0 gate rule, engine
    -- teeth for direct-SQL writers).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ingestion_contexts_scope_entity_chk') THEN
        ALTER TABLE ingestion_contexts ADD CONSTRAINT ingestion_contexts_scope_entity_chk
            CHECK (scope_type = 'global' OR scope_entity_id IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_ingestion_contexts_actor
    ON ingestion_contexts (actor_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_contexts_source_ref
    ON ingestion_contexts (source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ingestion_contexts_run_ref
    ON ingestion_contexts (run_ref) WHERE run_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ingestion_contexts_hash
    ON ingestion_contexts (source_hash) WHERE source_hash IS NOT NULL;

-- ============================================================
-- Back-link every object an ingestion can derive to the context that
-- produced it. Additive + nullable: a legacy row keeps NULL. No FK on the
-- derived side (same reasoning migration 12/32 give for trace_id /
-- procedure_id -- a context row must stay queryable forever even after
-- its derived rows are tombstoned).
-- ============================================================
ALTER TABLE ingested_artifacts
    ADD COLUMN IF NOT EXISTS ingestion_context_id UUID;
ALTER TABLE observations
    ADD COLUMN IF NOT EXISTS ingestion_context_id UUID;
ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS ingestion_context_id UUID;
ALTER TABLE evidence
    ADD COLUMN IF NOT EXISTS ingestion_context_id UUID;

CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_ctx
    ON ingested_artifacts (ingestion_context_id) WHERE ingestion_context_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_observations_ctx
    ON observations (ingestion_context_id) WHERE ingestion_context_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_procedures_ctx
    ON procedures (ingestion_context_id) WHERE ingestion_context_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_ctx
    ON evidence (ingestion_context_id) WHERE ingestion_context_id IS NOT NULL;

-- knowledge_nodes (claims) carry the same back-link. Column added here so
-- the claim-derivation path can stamp it; NULL for every non-ingested
-- node.
ALTER TABLE knowledge_nodes
    ADD COLUMN IF NOT EXISTS ingestion_context_id UUID;
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_ctx
    ON knowledge_nodes (ingestion_context_id)
    WHERE ingestion_context_id IS NOT NULL;
