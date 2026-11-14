-- A desktop session's recent conversation, on request (app/services/desktop_remote.py request_history, docs/desktop_remote.md).
-- The owner opens one of their sessions in the rooms' Desktop sessions panel: the panel asks for it, the desktop answers with the
-- session's recent lines (stored in `answer` as JSON), and the panel shows the real conversation instead of only what was asked
-- from the panel. Only the owner can ask; a desktop is handed these only when it says it can answer them.
ALTER TABLE desktop_asks ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'ask';
DO $$ BEGIN
    ALTER TABLE desktop_asks ADD CONSTRAINT desktop_asks_kind CHECK (kind IN ('ask', 'history'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
