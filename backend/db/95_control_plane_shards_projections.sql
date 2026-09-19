-- Migration 95: control plane for sharded knowledge + global search projections
-- + durable identity decisions + lease-based ingestion jobs.
--
-- What this is NOT: a second canonical store. `goals`, `procedures` and
-- `knowledge_nodes` (claims) stay canonical. The *_search_index tables are
-- rebuildable projections (app/services/search_projection.py: reindex/verify).
--
-- Physical FKs cannot span databases: projection rows reference canonical
-- objects by (stable id, home_shard_id). For the built-in shard 'K000' (this
-- database) the canonical row is local; app code validates cross-shard refs
-- (search_projection.verify_projection).
--
-- Idempotent: every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING so a
-- retried or partially-applied run converges.

-- ---------------------------------------------------------------- shards
CREATE TABLE IF NOT EXISTS knowledge_shards (
    shard_id      TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'active',
    -- Relative share of NEW goals (rendezvous-hash weight). 0 = never chosen.
    weight        INTEGER NOT NULL DEFAULT 100,
    capacity_rows BIGINT,
    -- NAME of the env var that holds this shard's DSN. Never the DSN itself:
    -- provider URLs are deployment config, not identity or repo data. NULL
    -- means "this database" (the built-in home shard).
    dsn_env       TEXT,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_shards_id_chk CHECK (shard_id ~ '^[A-Za-z0-9_-]{1,32}$'),
    CONSTRAINT knowledge_shards_status_chk
        CHECK (status IN ('active', 'full', 'readonly', 'unhealthy', 'retired')),
    CONSTRAINT knowledge_shards_weight_chk CHECK (weight >= 0)
);

INSERT INTO knowledge_shards (shard_id, status, dsn_env, notes)
VALUES ('K000', 'active', NULL, 'built-in home shard: the control database itself')
ON CONFLICT (shard_id) DO NOTHING;

-- object -> home shard (the global routing table). Written in the same
-- transaction as the canonical row when both are in this database, otherwise
-- by the projection outbox.
CREATE TABLE IF NOT EXISTS object_routes (
    object_type   TEXT NOT NULL,
    object_id     UUID NOT NULL,
    home_shard_id TEXT NOT NULL REFERENCES knowledge_shards(shard_id),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, object_id),
    CONSTRAINT object_routes_type_chk CHECK (object_type IN ('goal', 'claim', 'procedure'))
);
CREATE INDEX IF NOT EXISTS idx_object_routes_shard ON object_routes(home_shard_id, object_type);

ALTER TABLE goals
    ADD COLUMN IF NOT EXISTS home_shard_id TEXT NOT NULL DEFAULT 'K000' REFERENCES knowledge_shards(shard_id);
ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS home_shard_id TEXT NOT NULL DEFAULT 'K000' REFERENCES knowledge_shards(shard_id);

-- Exact source identity for retry/duplicate-delivery safety: a worker that
-- ingests the same source twice (or two workers racing) can create AT MOST one
-- live Procedure for it. NULL for legacy rows and adapters that dedup elsewhere.
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS source_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_procedures_live_source_key
    ON procedures(source_key) WHERE source_key IS NOT NULL AND t_invalid IS NULL;

-- ------------------------------------------------------- goal hierarchy
-- Separate from `goals` on purpose: optional, multi-parent, async, never used
-- for sharding, never required by retrieval.
CREATE TABLE IF NOT EXISTS goal_relations (
    specific_goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    abstract_goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    relation_type    TEXT NOT NULL DEFAULT 'SPECIALIZES',
    status           TEXT NOT NULL DEFAULT 'proposed',
    confidence       REAL,
    provenance       TEXT,
    decision_id      UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (specific_goal_id, abstract_goal_id, relation_type),
    CONSTRAINT goal_relations_type_chk CHECK (relation_type IN ('SPECIALIZES')),
    CONSTRAINT goal_relations_status_chk CHECK (status IN ('proposed', 'accepted', 'rejected')),
    CONSTRAINT goal_relations_not_self_chk CHECK (specific_goal_id <> abstract_goal_id),
    CONSTRAINT goal_relations_confidence_chk CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);
