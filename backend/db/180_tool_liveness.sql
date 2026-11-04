-- Which tool servers answer, checked again every day (app/tool_routing/liveness.py): the registry snapshot in the image only knew
-- what was true when it was made. One row per server, the newest check; cards read from tool_cards carry it.

CREATE TABLE IF NOT EXISTS tool_liveness (
    server      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,                      -- live | auth | dead
    http        INTEGER,
    tool_count  INTEGER,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE tool_liveness ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_liveness FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_liveness FROM authenticated;
    END IF;
END $$;
