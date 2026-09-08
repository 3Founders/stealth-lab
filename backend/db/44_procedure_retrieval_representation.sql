-- Next free migration number: 45.
--
-- Retrieval representation contract (see
-- .scratch/retrieval-representation-and-frontend-plan.md and
-- app/services/retrieval_document.py).
--
-- Before this, procedures.embedding_text_hash recorded the sha256 of
-- whatever text was embedded, but nothing recorded WHICH representation
-- recipe produced that text -- so a corpus embedded under two recipes was
-- indistinguishable and a re-embed could not be made resumable. And there
-- was no human-facing name/description at all; the UI showed the raw
-- machine slug (procedures.name).
--
-- This migration adds, additively and idempotently (same ADD COLUMN IF
-- NOT EXISTS idiom as 19_procedures_embedding.sql and every other ALTER
-- in db/):
--
--   retrieval_document          -- the exact canonical text that was
--                                  embedded, kept for inspection and as
--                                  the lexical-retrieval tsvector source
--   retrieval_document_version  -- e.g. 'procdoc_v1'; the backfill
--                                  re-embeds every row whose stored
--                                  version is older than the code's
--                                  RETRIEVAL_DOCUMENT_VERSION
--   retrieval_document_sha256   -- sha256(retrieval_document); lets the
--                                  backfill skip a row whose canonical
--                                  text did not actually change
--   display_name                -- human-facing title (NOT an identifier;
--                                  procedures.name stays the stable
--                                  machine handle)
--   display_description         -- human-facing capability sentence
--   display_metadata_version    -- e.g. 'disp_v1' / 'disp_v1_deslug_only'
--
-- No data backfill here (fresh-start rule 1): the columns are nullable,
-- and scripts/backfill_procedure_embeddings.py --representation /
-- --display-metadata populates live rows out of band, resumably.

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS retrieval_document          TEXT,
    ADD COLUMN IF NOT EXISTS retrieval_document_version   TEXT,
    ADD COLUMN IF NOT EXISTS retrieval_document_sha256    TEXT,
    ADD COLUMN IF NOT EXISTS display_name                 TEXT,
    ADD COLUMN IF NOT EXISTS display_description          TEXT,
    ADD COLUMN IF NOT EXISTS display_metadata_version     TEXT;

-- Lexical retrieval leg (Part 5): a full-text index over the canonical
-- document so find_applicable_procedures can add a keyword candidate leg
-- that matches real procedural content (name/goal/steps/tools/domain),
-- not just the short goal. GIN over an expression index -- no generated
-- column, so nothing to keep in sync on write.
CREATE INDEX IF NOT EXISTS idx_procedures_retrieval_document_fts
    ON procedures
    USING gin (to_tsvector('english', coalesce(retrieval_document, '')));

-- Backfill resumability: a partial index over exactly the rows the
-- --representation backfill still has to touch (live rows not yet on the
-- current recipe). Cheap because it is empty once the backfill completes.
CREATE INDEX IF NOT EXISTS idx_procedures_retrieval_document_pending
    ON procedures (id)
    WHERE t_invalid IS NULL
      AND (retrieval_document_version IS NULL
           OR retrieval_document_version <> 'procdoc_v1');