CREATE INDEX IF NOT EXISTS idx_goal_relations_abstract ON goal_relations(abstract_goal_id);

-- ------------------------------------------------ durable identity decisions
-- Every Goal/Claim/Procedure identity decision, auditable independent of any
-- observability vendor. `idempotency_key` makes a replayed job re-use its
-- earlier decision instead of re-judging (and possibly flipping) it.
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

-- ------------------------------------------------------ search projections
-- Embedding model/version/dimension are stored on every row and every ANN
-- query filters on them: incompatible vector spaces are never mixed.
CREATE TABLE IF NOT EXISTS goal_search_index (
    goal_id           UUID PRIMARY KEY,
    canonical_name    TEXT NOT NULL,
    short_description TEXT,
    aliases           TEXT[] NOT NULL DEFAULT '{}',
    search_text       TEXT NOT NULL,
    search_tsv        TSVECTOR NOT NULL,
    embedding         vector(1024),
    embedding_model   TEXT,
    embedding_version TEXT,
    embedding_dim     INTEGER,
    home_shard_id     TEXT NOT NULL REFERENCES knowledge_shards(shard_id),
    status            TEXT NOT NULL,
    version           INTEGER NOT NULL,
    visibility        visibility_level NOT NULL,
    owner_id          TEXT,
    scope_type        TEXT,
    scope_entity_id   TEXT,
    tenant_id         UUID,          -- always NULL today (goals have no tenant); keeps visibility_predicate() uniform
    updated_at        TIMESTAMPTZ NOT NULL,
    projected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT goal_search_index_embed_chk CHECK (embedding IS NULL OR (embedding_model IS NOT NULL AND embedding_dim IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_goal_search_tsv ON goal_search_index USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_goal_search_embedding ON goal_search_index
    USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_goal_search_scope ON goal_search_index(scope_type, scope_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_goal_search_shard ON goal_search_index(home_shard_id);

CREATE TABLE IF NOT EXISTS procedure_search_index (
    procedure_id           UUID PRIMARY KEY,           -- stable id across versions
    procedure_row_id       UUID NOT NULL,              -- the live version's procedures.id
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
    home_shard_id          TEXT NOT NULL REFERENCES knowledge_shards(shard_id),
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
    home_shard_id     TEXT NOT NULL REFERENCES knowledge_shards(shard_id),
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

-- Durable record of retrieval decisions (independent of any observability
-- vendor): which query hash, which local Claim ids, which Goal(s), which
-- Procedure was selected, in which semantic mode (jev / model_fallback /
-- candidates_only) and whether the result was degraded.
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

-- Durable projection outbox: canonical write commits the row + one outbox
-- entry; a drainer applies it idempotently. A crash between canonical write
-- and projection leaves a pending entry (lag is observable), never a silent
-- gap. Replay is safe: applying reads current canonical state.
CREATE TABLE IF NOT EXISTS projection_outbox (
    id          BIGSERIAL PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id   UUID NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at  TIMESTAMPTZ,
    CONSTRAINT projection_outbox_type_chk CHECK (object_type IN ('goal', 'claim', 'procedure')),
    CONSTRAINT projection_outbox_status_chk CHECK (status IN ('pending', 'applied', 'failed'))
);
-- One pending entry per object: repeated writes coalesce.
CREATE UNIQUE INDEX IF NOT EXISTS idx_projection_outbox_pending
    ON projection_outbox(object_type, object_id) WHERE status = 'pending';

-- ------------------------------------------------ write-path guarantees
-- DB-level so NO adapter (or raw SQL writer) can bypass routing or projection:
-- every canonical write in this database records its route and queues exactly
-- one coalesced projection refresh in the SAME transaction. Objects homed on a
-- remote shard get the same entries from app code (search_projection.enqueue).
CREATE OR REPLACE FUNCTION sl_canonical_touch() RETURNS trigger AS $$
DECLARE
    otype TEXT;
    oid   UUID;
    shard TEXT := 'K000';
BEGIN
    IF TG_TABLE_NAME = 'goals' THEN
        otype := 'goal'; oid := NEW.id; shard := NEW.home_shard_id;
    ELSIF TG_TABLE_NAME = 'procedures' THEN
        otype := 'procedure'; oid := NEW.procedure_id; shard := NEW.home_shard_id;
    ELSE
        otype := 'claim'; oid := NEW.id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        INSERT INTO object_routes (object_type, object_id, home_shard_id)
        VALUES (otype, oid, shard) ON CONFLICT (object_type, object_id) DO NOTHING;
    END IF;
    INSERT INTO projection_outbox (object_type, object_id) VALUES (otype, oid)
        ON CONFLICT (object_type, object_id) WHERE status = 'pending' DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_goals_canonical_touch ON goals;
CREATE TRIGGER tg_goals_canonical_touch AFTER INSERT OR UPDATE ON goals
    FOR EACH ROW EXECUTE FUNCTION sl_canonical_touch();
DROP TRIGGER IF EXISTS tg_procedures_canonical_touch ON procedures;
CREATE TRIGGER tg_procedures_canonical_touch AFTER INSERT OR UPDATE ON procedures
    FOR EACH ROW EXECUTE FUNCTION sl_canonical_touch();
DROP TRIGGER IF EXISTS tg_claims_canonical_touch ON knowledge_nodes;
CREATE TRIGGER tg_claims_canonical_touch AFTER INSERT OR UPDATE ON knowledge_nodes
    FOR EACH ROW WHEN (NEW.node_type = 'claim')
    EXECUTE FUNCTION sl_canonical_touch();

-- ---------------------------------------------- ingestion job lease/retry
-- Reuses `ingestion_jobs` (no second queue). Legacy statuses keep their
-- meaning: processing == leased/running, done == completed,
-- failed == permanent_failed. New: 'retryable_failed' (run_after gates retry).
ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS lease_until      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS idempotency_key  TEXT,
    ADD COLUMN IF NOT EXISTS source_id        TEXT,
    ADD COLUMN IF NOT EXISTS scope_type       TEXT,
    ADD COLUMN IF NOT EXISTS scope_entity_id  TEXT,
    ADD COLUMN IF NOT EXISTS owner_id         TEXT,
    ADD COLUMN IF NOT EXISTS visibility       TEXT,
    ADD COLUMN IF NOT EXISTS config_version   TEXT,
    ADD COLUMN IF NOT EXISTS started_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS max_attempts     INTEGER NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS usage            JSONB NOT NULL DEFAULT '{}';

ALTER TABLE ingestion_jobs DROP CONSTRAINT IF EXISTS ingestion_jobs_status_check;
ALTER TABLE ingestion_jobs ADD CONSTRAINT ingestion_jobs_status_check
    CHECK (status IN ('pending', 'processing', 'done', 'failed', 'cancelled', 'retryable_failed'));

-- Duplicate enqueue of the same logical job is a no-op (ON CONFLICT DO NOTHING).
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_jobs_idempotency
    ON ingestion_jobs(job_type, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_lease
    ON ingestion_jobs(lease_until) WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_retry
    ON ingestion_jobs(run_after) WHERE status = 'retryable_failed';

-- -------------------------------------------------------------- backfill
-- Route + queue projection for rows that pre-date this migration. Outbox
-- entries (not inline INSERT...SELECT into the indexes) so embedding/text
-- shaping stays in one place (app code) and the backfill is a replayable drain.
INSERT INTO object_routes (object_type, object_id, home_shard_id)
SELECT 'goal', id, 'K000' FROM goals ON CONFLICT DO NOTHING;
INSERT INTO object_routes (object_type, object_id, home_shard_id)
SELECT 'procedure', procedure_id, 'K000' FROM procedures WHERE t_invalid IS NULL
ON CONFLICT DO NOTHING;
INSERT INTO object_routes (object_type, object_id, home_shard_id)
SELECT 'claim', id, 'K000' FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL
ON CONFLICT DO NOTHING;

INSERT INTO projection_outbox (object_type, object_id)
SELECT 'goal', id FROM goals ON CONFLICT DO NOTHING;
INSERT INTO projection_outbox (object_type, object_id)
SELECT DISTINCT 'procedure', procedure_id FROM procedures WHERE t_invalid IS NULL ON CONFLICT DO NOTHING;
INSERT INTO projection_outbox (object_type, object_id)
SELECT 'claim', id FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL ON CONFLICT DO NOTHING;
