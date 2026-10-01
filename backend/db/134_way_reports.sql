-- Migration 134: reports and moderation for published ways.
-- Next free migration number: 135.
--
-- WHY: submit_way accepts a way through an automated screen (no links; an LLM rejects malicious and NSFW
-- content) with no human review behind it, so readers need a way to flag one, and the system needs a way to
-- take it out of retrieval. Control-plane tables: rows live on the control database only (migration 133's
-- role marker keeps shards and search members free of them); the table exists everywhere because every
-- database runs every migration.
--
--   way_reports            one row per (way, reporter): a signed-in user's report and the re-screen verdict.
--   way_moderation_events  append-only audit of every hide / restore / remove / withdraw, with actor and reason.
--
-- Additive, idempotent.

CREATE TABLE IF NOT EXISTS way_reports (
    id               UUID        PRIMARY KEY,
    procedure_id     UUID        NOT NULL,          -- the way's stable id (all versions)
    procedure_row_id UUID,                          -- the version that was reported
    reporter         TEXT        NOT NULL,          -- verified token subject
    category         TEXT        NOT NULL CHECK (category IN ('malicious', 'nsfw', 'spam', 'broken', 'other')),
    detail           TEXT        NOT NULL DEFAULT '',
    screen_verdict   JSONB,                         -- the automated re-screen run for malicious/nsfw reports
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (procedure_id, reporter)
);
CREATE INDEX IF NOT EXISTS idx_way_reports_procedure ON way_reports (procedure_id);

CREATE TABLE IF NOT EXISTS way_moderation_events (
    id               UUID        PRIMARY KEY,
    procedure_id     UUID        NOT NULL,
    procedure_row_id UUID,
    action           TEXT        NOT NULL CHECK (action IN ('hidden', 'restored', 'removed', 'withdrawn')),
    actor            TEXT        NOT NULL,
    reason           TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_way_moderation_events_procedure ON way_moderation_events (procedure_id, created_at);
