-- Migration 89: Claim-conditioned applicability judgment cache.
--
-- WHY THIS EXISTS
--   applicability_judge.py's ApplicabilityJudge providers (MockJudge/
--   LLMJudge/RemoteHTTPJudge) are the second-stage NLI/JEV contextual
--   reasoning layer claim_conditioned_retrieval.py runs on top of the
--   existing hard-constraint cascade survivors. A judge call (especially
--   RemoteHTTPJudge) is the most expensive step in that pipeline; re-
--   running it for the SAME (goal, candidate, relevant-Claims-set, judge)
--   combination is pure waste.
--
--   Modeled directly on migration 78's claim_extraction_cache pattern:
--   keyed on every input that could change the answer. `claim_ids_hash`
--   (app.services.applicability_judge.stable_claim_ids_hash) is a stable
--   hash of the sorted (claim_id, version) pairs actually retrieved for
--   this candidate -- if a Claim is superseded/added/removed/re-versioned,
--   the relevant-Claims set for a re-run changes, the hash changes, and
--   the old cached judgment is simply never looked up again. This IS the
--   cache-invalidation mechanism (product spec Sec 13) -- no separate
--   invalidation logic needed.
--
-- Fresh-start rule: additive, NO in-migration backfill.
-- Idempotent: CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS applicability_judgment_cache (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_hash           TEXT NOT NULL,
    candidate_id        TEXT NOT NULL,
    candidate_version   TEXT NOT NULL,
    claim_ids_hash      TEXT NOT NULL,
    judge_model         TEXT NOT NULL,
    judge_model_version TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    judgment            JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (
        goal_hash, candidate_id, candidate_version, claim_ids_hash,
        judge_model, judge_model_version, prompt_version
    )
);

CREATE INDEX IF NOT EXISTS idx_applicability_judgment_cache_lookup
    ON applicability_judgment_cache (
        goal_hash, candidate_id, candidate_version, claim_ids_hash,
        judge_model, judge_model_version, prompt_version
    );
