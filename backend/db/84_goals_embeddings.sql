-- Migration 84: Goal embeddings (ingestion.md Sec 17: "Goals: embed
-- globally") -- the vector column migration 83 deliberately did not add
-- (that migration was schema+backfill only). Same VECTOR(1024)/hnsw
-- convention as every other embedded table (01_ontology.sql, 07_agents.sql,
-- 19_procedures_embedding.sql).
--
-- This is additive and opt-in at the write path: app/services/goals.py::
-- find_or_create_goal only computes/stores an embedding when a caller
-- passes an `embedder` (none of today's real callers do yet -- adding
-- embedding calls to every procedure/implementation capture is a real
-- latency/cost decision, not a side effect of a migration). A Goal
-- without an embedding is a normal, valid, embedding IS NULL row -- same
-- honesty convention `procedures.embedding` already established.
--
-- Next free migration number: 85.

ALTER TABLE goals
    ADD COLUMN IF NOT EXISTS embedding VECTOR(1024),
    ADD COLUMN IF NOT EXISTS embedding_model_id TEXT,
    ADD COLUMN IF NOT EXISTS embedding_provider TEXT,
    ADD COLUMN IF NOT EXISTS embedding_text_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_goals_embedding
    ON goals USING hnsw (embedding vector_cosine_ops);
