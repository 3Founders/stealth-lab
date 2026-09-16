-- Migration 86: `goals.simhash` -- always-on, zero-cost text dedup tier
-- (ingestion.md Sec 8 tier "2.5"), sitting between tier 1/2 (exact
-- name/alias, migration 83) and tier 3/4 (embedding cosine similarity,
-- migration 84).
--
-- WHY: founder directive (2026-09-16, "aggressive dedup for goals") asked
-- to discuss a hashing-based dedup mechanism. Explicitly discussed and
-- decided: hashing the EMBEDDING vector (LSH) would be redundant --
-- migration 84 already gives an exact, fast nearest-neighbor search
-- (HNSW index + real pgvector `<=>` cosine distance) at this table's
-- scale, and a hash of the embedding could only be a lossier version of
-- a lookup we already do exactly. A 64-bit SimHash of the goal's
-- NORMALIZED TEXT is a genuinely different, useful signal instead: it
-- catches near-duplicate phrasings (word-order changes, minor edits, a
-- word inserted/removed) via cheap Hamming distance on a bare integer,
-- with NO embedder/LLM call required -- unlike tier 3/4/5, this tier
-- costs nothing and so runs unconditionally, before the opt-in tiers.
-- User's own final, explicit decision: "Skip vector-hashing, build
-- text-SimHash only."
--
-- Nullable, no backfill of the ~3000+ existing rows in this migration
-- (CLAUDE.md hard rule 1: "No backfills... Legacy rows stay quarantined."
-- -- an existing goal with simhash IS NULL simply never matches this tier;
-- it still gets tier-1/2/3/4/5 dedup exactly as before. Only NEWLY
-- written/re-matched goals populate this column going forward).
--
-- Next free migration number confirmed: 86 (highest existing before this
-- file was 85).

ALTER TABLE goals ADD COLUMN IF NOT EXISTS simhash BIGINT;

-- BIGINT has no native bit-Hamming-distance operator in stock Postgres,
-- so the candidate-narrowing query app/services/goals.py runs is a plain
-- equality/small-shortlist scan, not an index-accelerated range query --
-- this index exists only to make "is this hash already present at all"
-- and "rows sharing this hash" cheap, not to accelerate arbitrary Hamming
-- radius search (out of scope; see goals.py's own docstring for the
-- shortlist-then-compare approach this enables).
CREATE INDEX IF NOT EXISTS idx_goals_simhash
    ON goals (simhash)
    WHERE t_invalid IS NULL AND simhash IS NOT NULL;
