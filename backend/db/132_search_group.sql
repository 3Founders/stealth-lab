-- Migration 132: storage layout v2 (docs/storage_layout_v2.md) -- the search/log database B becomes a GROUP of
-- projects that grows on demand, and the control database A sheds a Goal's heavy search columns.
--
--   * knowledge_shards.role: 'knowledge' (K000, K001..: canonical knowledge) or 'search' (S001..: search indexes and
--     logs). Placement and fan-out read only the members of the role they need.
--   * search_routes (on A): the search member holding an object's search row (Goal, Procedure, Claim), so an update
--     or delete of that row goes back to the same member. No row = not placed on a member (the legacy single-B or
--     single-database layout, where the search tables live on the search pool / A).
--   * goal_search_docs: a Goal's searchable text, full-text vector and embedding, kept on a search member. On A the
--     slim goal_search_index keeps everything joined or filtered in SQL (status, scope, has_procedures, names).
--
-- Idempotent. Created on every database; which one holds rows depends on the role it is registered with.

ALTER TABLE knowledge_shards ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'knowledge';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'knowledge_shards_role_check') THEN
        ALTER TABLE knowledge_shards ADD CONSTRAINT knowledge_shards_role_check CHECK (role IN ('knowledge', 'search'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS search_routes (
    object_type      TEXT NOT NULL,          -- goal | procedure | claim
    object_id        UUID NOT NULL,          -- goal id, procedure_id (lineage), claim id
    search_shard_id  TEXT NOT NULL,
    PRIMARY KEY (object_type, object_id)
);

CREATE TABLE IF NOT EXISTS goal_search_docs (
    goal_id          UUID PRIMARY KEY,
    canonical_name   TEXT NOT NULL,
    short_description TEXT,
    search_text      TEXT,
    search_tsv       TSVECTOR,
    embedding        VECTOR(1024),
    embedding_model  TEXT,
    embedding_version TEXT,
    embedding_dim    INTEGER,
    -- filter columns duplicated from goal_search_index so a search member can filter without a cross-database join
    status           TEXT,
    has_procedures   BOOLEAN NOT NULL DEFAULT false,
    scope_type       TEXT,
    scope_entity_id  TEXT,
    visibility       visibility_level,
    owner_id         TEXT,
    tenant_id        UUID,
    home_shard_id    TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- product search filters and orders by these too (find_goal: resolved/unresolved; newest first)
ALTER TABLE goal_search_docs ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE goal_search_docs ADD COLUMN IF NOT EXISTS t_created TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_goal_search_docs_tsv ON goal_search_docs USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_goal_search_docs_embedding ON goal_search_docs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_goal_search_docs_status ON goal_search_docs (status) WHERE has_procedures;

-- With a search group the control database keeps a SLIM goal_search_index row (its searchable text and vectors live
-- in goal_search_docs on a member), so search_text and search_tsv may be NULL there.
ALTER TABLE goal_search_index ALTER COLUMN search_text DROP NOT NULL;
ALTER TABLE goal_search_index ALTER COLUMN search_tsv DROP NOT NULL;

-- Search tables on a search member: the member has no shard registry (it lives on the control database), so a row's
-- home_shard_id cannot be a local foreign key -- the same reasoning migration 96 applied to canonical tables on
-- knowledge shards. It is written by the projection from the route and checked by `admin verify-projections`.
ALTER TABLE procedure_search_index DROP CONSTRAINT IF EXISTS procedure_search_index_home_shard_id_fkey;
ALTER TABLE claim_search_index DROP CONSTRAINT IF EXISTS claim_search_index_home_shard_id_fkey;
