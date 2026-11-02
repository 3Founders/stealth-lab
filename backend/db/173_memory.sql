-- 173_memory.sql
--
-- Next free number: 172 (room files) was highest before this file.
--
-- What this adds: a per-account memory for kell, shaped like Supermemory's memoryrepo (https://github.com/supermemoryai/memoryrepo) and
-- the "dreaming" idea in ChatGPT's memory: a short always-loaded index (MEMORY.md) plus topic files (people/, projects/, preferences/,
-- notes/, knowledge/), every change kept as a version so any change can be undone, and a "dream" log of background tidy-ups. Ideas only;
-- no code copied.
--
-- Access: like 154, 155, 157 and 172, RLS is ON with NO policies and the Supabase API roles are revoked; the application scopes every
-- statement by `subject` (services/memory.py). Nothing here is deleted except by the person's own "Delete all and turn off".
-- Memory is OFF until the person turns it on. `share_agents` says whether their connected agents (Claude, Cursor, ...) may read and
-- write it through the MCP tools; it defaults to true and is shown to the person when memory is first turned on. Idempotent.

CREATE TABLE IF NOT EXISTS memory_settings (
    subject        text PRIMARY KEY CHECK (char_length(subject) BETWEEN 1 AND 128),
    enabled        boolean NOT NULL DEFAULT false,
    share_agents   boolean NOT NULL DEFAULT true,
    last_dream_at  timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_files (
    subject     text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 128),
    path        text NOT NULL CHECK (char_length(path) BETWEEN 1 AND 100),
    body        text NOT NULL DEFAULT '' CHECK (char_length(body) <= 20000),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subject, path)
);

CREATE TABLE IF NOT EXISTS memory_versions (
    id          uuid PRIMARY KEY,
    subject     text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 128),
    path        text NOT NULL,
    version     integer NOT NULL,
    body        text NOT NULL CHECK (char_length(body) <= 20000),
    by          text NOT NULL CHECK (char_length(by) BETWEEN 1 AND 80),   -- 'you', 'kell', 'dream', or a connector's handle
    reason      text NOT NULL DEFAULT '' CHECK (char_length(reason) <= 300),
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memory_versions_by_file ON memory_versions (subject, path, version DESC);

CREATE TABLE IF NOT EXISTS memory_dreams (
    id        uuid PRIMARY KEY,
    subject   text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 128),
    at        timestamptz NOT NULL DEFAULT now(),
    changed   text[] NOT NULL DEFAULT '{}',
    note      text NOT NULL DEFAULT '' CHECK (char_length(note) <= 500)
);

CREATE INDEX IF NOT EXISTS memory_dreams_by_person ON memory_dreams (subject, at DESC);

ALTER TABLE memory_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_dreams ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['memory_settings', 'memory_files', 'memory_versions', 'memory_dreams'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON %I FROM anon', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON %I FROM authenticated', t);
        END IF;
    END LOOP;
END $$;
