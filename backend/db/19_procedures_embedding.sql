-- Ticket 12 (applicability function): "similarity ranks only the
-- survivors" after the non-compensatory hard-constraint filter cascade.
-- 18_procedures.sql omitted an embedding column -- it's already applied
-- to the real database, so this is a separate ALTER rather than an edit
-- to that migration (same discipline this repo enforces everywhere
-- else: never edit an applied migration).
--
-- VECTOR(1024) + HNSW cosine, same convention as knowledge_nodes/
-- task_nodes/agents (01_ontology.sql, 07_agents.sql).

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS embedding VECTOR(1024);

CREATE INDEX IF NOT EXISTS idx_procedures_embedding
    ON procedures USING hnsw (embedding vector_cosine_ops);
