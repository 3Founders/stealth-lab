-- Index tuning for an append-only, bi-temporal graph.
-- Idempotent: safe to re-run.
--
-- Why this exists as a forward migration rather than an edit to
-- 01_ontology.sql: the original CREATE INDEX statements are guarded by
-- IF NOT EXISTS, so changing them there would silently do nothing on
-- every database that already ran 01. Index definitions can only be
-- changed by dropping and recreating them.
--
-- Two changes, both driven by the same property: nothing in this schema
-- is ever deleted. A superseded node keeps its row with t_invalid set,
-- and every query that matters filters t_invalid IS NULL.
--
--   1. Make the search indexes PARTIAL on that predicate. A full index
--      keeps indexing rows no live query can ever return. For HNSW
--      specifically this is worse than wasted space: superseded vectors
--      stay in the proximity graph, so a search that asks for the k
--      nearest neighbours spends some of its k on rows that are then
--      filtered out afterwards. Recall against *live* content decays as
--      the dead fraction grows, silently, with no error and no failing
--      test -- the corpus just quietly answers worse over time.
--
--   2. Raise ef_construction from the pgvector default of 64. Build is
--      slower and one-off; recall is permanent. m stays at 16, which is
--      the right trade for 1024-dim text embeddings at this scale.
--
-- NOTE ON LOCKING. DROP INDEX and CREATE INDEX both take locks that block
-- writes to the table. That is instant on a small graph and is not on a
-- large one. Against real volume, run the CREATE steps as
-- CREATE INDEX CONCURRENTLY outside a transaction -- which is why this
-- file has to be applied deliberately rather than treated as routine.

-- Vector search -------------------------------------------------------
DROP INDEX IF EXISTS idx_kn_embedding;
CREATE INDEX IF NOT EXISTS idx_kn_embedding
    ON knowledge_nodes USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE t_invalid IS NULL;

DROP INDEX IF EXISTS idx_tn_embedding;
CREATE INDEX IF NOT EXISTS idx_tn_embedding
    ON task_nodes USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE t_invalid IS NULL;

-- Full-text search ----------------------------------------------------
-- Same argument, less severe: a GIN index over dead rows costs space and
-- scan time but does not degrade ranking the way HNSW degrades recall.
DROP INDEX IF EXISTS idx_kn_fts;
CREATE INDEX IF NOT EXISTS idx_kn_fts
    ON knowledge_nodes USING gin (to_tsvector('english', name))
    WHERE t_invalid IS NULL;

DROP INDEX IF EXISTS idx_tn_fts;
CREATE INDEX IF NOT EXISTS idx_tn_fts
    ON task_nodes USING gin (
        to_tsvector('english', name || ' ' || COALESCE(description, ''))
    )
    WHERE t_invalid IS NULL;

-- Currently-valid lookups ---------------------------------------------
-- The (t_valid, t_invalid) indexes from 01 serve point-in-time queries.
-- The overwhelmingly common query is not point-in-time, it is "what is
-- true now", so give that its own much smaller index.
CREATE INDEX IF NOT EXISTS idx_kn_live ON knowledge_nodes (id) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_tn_live ON task_nodes (id) WHERE t_invalid IS NULL;

-- Traversal joins on edges are always in one of two directions, and
-- always restricted to live edges.
CREATE INDEX IF NOT EXISTS idx_edges_source_live
    ON edges (source_id, source_table) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_edges_target_live
    ON edges (target_id, target_table) WHERE t_invalid IS NULL;
