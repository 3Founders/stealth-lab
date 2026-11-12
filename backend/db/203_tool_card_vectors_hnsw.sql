-- 203_tool_card_vectors_hnsw.sql
--
-- Next free number: 202 (moderation promoted action) was highest before this file.
--
-- What this adds: an HNSW index on the narrow vector table the tool router scans per request.
-- `store.candidates` did an exact scan of all 48,124 rows (57 MB) to find the nearest cards;
-- measured 4-6 s per search on Neon. The same table already has the full 1024-dim vector on
-- `tool_cards`; this is the leading 256 dimensions, which is what the router compares.
--
-- Cosine, matching `small <=> $1::vector` in app/tool_routing/store.py. Idempotent.

CREATE INDEX IF NOT EXISTS idx_tool_card_vectors_small
    ON tool_card_vectors USING hnsw (small vector_cosine_ops);
