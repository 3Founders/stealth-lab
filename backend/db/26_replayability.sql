-- Migration 26 (Lane CORE-A, Band 2.8): end-to-end replayability -- the
-- provenance chain that lets every derived object be traced back to the
-- raw trace_events it was extracted from, and regenerated.
--
-- Next free number: 25 is highest. Never edit an applied migration --
-- scripts/migrate.py checksums them and a mismatch is a hard error by
-- this repo's own design (same header discipline as 20).
--
-- Idempotent: safe to re-run, same idiom as every other migration here.
-- Fresh-start compliant: a new join table only, no backfills, no
-- legacy shims -- claims promoted before this migration simply have no
-- claim_sources rows, exactly like observations before 14 had none.
--
-- ============================================================
-- THE PROVENANCE CHAIN this completes (spec.md REPLAYABILITY:
-- "Claim C17 was produced by extractor version X from trace E42"):
--
--   trace_events                      raw layer; schema_version stamp
--       ^  |
--       |  observation_events          obs -> event links (migration 14)
--       |  v
--   observations                       extractor_kind / extractor_name /
--       ^  |                           code_version / model_id /
--       |  claim_sources   <-- THIS    prompt_hash / decoding_params_hash
--       |  v                           stamps, component-wise (14's own
--   claims (knowledge_nodes            reasoning: one opaque hash would
--           node_type='claim')         destroy "which came from model X")
--       properties->>'extraction_version' composite stamp + epistemic_status
--       |
--   procedures                         extracted_by = "name@version"
--                                      (migration 20) + source_episode_ids
--
-- Before this migration the chain had ONE broken link: a claim carried
-- its extractor version but NOT which observation(s) produced it, so
-- "rebuild claim X from its raw events" was unanswerable -- nothing to
-- join on. This table is that link.
--
-- A dedicated join table, not JSONB keys on the claim's properties,
-- same reasoning 14_observations.sql gives for observation_events over
-- an array column: real FKs in both directions (a deleted observation
-- cannot leave a dangling provenance pointer), per-link metadata has a
-- home, and the reverse direction gets a plain index instead of GIN.
-- ============================================================

CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id       UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    observation_id UUID NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    t_created      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (claim_id, observation_id)
);

-- Reverse traversal ("which claims cite this observation") is the
-- load-bearing direction for replay: regenerate-from-raw walks
-- events -> observations -> claims. The primary key above covers the
-- forward direction (claim -> its sources), same split as
-- idx_observation_events_event in migration 14.
CREATE INDEX IF NOT EXISTS idx_claim_sources_observation
    ON claim_sources(observation_id);
