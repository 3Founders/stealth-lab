-- Migration 20 (memory-substrate map): procedure extraction's two real
-- prerequisites -- a human-approval axis on `procedures` distinct from
-- verification_state, and the `procedure_extractors` registry itself.
--
-- Next free number: 19 is highest, 15 was historically skipped
-- (16_state_projection_index.sql took the slot a doc had once called
-- "15"). Never edit an applied migration -- scripts/migrate.py checksums
-- them and a mismatch is a hard error by this repo's own design.
--
-- Idempotent: safe to re-run, same idiom as every other migration here.

-- ============================================================
-- 1. Human approval as its own orthogonal axis.
--
-- verification_state='verified' means something specific and
-- STATISTICAL (procedures.py: >=10 successes, 0 failures, >=3 distinct
-- contexts -- MIN_SUCCESSES_FOR_VERIFIED/MIN_DISTINCT_CONTEXTS_FOR_
-- VERIFIED). "A human looked at this and said OK" is a different
-- question and must NOT overwrite that meaning -- an approved procedure
-- with 2 recorded successes is still, correctly, not verified.
--
-- Same three-value vocabulary 05_decomposition.sql already uses for
-- exactly this shape of decision, not a fourth spelling of it.
-- ============================================================
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS approval_status TEXT
    NOT NULL DEFAULT 'proposed'
    CHECK (approval_status IN ('proposed', 'approved', 'rejected'));
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_procedures_approval_status
    ON procedures(approval_status) WHERE t_invalid IS NULL;

-- ============================================================
-- 2. capability_statement -- the abstract, domain-stripped field this
-- pass's extraction actually embeds (NOT the concrete `goal`, which
-- names real files/symbols/repos and therefore embeds nowhere near a
-- different domain's task). Cross-domain retrieval depends on this
-- distinction existing as a real column, not a convention inside
-- `goal`.
-- ============================================================
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS capability_statement TEXT;

-- extracted_by: "name@version" of the procedure_extractors row that
-- produced this procedure (or 'deterministic_v1' for the no-LLM
-- fallback -- see procedure_extraction/strategies.py). TEXT, not a FK:
-- an extractor row can be superseded/deleted while procedures it
-- already produced must remain queryable, same reasoning
-- 12_trace_ingestion_pipeline.sql gives for trace_id having no FK.
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS extracted_by TEXT;

-- ============================================================
-- 3. procedure_extractors -- extractors as first-class, versioned,
-- reviewable objects, not hardcoded prompts. Modeled directly on
-- 07_agents.sql's `agents` table, which is already this repo's pattern
-- for a config-driven, bi-temporal, reviewable artifact -- including
-- its deliberate split between "approved" (cleared for listing/review)
-- and "enabled" (cleared to actually run). Versions SUPERSEDE, never
-- edit in place: (name, version) is unique, and improving an extractor
-- means inserting a new version row and setting the old one's
-- t_invalid -- so "which extractor version produced this procedure"
-- (via procedures.extracted_by) stays answerable forever, the same
-- provenance discipline invariants 1 and 2 require everywhere else.
-- ============================================================
DO $$ BEGIN
    CREATE TYPE extractor_kind AS ENUM ('deterministic', 'llm', 'composite');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE extractor_review_state AS ENUM ('proposed', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS procedure_extractors (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT NOT NULL,
    description    TEXT,
    kind           extractor_kind NOT NULL,
    version        TEXT NOT NULL,

    -- llm kind: {"system_prompt": ..., "model": ..., "temperature": ...,
    -- "few_shot": [...], "allowed_binders": [...]}. deterministic kind:
    -- typically empty -- the derive.py logic it names is code, not data,
    -- per this module's own "mechanism vs variant" split.
    config         JSONB NOT NULL DEFAULT '{}',
    -- e.g. {"domain": ["debugging"]} -- matched via applicability.py's
    -- existing _scope_matches(), reused rather than a second
    -- implementation of the same scope-narrowing logic.
    scope          JSONB NOT NULL DEFAULT '{}',

    review_state   extractor_review_state NOT NULL DEFAULT 'proposed',
    -- Deliberately separate from review_state = 'approved', same
    -- distinction 07_agents.sql's `runnable` column makes for agents:
    -- an extractor can be approved for listing/comparison before it is
    -- cleared to actually run selection. Never set true by a migration
    -- or default -- only an explicit review action may flip it.
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,

    t_valid        TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid      TIMESTAMPTZ,
    t_created      TIMESTAMPTZ NOT NULL DEFAULT now(),

    owner_id       TEXT,
    visibility     visibility_level NOT NULL DEFAULT 'public',

    UNIQUE (name, version)
);

CREATE INDEX IF NOT EXISTS idx_procedure_extractors_selectable
    ON procedure_extractors(name) WHERE enabled AND review_state = 'approved' AND t_invalid IS NULL;

-- The deterministic baseline, seeded enabled -- procedure extraction
-- must work with zero LLM configured (ticket 04's own rule: "no study
-- reports a rule-based baseline before adding a model... build both and
-- measure the delta"), and this row is what makes that the real,
-- always-available fallback rather than an unreachable code path.
INSERT INTO procedure_extractors (name, description, kind, version, review_state, enabled)
VALUES (
    'deterministic_v1',
    'No-LLM baseline: steps from the observed tool sequence, preconditions from '
    'project_state(as_of=episode_start), no capability abstraction. Honest fallback, '
    'not a placeholder -- produces a literal, non-generalized but fully grounded procedure.',
    'deterministic', '1', 'approved', TRUE
)
ON CONFLICT (name, version) DO NOTHING;
