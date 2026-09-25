-- target: search
-- Migration 117: the search/log database (control project B). Next free number: 118.
--
-- Runs ONLY with `python scripts/migrate.py --target search` (see migrate.py). The
-- control plane is split over two hosted projects because each has a storage cap:
-- project A (the control database, K000) keeps everything joined or transactional
-- together; project B holds the tables nothing joins to and no canonical write
-- shares a transaction with:
--
--   procedure_search_index, claim_search_index   rebuildable projections (the outbox
--                                                 that feeds them stays on A)
--   retrieval_decisions, identity_decisions      decision logs (identity replay is
--                                                 idempotent per idempotency_key)
--   llm_spend                                     model-cost ledger (CostGovernor)
--
-- Same columns, constraints and indexes as on the control database (migrations 04,
-- 95), minus the foreign keys to knowledge_shards (that registry lives on A;
-- projection home_shard_id values are validated by `admin verify-projections`).
-- With SEARCH_DATABASE_URL unset these tables stay on A and this file never runs.
-- Idempotent.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'visibility_level') THEN
        CREATE TYPE visibility_level AS ENUM ('public', 'private');
    END IF;
END $$;
ALTER TYPE visibility_level ADD VALUE IF NOT EXISTS 'org';

-- ------------------------------------------------------------ projections
CREATE TABLE IF NOT EXISTS procedure_search_index (
    procedure_id           UUID PRIMARY KEY,
    procedure_row_id       UUID NOT NULL,
    name                   TEXT NOT NULL,
    summary                TEXT,
    goal_id                UUID,
    preconditions_summary  TEXT,
    outcome_summary        TEXT,
    verification_summary   TEXT,
    search_text            TEXT NOT NULL,
    search_tsv             TSVECTOR NOT NULL,
    embedding              vector(1024),
    embedding_model        TEXT,
    embedding_version      TEXT,
    embedding_dim          INTEGER,
    home_shard_id          TEXT NOT NULL,
    status                 TEXT NOT NULL,
    version                INTEGER NOT NULL,
    visibility             visibility_level NOT NULL,
    owner_id               TEXT,
    scope_type             TEXT,
    scope_entity_id        TEXT,
    tenant_id              UUID,
    updated_at             TIMESTAMPTZ NOT NULL,
    projected_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT procedure_search_index_embed_chk CHECK (embedding IS NULL OR (embedding_model IS NOT NULL AND embedding_dim IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_proc_search_tsv ON procedure_search_index USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_proc_search_embedding ON procedure_search_index
    USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_proc_search_goal ON procedure_search_index(goal_id);
CREATE INDEX IF NOT EXISTS idx_proc_search_shard ON procedure_search_index(home_shard_id);

CREATE TABLE IF NOT EXISTS claim_search_index (
    claim_id          UUID PRIMARY KEY,
    statement         TEXT NOT NULL,
    primary_goal_id   UUID,
    scope_summary     TEXT,
    search_text       TEXT NOT NULL,
    search_tsv        TSVECTOR NOT NULL,
    embedding         vector(1024),
    embedding_model   TEXT,
    embedding_version TEXT,
    embedding_dim     INTEGER,
    home_shard_id     TEXT NOT NULL,
    status            TEXT NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    visibility        visibility_level NOT NULL,
    owner_id          TEXT,
    scope_type        TEXT,
    scope_entity_id   TEXT,
    tenant_id         UUID,
    updated_at        TIMESTAMPTZ NOT NULL,
    projected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT claim_search_index_embed_chk CHECK (embedding IS NULL OR (embedding_model IS NOT NULL AND embedding_dim IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_claim_search_tsv ON claim_search_index USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_claim_search_embedding ON claim_search_index
    USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_claim_search_goal ON claim_search_index(primary_goal_id);

-- ------------------------------------------------------------ decision logs
CREATE TABLE IF NOT EXISTS identity_decisions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type      TEXT NOT NULL,
    candidate_text   TEXT NOT NULL,
    scope_type       TEXT,
    scope_entity_id  TEXT,
    decision         TEXT NOT NULL,
    resolved_id      UUID,
    candidates       JSONB NOT NULL DEFAULT '[]',
    judge_chain      TEXT,
    judge_provider   TEXT,
    judge_model      TEXT,
    prompt_version   TEXT,
    fts_candidates   INTEGER NOT NULL DEFAULT 0,
    vector_candidates INTEGER NOT NULL DEFAULT 0,
    job_id           BIGINT,
    idempotency_key  TEXT,
    detail           JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT identity_decisions_type_chk CHECK (object_type IN ('goal', 'claim', 'procedure')),
    CONSTRAINT identity_decisions_decision_chk CHECK (decision IN (
        'exact_match', 'same', 'related', 'broader', 'narrower', 'contradicts',
        'distinct', 'no_candidates', 'judge_unavailable', 'new_version'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_decisions_idem
    ON identity_decisions(object_type, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_identity_decisions_resolved ON identity_decisions(object_type, resolved_id);

CREATE TABLE IF NOT EXISTS retrieval_decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_sha256    TEXT NOT NULL,
    viewer_id       TEXT,
    goal_ids        UUID[] NOT NULL DEFAULT '{}',
    procedure_ids   UUID[] NOT NULL DEFAULT '{}',
    selected_procedure_id UUID,
    local_claim_ids TEXT[] NOT NULL DEFAULT '{}',
    mode            TEXT NOT NULL,
    degraded        BOOLEAN NOT NULL DEFAULT false,
    detail          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_retrieval_decisions_created ON retrieval_decisions(created_at);

-- ------------------------------------------------------------ model-cost ledger
CREATE TABLE IF NOT EXISTS llm_spend (
    id             BIGSERIAL PRIMARY KEY,
    scope_key      TEXT,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    operation      TEXT NOT NULL,
    estimated_cost NUMERIC NOT NULL DEFAULT 0,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_spend_window ON llm_spend(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_spend_scope  ON llm_spend(scope_key, occurred_at DESC);
