-- Migration 68 (Ingestion + Knowledge hardening, V4-hardening Part II-A
-- §5 / Gate G3 / audit B14): screening_decisions -- the persisted,
-- auditable ALLOW / QUARANTINE / REJECT record for every ingestion-time
-- security / policy screen.
--
-- Next free migration number: 69.
--   (49 sources, 50 ingestion_contexts, 51 procedure_claim_refs,
--    52 procedure_implementation_relation already exist; 54 is claimed
--    by a concurrent lane. This file takes 53 and nothing else.)
--
-- WHY THIS EXISTS
--   Injection screening already runs at ingestion time
--   (`skill_ingestion.py::_screen_untrusted_document`) but its ONLY
--   effect is a provenance downgrade to 'system_pending_review'. There
--   is NO row anywhere recording that a screen ran, what it decided,
--   which detector decided it, at what version, over what content, and
--   why. Secret/credential, PII, license, malicious-executable and
--   source-trust checks are not run as an ingestion-time pass at all --
--   they exist only at publication time (`publication.py`). V4-hardening
--   §5: "Persist ALLOW / QUARANTINE / REJECT with detector/version/
--   reason. Rejected material is auditable. Do not silently delete
--   rejected material." This table is that record.
--
-- WHAT THIS IS NOT
--   Not a DLP engine and not a second provenance graph. A row here is a
--   DETECTION + DECISION receipt: it composes the regex detectors that
--   already exist (`_META_DIRECTIVE_RE` / `_TRUST_ASSERTION_RE` in
--   skill_ingestion, `KNOWN_TOKEN_PATTERNS` in trace_redaction) and
--   writes down what they found. A REJECT row does NOT delete the
--   screened artifact -- the caller decides what to do with a
--   QUARANTINE / REJECT; the material stays auditable.
--
-- Fresh-start rule: additive, nullable back-links, NO backfill. Legacy
-- ingestions that ran before this table simply have no screening row.
-- Idempotent: CREATE / ALTER ... IF NOT EXISTS, same idiom as the rest
-- of db/.
--
-- Append-only IN SPIRIT: a screening decision is a historical fact and
-- is never edited in place; a re-screen appends a new row. No
-- append-only trigger ships in THIS migration -- same sequencing note
-- migration 24 used for the verified-requires-evidence engine trigger:
-- the trigger lands in the change that wires the first real re-screen /
-- retraction path, gate and writer together, not before.

-- ============================================================
-- screening_decisions -- one row per (screened artifact, check_type)
-- decision. `decision` is the terminal verdict; `check_type` names which
-- screen produced it; `detector` + `detector_version` pin the exact code
-- that decided; `signals` carries the concrete matched labels/offsets
-- (NEVER a raw secret value -- the writer stores a redacted marker).
-- ============================================================
CREATE TABLE IF NOT EXISTS screening_decisions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Back-link to the IngestionContext (migration 65). Nullable: a
    -- screen may run BEFORE a context is opened (fail-closed pre-flight),
    -- so the context id is stamped when it exists and left NULL when it
    -- does not.
    ingestion_context_id UUID,

    -- The Source (migration 64) the screened material belongs to, when
    -- known. Nullable for the same reason.
    source_ref          UUID,

    -- What was screened.
    artifact_uri        TEXT,
    content_hash        TEXT,

    -- The verdict. Three values, closed set, inline CHECK: unlike
    -- check_type below this vocabulary is fixed by §5 and will not grow.
    decision            TEXT NOT NULL
                          CHECK (decision IN ('ALLOW', 'QUARANTINE', 'REJECT')),

    -- Which screen produced the verdict. TEXT + named CHECK, NOT a pg
    -- enum: the screen catalogue has visibly grown over this project's
    -- life and will keep growing (pii / license / malicious_executable /
    -- source_trust are not all implemented yet), so a CHECK we can widen
    -- in a one-line migration beats ALTER TYPE ... ADD VALUE's
    -- transaction constraints -- the exact call migration 64 made for
    -- source_kind.
    check_type          TEXT NOT NULL,

    -- The exact code that decided, e.g.
    -- 'skill_md._screen_untrusted_document', and its version stamp.
    detector            TEXT NOT NULL,
    detector_version    TEXT NOT NULL,

    -- The concrete matched signal labels / spans -- a JSON array of
    -- strings like ["aws_access_key <redacted:secret_exposure>@142"].
    -- NEVER the raw secret value: the writer replaces a matched secret
    -- with "<redacted:<check_type>>" plus a char offset before it gets
    -- here.
    signals             JSONB NOT NULL DEFAULT '[]',

    -- Human-readable one-liner.
    reason              TEXT,

    created_by          TEXT,
    -- Ticket 09 pair rule: BOTH columns on every new table
    -- (access.py::visibility_predicate() breaks otherwise). Invariant #9
    -- (a private screening record cannot silently become public).
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    scope_type          TEXT,
    scope_entity_id     TEXT,

    -- Bi-temporal trio, same as sources / evidence / ingestion_contexts:
    -- t_valid / t_invalid = true-in-the-world (t_invalid = a decision we
    -- have retracted / superseded), t_created = when we recorded it.
    -- Supersede-by-append; no in-place edit.
    t_valid             TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid           TIMESTAMPTZ,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- named, greppable CHECKs (migration 22/24/49's defense-in-depth
-- idiom: the service layer owns error messaging, the engine refuses
-- garbage from direct-SQL paths that bypass it) ----
DO $$
BEGIN
    -- Today's screen catalogue. Widen with a one-line
    -- `ALTER TABLE ... DROP CONSTRAINT / ADD CONSTRAINT` migration when a
    -- new screen ships; do NOT reuse this file.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'check_type_chk_screening_decisions') THEN
        ALTER TABLE screening_decisions ADD CONSTRAINT check_type_chk_screening_decisions
            CHECK (check_type IN (
                'prompt_injection',
                'trust_escalation',
                'secret_exposure',
                'pii',
                'license',
                'malicious_executable',
                'source_trust',
                'unsafe_locator'
            ));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'detector_version_chk_screening_decisions') THEN
        ALTER TABLE screening_decisions ADD CONSTRAINT detector_version_chk_screening_decisions
            CHECK (length(trim(detector_version)) > 0);
    END IF;

    -- Scope CHECK, migration 24/49's greppable one-block-per-table shape.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_screening_decisions') THEN
        ALTER TABLE screening_decisions ADD CONSTRAINT scope_type_chk_screening_decisions
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_screening_decisions_context
    ON screening_decisions (ingestion_context_id) WHERE ingestion_context_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_screening_decisions_content_hash
    ON screening_decisions (content_hash);
CREATE INDEX IF NOT EXISTS idx_screening_decisions_decision
    ON screening_decisions (decision, check_type);
CREATE INDEX IF NOT EXISTS idx_screening_decisions_source_ref
    ON screening_decisions (source_ref) WHERE source_ref IS NOT NULL;
