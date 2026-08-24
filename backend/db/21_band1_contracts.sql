-- Band 1 contracts: scope keys, claim shape, embedding provenance.
-- Fresh-start ruling: applied to an empty/rebuilt database; NO backfills,
-- NO dual-read shims for legacy rows (ROADMAP.md, fresh-start ruling).
-- Idempotent per this repo's migration convention.

-- ============================================================
-- 1.2 support: 'system_pending_review' provenance — for objects
-- created mechanically (conflict proxy nodes) that no human has
-- reviewed yet. Distinct from company_debate, which previously
-- stamped these pre-debate and misattributed their origin.
-- ============================================================
ALTER TYPE provenance_source ADD VALUE IF NOT EXISTS 'system_pending_review';

-- ============================================================
-- 1.3 Scope columns on every entity table. These are the shard
-- key (§3) and are distinct from procedures.scope (the JSONB
-- applicability-narrowing dict). V0 gate enforces presence at
-- the service boundary; the DB columns are nullable only so the
-- gate -- not the storage engine -- is the enforcement point.
-- ============================================================
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;
ALTER TABLE task_nodes      ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE task_nodes      ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;
ALTER TABLE edges           ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE edges           ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;
ALTER TABLE procedures      ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE procedures      ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;
ALTER TABLE observations    ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE observations    ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;
ALTER TABLE episodes        ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE episodes        ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;
ALTER TABLE agent_traces    ADD COLUMN IF NOT EXISTS scope_type  TEXT;
ALTER TABLE agent_traces    ADD COLUMN IF NOT EXISTS scope_entity_id TEXT;

CREATE INDEX IF NOT EXISTS idx_kn_scope ON knowledge_nodes(scope_type, scope_entity_id) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_proc_scope ON procedures(scope_type, scope_entity_id) WHERE t_invalid IS NULL;

-- ============================================================
-- 1.4 Claim shape born correct: structured columns alongside the
-- JSONB extension payload. Fresh-start: writers populate these
-- natively; nothing migrates from properties.
-- ============================================================
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS subject   TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS predicate TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS object    TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS proposition_type TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS claim_status TEXT
    DEFAULT 'candidate';
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS belief_score REAL;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS belief_method TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'kn_claim_status_chk'
    ) THEN
        ALTER TABLE knowledge_nodes ADD CONSTRAINT kn_claim_status_chk CHECK (
            claim_status IS NULL OR claim_status IN (
                'candidate', 'supported', 'disputed', 'uncertain',
                'stale', 'superseded', 'invalid', 'retracted'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'kn_proposition_chk'
    ) THEN
        ALTER TABLE knowledge_nodes ADD CONSTRAINT kn_proposition_chk CHECK (
            proposition_type IS NULL OR proposition_type IN (
                'fact', 'property', 'conditional', 'causal', 'temporal',
                'probabilistic', 'comparative', 'procedural', 'negative',
                'composite'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kn_claim_spo
    ON knowledge_nodes(subject, predicate)
    WHERE node_type = 'claim' AND t_invalid IS NULL;

-- ============================================================
-- 1.6 Embedding provenance stamps: model transitions become a
-- column update, not an operational gamble.
-- ============================================================
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS embedding_model_id TEXT;
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS embedding_dim INT;
ALTER TABLE task_nodes      ADD COLUMN IF NOT EXISTS embedding_model_id TEXT;
ALTER TABLE task_nodes      ADD COLUMN IF NOT EXISTS embedding_dim INT;
ALTER TABLE procedures      ADD COLUMN IF NOT EXISTS embedding_model_id TEXT;
ALTER TABLE procedures      ADD COLUMN IF NOT EXISTS embedding_dim INT;

-- ============================================================
-- 1.2/1.9 support: provenance + extractor version become required
-- at the boundary for new rows; the columns predate this
-- migration but the V0 gate now enforces non-null going forward.
-- No backfill: trial-era rows are discarded by decision.
-- ============================================================
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS extractor_version TEXT;
