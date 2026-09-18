-- Migration 88: Trajectory semantic extraction + OpenHands/cross-harness
-- structural normalization (trajectory ingestion hardening task).
--
-- Next free migration number: 89.
--
-- WHAT THIS IS
--   Additive support for two things:
--   1. A canonical, harness-neutral event vocabulary layered ON TOP of the
--      existing provider-native `trace_events.event_type` (never replacing
--      it), plus a lossless raw-payload slot for sources (OpenHands) whose
--      native event shape doesn't fit the existing tool_input/tool_output
--      pair.
--   2. A versioned, re-runnable LLM semantic-extraction record per episode
--      (`trajectory_extractions`) and the explicit event-citation lineage
--      edge from an extraction run to the canonical objects (Goal/Claim/
--      Procedure/Implementation) it produced (`trajectory_extraction_objects`).
--   Plus a queryable quarantine table generalizing the existing
--   Claude-Code-only `.quarantine` sidecar-file mechanism to any adapter.
--
-- WHAT THIS IS NOT
--   Not a second ingestion system. `trace_events`/`agent_traces` remain the
--   one place raw normalized events live regardless of source; this
--   migration only widens them. Not a new knowledge-object table --
--   Goals/Claims/Procedures/Implementations are still `goals`/
--   `knowledge_nodes`/`procedures`/`implementations`; `trajectory_extractions`
--   is metadata ABOUT one extraction run, not a knowledge object itself.
--
-- Fresh-start rule: additive, nullable back-links / new tables, NO backfill.
-- Idempotent: same CREATE/ALTER ... IF NOT EXISTS idiom as the rest of db/.

-- ============================================================
-- 1. Canonical event vocabulary + lossless raw payload on trace_events.
--    `canonical_event_type` is free TEXT like `event_type` itself (not a
--    CHECK enum) -- the existing convention here is "a new source should
--    never need a migration just to extend the vocabulary" (see
--    12_trace_ingestion_pipeline.sql's own event_type comment).
--    `raw_event` is NULL for Claude Code rows (their native shape already
--    fits tool_input/tool_output); OpenHands and future adapters populate
--    it with the full native action/observation dict so nothing is lost.
-- ============================================================
ALTER TABLE trace_events
    ADD COLUMN IF NOT EXISTS canonical_event_type TEXT,
    ADD COLUMN IF NOT EXISTS raw_event JSONB;

CREATE INDEX IF NOT EXISTS idx_trace_events_canonical_type
    ON trace_events (canonical_event_type) WHERE canonical_event_type IS NOT NULL;

-- ============================================================
-- 2. Trajectory-header fields the task requires that agent_traces doesn't
--    carry today: model identity, token/cost accounting, and a
--    harness-specific catch-all (OpenHands instance_id, SWE-bench task
--    ref, resolved/unresolved eval outcome, etc.) that doesn't deserve its
--    own typed column.
-- ============================================================
ALTER TABLE agent_traces
    ADD COLUMN IF NOT EXISTS model TEXT,
    ADD COLUMN IF NOT EXISTS token_usage JSONB,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12,4),
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';

-- ============================================================
-- 3. Quarantine: a malformed record from any adapter (not just the
--    Claude-Code collector's file-sidecar path) lands here instead of
--    being silently dropped or crashing the batch.
-- ============================================================
CREATE TABLE IF NOT EXISTS quarantined_records (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type             TEXT NOT NULL,
    source_uri              TEXT,
    raw_content             TEXT NOT NULL,
    reason                  TEXT NOT NULL,
    schema_version_detected TEXT,
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved                BOOLEAN NOT NULL DEFAULT false,
    resolution_note         TEXT,

    visibility              visibility_level NOT NULL DEFAULT 'public',
    owner_id                TEXT,
    scope_type              TEXT,
    scope_entity_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_quarantined_records_unresolved
    ON quarantined_records (source_type, detected_at DESC) WHERE NOT resolved;

-- ============================================================
-- 4. trajectory_extractions -- one row per LLM semantic-extraction run
--    over one episode. Versioned and replayable: extraction_v1 and
--    extraction_v2 for the same episode both persist, neither overwrites
--    the other.
-- ============================================================
CREATE TABLE IF NOT EXISTS trajectory_extractions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id           UUID NOT NULL REFERENCES episodes(id),
    ingestion_context_id UUID,

    extractor_id         TEXT NOT NULL,
    model                TEXT NOT NULL,
    model_version        TEXT,
    prompt_version       TEXT NOT NULL,
    schema_version       TEXT NOT NULL,
    input_hash           TEXT NOT NULL,
    output_hash          TEXT,

    status               TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'completed', 'failed')),
    confidence_summary   JSONB NOT NULL DEFAULT '{}',
    escalated            BOOLEAN NOT NULL DEFAULT false,
    escalation_reason    TEXT,
    error                TEXT,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at         TIMESTAMPTZ,

    visibility           visibility_level NOT NULL DEFAULT 'public',
    owner_id             TEXT,
    scope_type           TEXT,
    scope_entity_id      TEXT,
    tenant_id            UUID
);

CREATE INDEX IF NOT EXISTS idx_trajectory_extractions_episode
    ON trajectory_extractions (episode_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trajectory_extractions_ctx
    ON trajectory_extractions (ingestion_context_id) WHERE ingestion_context_id IS NOT NULL;

-- ============================================================
-- 5. trajectory_extraction_objects -- the explicit citation/lineage edge:
--    "this extraction run produced/touched this canonical object, from
--    exactly these source events". Keeps citation bookkeeping out of
--    knowledge_nodes/procedures/implementations themselves.
-- ============================================================
CREATE TABLE IF NOT EXISTS trajectory_extraction_objects (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id     UUID NOT NULL REFERENCES trajectory_extractions(id) ON DELETE CASCADE,
    object_type       TEXT NOT NULL CHECK (object_type IN ('goal', 'claim', 'procedure', 'implementation')),
    object_id         UUID NOT NULL,
    event_refs        UUID[] NOT NULL DEFAULT '{}',
    epistemic_status  TEXT NOT NULL CHECK (epistemic_status IN ('observed', 'inferred', 'generalized')),
    confidence        REAL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT trajectory_extraction_objects_event_refs_chk CHECK (cardinality(event_refs) > 0)
);

CREATE INDEX IF NOT EXISTS idx_trajectory_extraction_objects_extraction
    ON trajectory_extraction_objects (extraction_id);
CREATE INDEX IF NOT EXISTS idx_trajectory_extraction_objects_object
    ON trajectory_extraction_objects (object_type, object_id);
