-- Migration 87: Implementation goal embeddings (Prompt 2 Sec 11 follow-up,
-- 2026-09-16: hybrid semantic Goal->Implementation retrieval). Same
-- VECTOR(1024)/hnsw convention as every other embedded table
-- (01_ontology.sql, 07_agents.sql, 19_procedures_embedding.sql,
-- 84_goals_embeddings.sql).
--
-- WHY THIS COLUMN, GIVEN `goals.embedding` ALREADY EXISTS: Goal is the
-- first-class embedded object in this codebase -- an Implementation
-- whose `goal_id` (migration 83) is set should NEVER get its own
-- embedding computed; it rides entirely on the canonical
-- `goals.embedding` via that real FK join (always current, never
-- duplicated, never able to drift from the Goal it's linked to). This
-- column exists ONLY for the real fallback case: an Implementation with
-- free-text `goal` (migration 80) but NO `goal_id` link yet -- there is
-- no canonical Goal row to borrow an embedding from, so its own raw
-- `goal` text is embedded directly. The write path
-- (implementation_registry.py::register) enforces this split -- see its
-- own docstring, not re-explained here.
--
-- Additive and opt-in, same posture migration 84 already established:
-- `register()` only computes/stores this when a caller passes an
-- `embedder` AND no `goal_id` is set. A NULL value is a normal, valid
-- row -- most Implementations have neither `goal` nor `goal_id` today
-- (confirmed live: 19/618), so this stays NULL for the overwhelming
-- majority, honestly.
--
-- Next free migration number: 88.

ALTER TABLE implementations
    ADD COLUMN IF NOT EXISTS goal_embedding VECTOR(1024),
    ADD COLUMN IF NOT EXISTS goal_embedding_model_id TEXT,
    ADD COLUMN IF NOT EXISTS goal_embedding_provider TEXT,
    ADD COLUMN IF NOT EXISTS goal_embedding_text_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_implementations_goal_embedding
    ON implementations USING hnsw (goal_embedding vector_cosine_ops);
