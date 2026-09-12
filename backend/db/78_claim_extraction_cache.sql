-- Migration 78: Claim extraction result cache (Phase 10 cost control).
--
-- WHY THIS EXISTS
--   app/services/skill_ingestion.py's own exact-content-hash short-circuit
--   (compile_skill_artifact's "unchanged" branch) already skips re-running
--   claim extraction when the SAME artifact (same source_type + uri) is
--   re-ingested byte-identical. It does NOT cover a DIFFERENT artifact
--   (different uri/repository) whose BODY happens to be byte-identical --
--   e.g. the same SKILL.md copied into two repositories, or re-ingested
--   under a bumped extractor_version that intentionally forces a fresh
--   pass everywhere else in the pipeline but gains nothing from re-paying
--   the SAME LLM extraction call for content already seen.
--
--   Keyed on (content_hash, prompt_version, schema_version, model): a
--   real cache-invalidation key, not just content_hash alone -- a prompt
--   or schema bump (app.services.claim_extraction.
--   CLAIM_EXTRACTION_PROMPT_VERSION / _SCHEMA_VERSION) or a different
--   model must never silently serve a stale cached result.
--
-- WHAT IS CACHED
--   The validated `list[ClaimCandidate]` result of ONE extraction call
--   (or the whole chunked sequence for a long document), serialized as
--   JSONB. Caching an EMPTY result ([]) is deliberate and valuable: "this
--   exact content, under this exact prompt/schema/model, has zero
--   independently meaningful claims" is itself worth not re-paying for.
--
-- Fresh-start rule: additive, NO in-migration backfill.
-- Idempotent: CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS claim_extraction_cache (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash      TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    model             TEXT NOT NULL,
    candidates        JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (content_hash, prompt_version, schema_version, model)
);

CREATE INDEX IF NOT EXISTS idx_claim_extraction_cache_lookup
    ON claim_extraction_cache (content_hash, prompt_version, schema_version, model);
