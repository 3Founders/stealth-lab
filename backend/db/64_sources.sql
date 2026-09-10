-- Migration 64 (Ingestion + Knowledge hardening, Plan A / G2 / audit B12):
-- the `sources` origin registry that `schema.md`'s `Source [V]` object has
-- always specified but nothing ever backed.
--
-- Next free migration number: 65.
--
-- WHY THIS EXISTS
--   `evidence.source_id` (migration 24) and `knowledge_nodes` provenance
--   have carried a `source` handle for a long time with the explicit note
--   "→ Source ... have no tables yet" (24_evidence.sql:110-114). Every
--   ingestion path recorded provenance ad hoc: `ingested_artifacts`
--   (document corpus), `claim_sources` (trace claims), `evidence_refs`
--   JSONB blobs (skill dedup). None of them is an addressable origin with
--   a stable identity and a reliability score kept SEPARATE from claim
--   belief (schema.md: "Source reliability is represented separately from
--   claim confidence").
--
-- WHAT THIS IS NOT
--   Not a second provenance graph. `ingested_artifacts` / `ingestion_runs`
--   (migrations 32/39) stay exactly as they are -- they become the
--   per-artifact / per-run detail that points AT a `sources` row via
--   `source_ref`. `claim_sources` / `episode_links` / `evidence` are
--   untouched. This table is the missing identity anchor the others
--   already assume.
--
-- Fresh-start rule: additive, nullable FKs elsewhere, NO backfill. Legacy
-- rows keep their ad-hoc provenance until their writers are rewired.
--
-- Idempotent: CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS, same
-- idiom as every other migration in db/.

-- ============================================================
-- source_kind -- schema.md's own enumeration of Source origins. TEXT+CHECK
-- rather than a pg enum: the list is spec-authored but has visibly grown
-- over the project's life (benchmark, community review, external research
-- were later additions), so a CHECK we can widen in a one-line migration
-- beats ALTER TYPE ... ADD VALUE's transaction constraints.
-- ============================================================
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'source_kind') THEN
        CREATE TYPE source_kind AS ENUM (
            'agent_execution',   -- a recorded StealthLab / agent run
            'human_action',      -- a person did something we recorded
            'document',          -- SKILL.md, AGENTS.md, CLAUDE.md, runbook, article, spec
            'repository',        -- a git repo / code tree
            'database',          -- a queried datastore
            'api',               -- an external API response
            'test',              -- a test / test report
            'benchmark',         -- a benchmark run / result set
            'community_review',  -- peer review / discussion
            'external_research', -- a paper / third-party study
            'sensor',            -- a measurement device
            'system_event'       -- infrastructure / platform event
        );
    END IF;
END $$;

-- ============================================================
-- sources -- one row per distinct origin.
--
-- IDENTITY (V4-hardening §2): a Source is identified by
--   normalized locator + publisher/owner + source type
-- so the same document fetched twice reuses one row, and two different
-- documents at the same URL over time are still one Source with multiple
-- Artifact snapshots hanging off it (migration 32's ingested_artifacts
-- already versions by content_hash).
--
-- RELIABILITY is deliberately its own column pair, never folded into
-- claim belief: `reliability_score` answers "how much do we trust THIS
-- origin" (a preprint vs a peer-reviewed paper vs our own verified run),
-- `reliability_method` names how that number was set. NULL = unassessed;
-- consumers must treat unassessed as "unknown", never as "trusted".
-- ============================================================
CREATE TABLE IF NOT EXISTS sources (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_type         source_kind NOT NULL,

    -- normalized, canonical locator: a resolved URL, a repo slug + path,
    -- an execution id, a benchmark id. Normalization (lowercasing host,
    -- stripping tracking params, resolving redirects) is the writer's
    -- job; this column stores the already-normalized string.
    locator             TEXT NOT NULL,
    publisher           TEXT,                 -- owner / org / author when known
    title               TEXT,
    license             TEXT,                 -- SPDX id or free text

    -- reliability of the ORIGIN, not of any claim it feeds.
    reliability_score   REAL CHECK (reliability_score IS NULL
                                    OR (reliability_score >= 0 AND reliability_score <= 1)),
    reliability_method  TEXT,

    -- how this origin was first reached, for audit ("who told us about
    -- this source"). Free text: a CLI arg, a corpus manifest id, a user id.
    discovered_via      TEXT,

    -- Birth discipline (V0 gate): every write carries scope + provenance.
    -- Nullable COLUMN / required-at-boundary, same split migration 21
    -- established -- the service owns the error message.
    provenance          provenance_source,
    created_by          TEXT,

    -- Ticket 09 pair rule: BOTH columns on every new table
    -- (access.py::visibility_predicate() breaks otherwise). Invariant #9
    -- (private evidence / private origin cannot silently become public).
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    tenant_id           UUID,                 -- ORG visibility boundary (migration 41/47 idiom)
    scope_type          TEXT,
    scope_entity_id     TEXT,

    -- Bi-temporal trio, same as evidence / procedure_extractors: t_valid /
    -- t_invalid = true-in-the-world (t_invalid = a source we no longer
    -- consider a valid origin, e.g. retracted paper), t_created = when we
    -- learned it. Supersede-by-append; the one legal UPDATE is the
    -- t_invalid tombstone (no trigger here yet -- add with the first real
    -- retraction path, same sequencing note migration 24 used).
    t_valid             TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid           TIMESTAMPTZ,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- identity key (V4-hardening §2). Two rows may share a locator only if
    -- they differ in publisher or type (e.g. a fork vs upstream).
    UNIQUE (source_type, locator, publisher)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_sources') THEN
        ALTER TABLE sources ADD CONSTRAINT scope_type_chk_sources
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_sources_locator
    ON sources (source_type, locator) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_sources_publisher
    ON sources (publisher) WHERE publisher IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sources_scope
    ON sources (scope_type, scope_entity_id);
CREATE INDEX IF NOT EXISTS idx_sources_tenant
    ON sources (tenant_id) WHERE tenant_id IS NOT NULL;

-- ============================================================
-- Forward links from the existing provenance side-tables to the new
-- identity anchor. Additive + nullable: a legacy row keeps NULL until its
-- writer is rewired. No FK enforcement change to existing columns.
-- ============================================================
ALTER TABLE ingested_artifacts
    ADD COLUMN IF NOT EXISTS source_ref UUID REFERENCES sources(id);

CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_source_ref
    ON ingested_artifacts (source_ref) WHERE source_ref IS NOT NULL;
