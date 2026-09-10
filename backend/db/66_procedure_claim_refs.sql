-- Migration 66 (Ingestion + Knowledge hardening, Plan A / G8 / audit B4+B5):
-- ProcedureClaimRef -- the typed, role-bearing relation between a
-- Procedure version and a Claim, replacing the JSON-blob
-- `procedures.preconditions[*].claim_id` as the primary lookup.
--
-- Next free migration number: 67.
--
-- WHY THIS EXISTS
--   `claim_impact.find_procedures_referencing_claim` does
--   `WHERE preconditions @> '[{"claim_id": ...}]'` -- a JSONB containment
--   scan over ONE of the procedure's several claim-bearing arrays, with no
--   notion of WHY the claim is referenced. So a claim flip marks every
--   referencing procedure stale regardless of whether the claim is a hard
--   precondition or merely explanatory rationale (V4-hardening
--   "CLAIM INVALIDATION": explanatory roles must not auto-invalidate).
--
-- WHAT THIS IS NOT
--   Not claims-inside-procedures. The relation lives here; the Procedure
--   version and the Claim stay independent, independently searchable,
--   independently versioned. `procedures.preconditions` JSONB stays as-is
--   for compatibility reads during migration -- a backfill (out of band,
--   fresh-start rule) copies existing `preconditions[*].claim_id` rows in
--   with role='PRECONDITION'.
--
-- Fresh-start rule: additive, NO in-migration backfill.
-- Idempotent: CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS procedure_claim_refs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The Procedure this ref belongs to. `procedure_id` is the stable
    -- logical handle (survives versioning); `procedure_version` pins the
    -- exact version row that authored the ref (V4-hardening: "Use the
    -- current Procedure version semantics correctly"). Both recorded, same
    -- pair `evidence` uses for procedure targets.
    procedure_id            UUID NOT NULL,
    procedure_version       INTEGER NOT NULL CHECK (procedure_version >= 1),

    -- The Claim. `knowledge_nodes` rows are id-addressed (no version
    -- column); the logical claim version lives in properties JSONB, so
    -- `claim_version` here is a nullable INT snapshot of that logical
    -- version at authoring time, for staleness reasoning.
    claim_id                UUID NOT NULL,
    claim_version           INTEGER,

    -- WHY the procedure references the claim. Non-compensatory downstream:
    -- STRONG roles (precondition / applicability / assumption) force a
    -- stale/revalidation when the claim changes; EXPLANATORY roles
    -- (rationale / decision / expected_effect / failure_mode /
    -- verification) are recorded for provenance and NEVER auto-invalidate
    -- the whole procedure.
    role                    TEXT NOT NULL CHECK (role IN (
        'PRECONDITION', 'APPLICABILITY', 'ASSUMPTION',
        'RATIONALE', 'DECISION', 'EXPECTED_EFFECT',
        'FAILURE_MODE', 'VERIFICATION'
    )),

    -- Optional: which procedure step(s) this ref pertains to, by step id
    -- from procedures.steps[*].id. Empty = whole-procedure scope.
    step_refs               JSONB NOT NULL DEFAULT '[]',

    -- How the ref was established: 'authored' (in the source / by a human),
    -- 'derived' (an extractor matched the claim to a precondition triple),
    -- 'backfilled' (migrated from preconditions[*].claim_id).
    ref_origin              TEXT NOT NULL DEFAULT 'derived'
                            CHECK (ref_origin IN ('authored', 'derived', 'backfilled')),

    extractor_version       TEXT,             -- when ref_origin='derived'
    ingestion_context_id    UUID,             -- migration 65 back-link
    created_by              TEXT,

    -- Bi-temporal: a ref is closed (t_invalid) when a new procedure
    -- version re-authors its claim set; supersede-by-append, never edit.
    t_valid                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid               TIMESTAMPTZ,
    t_created               TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One live ref per (procedure version, claim, role): the same claim
    -- may be BOTH a precondition and a rationale (two rows), but not two
    -- identical precondition rows.
    UNIQUE (procedure_id, procedure_version, claim_id, role)
);

CREATE INDEX IF NOT EXISTS idx_procedure_claim_refs_claim
    ON procedure_claim_refs (claim_id) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_procedure_claim_refs_procedure
    ON procedure_claim_refs (procedure_id, procedure_version) WHERE t_invalid IS NULL;
-- The role-aware invalidation query: "live STRONG-role refs to this claim".
CREATE INDEX IF NOT EXISTS idx_procedure_claim_refs_strong
    ON procedure_claim_refs (claim_id)
    WHERE t_invalid IS NULL
      AND role IN ('PRECONDITION', 'APPLICABILITY', 'ASSUMPTION');
