-- Migration 72 (Ingestion + Knowledge hardening, Plan A / G14):
-- retrieval index-freshness contract.
--
-- Next free migration number: 73.
--
-- WHY
--   Spec §B37 wants retrieval to expose canonical-vs-indexed revision and
--   an index lag so a stale index cannot cause an agent to act on
--   outdated Procedure text. StealthLab has NO detached index: the
--   embedding and every authoritative column live on the `procedures`
--   row, retrieval returns bare ids, and every candidate is re-hydrated
--   from its live row before ranking / selection (`_resolve_live_procedure`
--   in the MCP path; `visibility_predicate` re-fetch in the applicability
--   cascade). So the *correctness* guarantee the freshness contract
--   exists to provide is already met structurally.
--
--   The one real staleness is the embedding lagging its source text (row
--   edited, re-embed not yet run). This migration makes that lag
--   VISIBLE and QUERYABLE rather than introducing a second index.
--
-- WHAT THIS IS
--   * `procedures.retrieval_indexed_at` -- set to now() by the embedding
--     write path (scripts/backfill_procedure_embeddings.py) whenever the
--     vector + retrieval_document are (re)computed for a row.
--   * `procedure_index_lag` -- a view over LIVE procedures whose index is
--     behind canonical, with a `reason`. Recipe-version drift
--     (`retrieval_document_version` vs the current
--     `RETRIEVAL_DOCUMENT_VERSION` constant) is deliberately checked in
--     the service layer, not hard-coded here, so bumping the constant
--     never needs a new migration for the view.
--
-- Fresh-start rule: additive column, nullable, NO backfill (existing rows
-- read as `never_indexed` until the backfill job touches them, which is
-- correct). Idempotent.

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS retrieval_indexed_at TIMESTAMPTZ;

-- "live procedures whose embedding index is behind the canonical row"
CREATE OR REPLACE VIEW procedure_index_lag AS
SELECT
    p.id,
    p.procedure_id,
    p.version,
    p.name,
    p.retrieval_indexed_at,
    p.updated_at,
    p.retrieval_document_version,
    CASE
        WHEN p.embedding IS NULL                       THEN 'no_embedding'
        WHEN p.retrieval_indexed_at IS NULL            THEN 'never_indexed'
        WHEN p.retrieval_indexed_at < p.updated_at     THEN 'stale_since_update'
        ELSE 'current'
    END AS reason
FROM procedures p
WHERE p.t_invalid IS NULL
  AND (
        p.embedding IS NULL
     OR p.retrieval_indexed_at IS NULL
     OR p.retrieval_indexed_at < p.updated_at
  );

CREATE INDEX IF NOT EXISTS idx_procedures_retrieval_indexed_at
    ON procedures (retrieval_indexed_at) WHERE t_invalid IS NULL;
