-- Migration 126: the license (and the attribution it requires) an ingestion was made under, on its IngestionContext.
-- Next free migration number: 127.
--
-- WHY: content ingested under an attribution license (CC-BY-4.0, founder ruling 2026-09-29) must (a) carry its
-- credit and (b) stay removable as a class. Every derived object already points back to its IngestionContext
-- (migration 65: artifacts, procedures, evidence, knowledge nodes), and a context outlives its tombstoned rows.
-- Recording the license there makes "everything ingested under CC-BY-4.0" one indexed lookup, which is what
-- `admin license-takedown` uses. Additive, nullable, no backfill (fresh-start rule); idempotent.
--
-- Code that writes these columns only does so when a license is supplied, so a database that has not applied this
-- migration keeps working for everything else.

ALTER TABLE ingestion_contexts
    ADD COLUMN IF NOT EXISTS license_spdx TEXT,
    ADD COLUMN IF NOT EXISTS attribution  JSONB;

CREATE INDEX IF NOT EXISTS idx_ingestion_contexts_license
    ON ingestion_contexts (license_spdx) WHERE license_spdx IS NOT NULL;
