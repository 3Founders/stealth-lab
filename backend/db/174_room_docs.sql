-- 174_room_docs.sql
--
-- Next free number: 173 (memory) was highest before this file.
--
-- What this adds: documents that live in a room (the "canvas"), written by people and agents together, like a shared document. Everyone in
-- the room sees the same document, edits it at the same time, and every save is a version that can be restored. Two edits made at the same
-- moment are merged by the service (services/room_docs.py, a three-way merge of the paragraph-level changes) and nothing is silently lost:
-- the version before the merge is kept. A canvas block an agent writes in a message becomes a version of the document with that title.
-- (The side-panel idea is from Open WebUI's artifacts, https://github.com/open-webui/open-webui; no code copied.)
--
-- Access: like 154, 155, 157, 172 and 173, RLS is ON with NO policies and the Supabase API roles are revoked; the application checks room
-- membership on every call. Documents follow the room: they are removed with it. Idempotent.

CREATE TABLE IF NOT EXISTS room_docs (
    id          uuid PRIMARY KEY,
    room_id     uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title       text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    kind        text NOT NULL DEFAULT 'document' CHECK (kind IN ('document', 'code', 'html')),
    body        text NOT NULL DEFAULT '' CHECK (char_length(body) <= 200000),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_by  text NOT NULL CHECK (char_length(created_by) BETWEEN 1 AND 128),
    updated_by  text NOT NULL CHECK (char_length(updated_by) BETWEEN 1 AND 128),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS room_docs_title_live ON room_docs (room_id, lower(title)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS room_docs_by_room ON room_docs (room_id, updated_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS room_doc_versions (
    id          uuid PRIMARY KEY,
    doc_id      uuid NOT NULL REFERENCES room_docs(id) ON DELETE CASCADE,
    version     integer NOT NULL,
    body        text NOT NULL CHECK (char_length(body) <= 200000),
    by_handle   text NOT NULL CHECK (char_length(by_handle) BETWEEN 1 AND 80),   -- the person's name, or an agent's @handle
    note        text NOT NULL DEFAULT '' CHECK (char_length(note) <= 200),         -- 'merged', 'restored version 3', ''
    src         text CHECK (src IS NULL OR char_length(src) <= 64),                -- the message that made it (so a replay adds nothing)
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS room_doc_versions_number ON room_doc_versions (doc_id, version);
CREATE UNIQUE INDEX IF NOT EXISTS room_doc_versions_src ON room_doc_versions (doc_id, src) WHERE src IS NOT NULL;

ALTER TABLE room_docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_doc_versions ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['room_docs', 'room_doc_versions'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON %I FROM anon', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON %I FROM authenticated', t);
        END IF;
    END LOOP;
END $$;
