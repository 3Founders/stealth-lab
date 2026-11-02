-- 172_room_files.sql
--
-- Next free number: 171 (plan cap) was highest before this file.
--
-- What this adds: files sent in a room (docs/adding_files.md, build order steps 1-2). The message carries only metadata and a file
-- id; the bytes live here, behind the rooms API, never in the message row. A chat-native file card with a bounded server retention
-- window is the WhatsApp shape described in that doc. (The feature comparison that led to this work is
-- docs/open_webui_gap_analysis_2026-11-02.md, which takes its ideas from Open WebUI, https://github.com/open-webui/open-webui;
-- no code was copied.)
--
-- First cut, honestly: bytes are stored in this table (`storage = 'db'`), capped by the application (25 MB by default). The column
-- exists so object storage (R2, Railway Buckets, B2) can replace it without changing the API. `scanned` is false until a malware
-- scanner is wired; the application says so on the upload response and serves every type except pictures, audio, video and PDF as
-- a download only.
--
-- Access: like 154, 155 and 157, RLS is ON with NO policies and the Supabase API roles are revoked; the application checks room
-- membership on every call (services/room_files.py). Retention: `expires_at` (30 days by default); an expired file is not served.
-- The same bytes sent twice in one room are stored once (unique on room_id + sha256). Idempotent.

CREATE TABLE IF NOT EXISTS room_files (
    id          uuid PRIMARY KEY,
    room_id     uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    uploader    text NOT NULL CHECK (char_length(uploader) BETWEEN 1 AND 128),
    sha256      text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size        bigint NOT NULL CHECK (size >= 0),
    mime        text NOT NULL CHECK (char_length(mime) <= 100),
    name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    state       text NOT NULL DEFAULT 'clean' CHECK (state IN ('scanning', 'clean', 'quarantine', 'expired')),
    scanned     boolean NOT NULL DEFAULT false,
    storage     text NOT NULL DEFAULT 'db' CHECK (storage IN ('db', 's3')),
    storage_key text,
    data        bytea,
    iv          text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS room_files_room_sha ON room_files (room_id, sha256);
CREATE INDEX IF NOT EXISTS room_files_expiry ON room_files (expires_at);

ALTER TABLE room_files ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON room_files FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON room_files FROM authenticated';
    END IF;
END $$;
