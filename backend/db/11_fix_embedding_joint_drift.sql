-- Closes a real, confirmed drift (ticket 17, memory-substrate map, and
-- handoff.md's "known defects to fix along the way"): app/services/
-- retrieval.py has accepted embedding_column='embedding_joint' as valid
-- since before this migration existed, and experiments/swebench_pro/
-- graph_ingest.py --joint-embeddings has been writing to it and
-- experiments/swebench_pro/compare_embeddings.py reading from it -- but no
-- DDL file in this repo ever created the column. It must have been added
-- by hand, out of band, on whichever database produced Stage 2's real,
-- published result (joint beats split, sign test p=0.0066, n=400).
--
-- task_nodes only, matching retrieval.py's own comment ("embedding_joint
-- only exists on task_nodes -- knowledge_nodes has no alt column").
-- IF NOT EXISTS: safe to apply on a database where this was already added
-- by hand -- this migration formalizes it into version control, it does
-- not assume a clean slate.
ALTER TABLE task_nodes
    ADD COLUMN IF NOT EXISTS embedding_joint VECTOR(1024);

-- Same HNSW/cosine indexing convention already used for the primary
-- embedding column (db/01_ontology.sql:127), applied here for the same
-- reason: any real query against this column without it degrades to a
-- full scan.
CREATE INDEX IF NOT EXISTS idx_tn_embedding_joint
    ON task_nodes USING hnsw (embedding_joint vector_cosine_ops);
