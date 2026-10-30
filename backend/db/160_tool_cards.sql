-- 160_tool_cards.sql
--
-- Next free number: 159 (profile photos) was highest before this file.
--
-- What this adds: the tool router's catalog (app/tool_routing, docs/tool_routing_plan.md) as rows instead of files the
-- server loads. Before this every server process read 48k cards, built a keyword index and loaded 89 MB of vectors at
-- startup (about 2 minutes and 430 MB each), and a redeploy threw the vectors away (35 minutes of embedding again). Now
-- the server fills these tables itself from the snapshot shipped in the image (app/tool_routing/store.py sync_catalog,
-- under an advisory lock, writing only cards that changed -- a rewritten row is re-inserted into the vector index -- and
-- embedding only cards whose text changed) and asks them for candidates per request: full-text search on search_tsv
-- with the request's rare words (tool_terms: how many cards hold each word), and the nearest neighbours by an exact
-- scan of tool_card_vectors.small, the first 256 of the 1024 dimensions (Qwen3-Embedding is trained so the leading
-- dimensions stand alone), in a table of its own so the scan reads 54 MB, not the cards' text; the full embedding gives
-- those candidates' exact similarity. No HNSW index: on the 48k cards it took 3-18 s a query once it no longer fit the
-- buffers (a 400 MB index); the narrow scan took 0.15 s, the same scan over the wide table 1 s.
--
-- One row per card: an MCP Registry server today (id "registry:<name>"). The text is third-party data: matched and
-- shown, never followed. No personal data. Access: application only, like 154/155/157/159 (RLS on, no policies, the
-- Supabase API roles revoked).
--
-- Retention: the catalog is replaced by each snapshot (rows no longer in it are deleted). Idempotent.

CREATE TABLE IF NOT EXISTS tool_cards (
    id              text PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 300),
    source          text NOT NULL DEFAULT 'registry',
    name            text NOT NULL,
    title           text NOT NULL DEFAULT '',
    description     text NOT NULL DEFAULT '',
    server          text NOT NULL DEFAULT '',
    goals           text[] NOT NULL DEFAULT '{}',
    how             text NOT NULL DEFAULT '',
    url             text NOT NULL DEFAULT '',
    expansion       text NOT NULL DEFAULT '',
    tools           text[] NOT NULL DEFAULT '{}',
    tools_text      text NOT NULL DEFAULT '',
    extra           jsonb NOT NULL DEFAULT '{}'::jsonb,   -- probe status, tool count, stars, archived, verified, owner count
    text_hash       text NOT NULL,                        -- of the text that is embedded: a change means a new embedding
    card_hash       text NOT NULL DEFAULT '',             -- of the whole card: an unchanged card is not rewritten
    search_tsv      tsvector,
    embedding       vector(1024),
    embedding_model text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tool_cards_tsv ON tool_cards USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_tool_cards_goals ON tool_cards USING gin (goals);

-- the first-stage vectors: the leading 256 dimensions of tool_cards.embedding, renormalised
CREATE TABLE IF NOT EXISTS tool_card_vectors (
    id    text PRIMARY KEY REFERENCES tool_cards (id) ON DELETE CASCADE,
    small vector(256) NOT NULL
);

-- how many cards hold each search word (ts_stat over search_tsv), refreshed by each sync that writes cards
CREATE TABLE IF NOT EXISTS tool_terms (
    lexeme text PRIMARY KEY,
    ndoc   integer NOT NULL
);

ALTER TABLE tool_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_terms ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_card_vectors ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_cards FROM anon;
        REVOKE ALL ON tool_terms FROM anon;
        REVOKE ALL ON tool_card_vectors FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_cards FROM authenticated;
        REVOKE ALL ON tool_terms FROM authenticated;
        REVOKE ALL ON tool_card_vectors FROM authenticated;
    END IF;
END $$;
