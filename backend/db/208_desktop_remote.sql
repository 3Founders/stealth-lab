-- Desktop sessions callable from the rooms (app/services/desktop_remote.py, docs/desktop_remote.md).
-- A signed-in desktop app reports its sessions (title, project folder name, a short summary of the latest answer: never
-- the conversation) and polls for asks. A room's @kell hands a message to the session it is meant for; the desktop runs
-- it and posts the answer into the room. A person lets a teammate call their desktop with a grant whose level decides
-- what that teammate's asks may do there.
CREATE TABLE IF NOT EXISTS desktop_devices (
    id          text PRIMARY KEY,                 -- chosen by the app, stable per install
    owner_id    text NOT NULL,
    name        text NOT NULL DEFAULT '',          -- the computer's name, as the app reports it
    last_seen   timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_desktop_devices_owner ON desktop_devices (owner_id);

CREATE TABLE IF NOT EXISTS desktop_sessions (
    device_id   text NOT NULL REFERENCES desktop_devices (id) ON DELETE CASCADE,
    session_id  text NOT NULL,
    owner_id    text NOT NULL,
    title       text NOT NULL DEFAULT '',
    project     text NOT NULL DEFAULT '',         -- the folder's name only, never its full path
    summary     text NOT NULL DEFAULT '',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_desktop_sessions_owner ON desktop_sessions (owner_id, updated_at DESC);

-- owner lets grantee call their desktop. level: answer (read only, in a copy of the session), approve (the owner approves
-- each ask, which then runs with full access), full (runs like the owner's own ask, in a copy of the session).
CREATE TABLE IF NOT EXISTS desktop_grants (
    owner_id    text NOT NULL,
    grantee_id  text NOT NULL,
    level       text NOT NULL CHECK (level IN ('answer', 'approve', 'full')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, grantee_id)
);
CREATE INDEX IF NOT EXISTS idx_desktop_grants_grantee ON desktop_grants (grantee_id);

CREATE TABLE IF NOT EXISTS desktop_asks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    text NOT NULL,                     -- whose desktop
    asker_id    text NOT NULL,
    device_id   text NOT NULL,
    session_id  text NOT NULL,
    room_id     uuid,                              -- where the answer is posted; NULL for the sessions panel
    text        text NOT NULL,
    level       text NOT NULL CHECK (level IN ('owner', 'answer', 'approve', 'full')),
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN
                ('needs_approval', 'queued', 'running', 'waiting_permission', 'done', 'failed', 'denied', 'cancelled')),
    answer      text NOT NULL DEFAULT '',          -- the answer so far while running, then the whole answer
    waiting     jsonb,                             -- the permission the run waits on {request_id, tool, detail}
    decision    text,                              -- the owner's reply to it: once | always | reject
    error       text NOT NULL DEFAULT '',
    run_session text,                              -- the session the ask ran in (a fork for a teammate)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    claimed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS idx_desktop_asks_device ON desktop_asks (device_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_desktop_asks_owner ON desktop_asks (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_desktop_asks_asker ON desktop_asks (asker_id, created_at DESC);
