-- Migration 74 (Ingestion + Knowledge hardening, G24 residual):
-- a durable, queryable queue for procedures that were published globally
-- with `global_verification_required=True` but have NOT yet earned real
-- independent public verification evidence.
--
-- Next free migration number: 75.
--
-- WHY
--   app/services/publication_deps.py already COMPUTES
--   `global_verification_required` correctly on every publish (True unless
--   the traversal found >=2 independent public verification groups). But
--   nothing ever consumed that flag -- it was written into
--   `publication_records.classification_report`'s JSON blob and never read
--   again by any code path. "Recorded as required" was true; "will
--   actually get re-verified" had no mechanism at all. This table closes
--   that gap the same way migration 73's `claim_relation_candidates` did
--   for detected claim relations: a real, durable, reviewable queue --
--   NOT an auto-executing re-verification pipeline. Actually RUNNING an
--   independent re-verification (who does it, what counts as
--   independent-enough, how the result feeds back into verification_state)
--   is a real, separate, later decision -- explicitly out of scope here.
--
-- WHAT THIS IS NOT
--   Not a second source of truth for verification_state (procedures.
--   verification_state stays authoritative). Not auto-resolving under any
--   circumstance -- no code path here calls anything that flips a
--   procedure to 'verified'. A row here becoming 'resolved' only means a
--   human (or a later-decided mechanism) looked at it and recorded what
--   happened; promoting the procedure itself is a separate, existing
--   write path (evidence.py + claim_belief-style convergence), untouched
--   by this migration.
--
-- Fresh-start rule: additive, idempotent, no backfill (existing published
-- procedures that never got a row here are not retroactively enrolled --
-- this table only ever holds newly-published candidates from the point
-- this migration lands forward).

CREATE TABLE IF NOT EXISTS pending_global_verifications (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id        UUID NOT NULL,
    procedure_row_id    UUID NOT NULL,
    publication_id      UUID,
    reason              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    resolution          TEXT,
    resolved_by         TEXT,
    resolved_at         TIMESTAMPTZ,
    created_by          TEXT,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pending_global_verifications_status_chk') THEN
        ALTER TABLE pending_global_verifications ADD CONSTRAINT pending_global_verifications_status_chk
            CHECK (status IN ('pending', 'resolved', 'dismissed'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pending_global_verifications_pending
    ON pending_global_verifications (t_created) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_pending_global_verifications_procedure
    ON pending_global_verifications (procedure_id);
