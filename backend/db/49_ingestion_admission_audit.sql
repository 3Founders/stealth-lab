-- Global internet/public-source ingestion admission gate (public-source
-- ingestion ONLY -- app/services/ingestion_admission.py; distinct from
-- Local -> Global explicit user publication, app/services/publish.py,
-- which is untouched by this migration).
--
-- WHY ON ingested_artifacts, NOT A NEW TABLE: migration 32's own header
-- comment already states the design intent this migration fulfils --
-- "a rejected or duplicate candidate produces an artifact record with
-- neither [procedure_id/procedure_row_id]" -- i.e. ingested_artifacts was
-- always meant to be the per-artifact audit trail for EVERY admission
-- outcome, accepted or not. Before this pass, compile_skill_artifact's
-- one real rejection path (SkillMdParseError -- no extractable structure)
-- returned without writing any row at all, so a rejected artifact left
-- zero trace. This migration adds the columns; the app-layer fix (always
-- write an ingested_artifacts row, admitted/quarantined/rejected alike)
-- ships in the same change.
--
-- SAFE TO RECORD REJECTED/QUARANTINED CONTENT HERE: ingested_artifacts
-- never stores the artifact's raw body -- only identity/provenance
-- (source_type, uri, repository, path, commit, content_hash) and, for
-- skill_package artifacts, already-redacted metadata (parsed_metadata,
-- dependencies, requirements -- migration 32). The admission columns
-- below follow the same discipline: reason codes / check names, never
-- raw matched secret values or raw dangerous command text.
--
-- Additive, idempotent, no backfill: existing rows get admission_decision
-- NULL (meaning "ingested before this gate existed" -- not itself a claim
-- about safety), same as every other hardening-band migration here.
-- Next free migration number: 48 was highest before this file.

DO $$ BEGIN
    CREATE TYPE ingestion_admission_decision AS ENUM ('admitted', 'quarantined', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE ingested_artifacts
    ADD COLUMN IF NOT EXISTS admission_decision ingestion_admission_decision,
    -- Ordered list of check codes that actually fired (e.g.
    -- ["secret_literal", "prompt_injection"]), never the matched text
    -- itself -- see the discipline note above.
    ADD COLUMN IF NOT EXISTS admission_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Human-readable summary of why this decision was made -- "why was
    -- this procedure allowed into Global / quarantined / rejected"
    -- (brief Phase 6), never containing raw secret/command text.
    ADD COLUMN IF NOT EXISTS admission_reason TEXT,
    -- Name+version of the deterministic policy that produced this
    -- decision (ingestion_admission.ADMISSION_POLICY_VERSION), so a
    -- later policy change can be told apart from the content itself.
    ADD COLUMN IF NOT EXISTS admission_policy_version TEXT,
    -- Whether an LLM risk-classification call was made at all (Phase 3:
    -- the normal bulk path makes zero such calls; only an ambiguous
    -- deterministic verdict, WITH a model client configured, escalates).
    ADD COLUMN IF NOT EXISTS admission_escalated BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS admission_llm_model TEXT,
    -- One of 'safe' | 'unsafe' | 'uncertain' (uncertain covers both an
    -- explicit model answer and a call failure/timeout -- both fail
    -- toward "stay quarantined", never toward silent admission).
    ADD COLUMN IF NOT EXISTS admission_llm_verdict TEXT,
    ADD COLUMN IF NOT EXISTS admission_llm_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_admission_decision
    ON ingested_artifacts(admission_decision)
    WHERE admission_decision IS NOT NULL;
