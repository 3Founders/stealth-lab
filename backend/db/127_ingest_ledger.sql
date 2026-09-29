-- Migration 127: the ingestion ledger -- one row per source item, for every item a pipeline touches.
-- Next free migration number: 128.
--
-- WHY: the step 0-8 pipelines kept their accounting in process memory. Today's production run (2026-09-29) lost
-- 45 of 62 SkillMD items with no stored reason, dedup across sources and across runs did not exist, and a killed
-- run could not be resumed without re-deciding everything. The rebuilt pipelines (app/ingest/) record every item
-- here, in the same transaction style as the knowledge they write:
--
--   * status 'written'  -- knowledge was written; `objects` names every row it created. Terminal.
--   * status 'rejected' -- a policy said no (held-out, license, duplicate, content changed...). Terminal: a re-run
--                          does not re-decide it. `reason` is a closed code, `detail` the evidence.
--   * status 'failed'   -- infrastructure broke (network, model, database). Retried by the next run, up to the
--                          pipeline's attempt limit; `attempts` counts them.
--
-- dedup_key is the item's identity ACROSS sources and pipelines (for example `task:<instance_id>:resolved`), so the
-- same SWE task arriving from two trajectory corpora is written once. The partial unique index enforces it in the
-- database, not in a Python set.
--
-- Additive, idempotent, no backfill (fresh-start rule).

CREATE TABLE IF NOT EXISTS ingest_ledger (
    pipeline      TEXT        NOT NULL,
    item_key      TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    revision      TEXT        NOT NULL,
    row_ref       TEXT        NOT NULL,
    dedup_key     TEXT,
    status        TEXT        NOT NULL CHECK (status IN ('written', 'rejected', 'failed')),
    reason        TEXT        NOT NULL,
    detail        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    objects       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    run_id        UUID,
    target        TEXT        NOT NULL,
    attempts      INTEGER     NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pipeline, item_key)
);

-- One WRITTEN item per identity, across every pipeline and source.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingest_ledger_written_identity
    ON ingest_ledger (dedup_key) WHERE status = 'written' AND dedup_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ingest_ledger_pipeline_status
    ON ingest_ledger (pipeline, status, reason);

CREATE INDEX IF NOT EXISTS idx_ingest_ledger_run
    ON ingest_ledger (run_id) WHERE run_id IS NOT NULL;

-- Near-duplicate detection for text corpora (SKILL.md): MinHash band keys of every WRITTEN item. A new item that
-- shares a band key with a written one is compared exactly (Jaccard over its shingles) before it is rejected.
CREATE TABLE IF NOT EXISTS ingest_near_dup_bands (
    pipeline  TEXT NOT NULL,
    band_key  TEXT NOT NULL,
    item_key  TEXT NOT NULL,
    PRIMARY KEY (pipeline, band_key, item_key)
);
