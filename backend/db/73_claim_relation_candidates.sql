-- Migration 73 (Ingestion + Knowledge hardening, Plan A / mechanism-9 &
-- A6 EquivalentClaims): a durable, reviewable queue for DETECTED
-- (never auto-applied) claim relations.
--
-- Next free migration number: 74.
--
-- WHY
--   claims.py::relate_claims already writes SUPERSEDES/CONTRADICTS edges,
--   and link_claims already writes the richer GENERAL_RELATIONS vocabulary
--   (SUPPORTS/REFINES/GENERALIZES/...) -- but ONLY when a caller already
--   knows the relation and asserts it explicitly. Nothing detects that two
--   differently-worded claims mean the same thing (duplicate belief/
--   evidence splitting) or conflict (a real contradiction) from their text.
--
--   Founder directive (2026-09-11): a detected relation must NOT
--   auto-resolve -- store it somewhere reviewable, decide the resolution
--   mechanism later. This table is exactly that: a candidate queue, not a
--   second source of truth for claim relations. `knowledge_nodes` /
--   `edges` remain the only place a REAL, resolved relation lives; a row
--   here becomes one only when a human (or a later-decided review
--   mechanism) confirms it and calls the EXISTING relate_claims/
--   link_claims -- this migration adds no new write path into the claim
--   graph itself.
--
-- WHAT THIS IS NOT
--   Not a second claim-relation edge type. Not auto-applied under any
--   circumstance -- there is no code path anywhere that reads `status`
--   and calls relate_claims/link_claims automatically; that decision is
--   explicitly deferred (see claim_equivalence.py's own module docstring).
--
-- Fresh-start rule: additive, idempotent, no backfill (there is nothing to
-- backfill -- this table only ever holds newly-detected candidates from
-- the point this migration lands forward).

CREATE TABLE IF NOT EXISTS claim_relation_candidates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- normalized so (a,b) and (b,a) are the same candidate: the service
    -- layer always stores the lexicographically smaller uuid as claim_a_id.
    claim_a_id          UUID NOT NULL,
    claim_b_id          UUID NOT NULL,
    relation            TEXT NOT NULL,
    confidence          DOUBLE PRECISION,
    detector            TEXT NOT NULL,
    detector_version    TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    resolution          TEXT,
    resolved_by         TEXT,
    resolved_at         TIMESTAMPTZ,
    created_by          TEXT,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (claim_a_id, claim_b_id, detector)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'claim_relation_candidates_relation_chk') THEN
        ALTER TABLE claim_relation_candidates ADD CONSTRAINT claim_relation_candidates_relation_chk
            CHECK (relation IN ('equivalent', 'contradicts', 'related', 'unrelated'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'claim_relation_candidates_status_chk') THEN
        ALTER TABLE claim_relation_candidates ADD CONSTRAINT claim_relation_candidates_status_chk
            CHECK (status IN ('pending', 'resolved', 'dismissed'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'claim_relation_candidates_order_chk') THEN
        ALTER TABLE claim_relation_candidates ADD CONSTRAINT claim_relation_candidates_order_chk
            CHECK (claim_a_id < claim_b_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_claim_relation_candidates_pending
    ON claim_relation_candidates (t_created) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_claim_relation_candidates_claim_a
    ON claim_relation_candidates (claim_a_id);
CREATE INDEX IF NOT EXISTS idx_claim_relation_candidates_claim_b
    ON claim_relation_candidates (claim_b_id);
