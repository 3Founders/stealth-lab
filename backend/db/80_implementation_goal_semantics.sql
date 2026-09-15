-- Migration 80: Implementation goal/expected-outcome/verification-contract
-- semantics (vertical slice of the founder's ingestion-hardening directive
-- 2026-09-15, scoped to skill-package bundled-script Implementations only --
-- see app/services/implementation_goals.py's own module docstring for the
-- full scoping rationale and known limitations).
--
-- WHY
--   `implementations` (migration 33) already models identity, method
--   (kind/provider), access (locator/invocation), and requirements -- but
--   has no column anywhere for WHAT normalized goal a mechanism satisfies,
--   what its expected post-success state is, or how success is proven.
--   `verification_status` (migration 33) only records WHETHER something
--   has been verified, never HOW -- a real gap when a caller wants to
--   choose between two Implementations that both satisfy the same
--   `ProcedureStep.goal`.
--
-- SCOPE, stated honestly: this migration adds the columns for the WHOLE
-- Implementation object (any writer may populate them), but the FIRST
-- real writer wired to populate them (this same change) is
-- `_persist_package_relations()` in skill_ingestion.py, covering only
-- skill-package bundled scripts -- not implementation_registry.py's or
-- hierarchy.py's own writers, and not a full LLM-driven semantic
-- decomposition pass. `classification` distinguishes "never even
-- considered" (existing rows, backfilled below) from "a real writer set
-- these deterministically" (`heuristic`) from "nothing else applied"
-- (`unclassified`, the default for a genuinely-uninformative resource).
--
-- Next free migration number: 81.

ALTER TABLE implementations
    -- Normalized goal string (ProcedureStep.goal <-> Implementation.goal,
    -- the founder directive's own minimal goal model) -- free text, not an
    -- enum, so the real vocabulary can grow without a migration per new
    -- goal. NULL is honest: "no goal could be determined," never a
    -- fabricated default.
    ADD COLUMN IF NOT EXISTS goal TEXT,

    -- Optional structured elaboration of `goal` (parameters, scope) --
    -- JSONB so a writer with nothing structured to add can simply omit it.
    ADD COLUMN IF NOT EXISTS goal_spec JSONB,

    -- The semantic state/result intended after success. NEVER fabricated
    -- for a row where the source gives no evidence of it -- left NULL in
    -- that case, exactly per the founder directive's own "do not fabricate
    -- expected_outcome" rule.
    ADD COLUMN IF NOT EXISTS expected_outcome JSONB,

    -- {"type": "deterministic"|"test"|"external_system"|"llm"|"human", ...}
    -- -- how success is actually proven, distinct from `verification_status`
    -- (whether anyone has verified it). Application-layer validated (see
    -- implementation_goals.py::VERIFICATION_CONTRACT_TYPES), not a DB CHECK,
    -- matching this table's existing convention of plain TEXT + app-level
    -- enums for open-ended-but-real vocabularies (status/verification_status
    -- above use the same pattern).
    ADD COLUMN IF NOT EXISTS verification_contract JSONB,

    -- Bookkeeping for the backfill below and for future enrichment passes:
    -- 'needs_enrichment' (existing rows this migration backfills -- real
    -- data, never classified), 'unclassified' (a row a real writer looked
    -- at and found no deterministic signal for -- the honest default for
    -- NEW rows), 'heuristic' (a deterministic writer populated goal/
    -- verification_contract), 'llm_classified' (reserved for a future real
    -- semantic-decomposition pass -- no writer sets this yet).
    ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'unclassified';

-- Postgres has no ADD CONSTRAINT IF NOT EXISTS -- guarded manually so this
-- migration stays idempotent (this repo's own hard rule) on a re-run.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'implementations_classification_check'
    ) THEN
        ALTER TABLE implementations
            ADD CONSTRAINT implementations_classification_check
            CHECK (classification IN ('unclassified', 'needs_enrichment', 'heuristic', 'llm_classified'));
    END IF;
END $$;

-- Real, safe backfill (founder directive 2026-09-15 explicitly overrides
-- CLAUDE.md's default fresh-start/no-backfill rule for this initiative):
-- every row that existed before this migration ran is stamped
-- 'needs_enrichment' -- distinguishing "real historical data nobody has
-- ever classified" from a fresh row a writer just looked at and found
-- nothing for ('unclassified'). No goal/expected_outcome/verification_contract
-- is fabricated for any of them; those three columns stay NULL until a
-- real enrichment pass (out of this slice's scope) or a caller supplies
-- real evidence.
UPDATE implementations SET classification = 'needs_enrichment'
WHERE classification = 'unclassified';
