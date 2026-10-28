-- 157_rooms.sql
--
-- Next free number: 156 (org wallet) was highest before this file.
--
-- What this adds: rooms that follow a person across devices (docs/rooms_design.md, rev 2). A room is an append-only log of events
-- (messages, edits, deletes, reactions, notes) shared by the people in it and, later, by agents. Every device keeps a full copy
-- and syncs by asking "give me events after seq N"; the server is the ordering and relay peer, not the only copy.
--
-- OPEN rooms (mode 'open', the default): the server stores the text, redacted at the edge like relay tasks (services/rooms.py), so
-- Claude, ChatGPT and other connectors can read and post. They are NOT end-to-end encrypted, and the product says so. 'private'
-- is reserved for end-to-end encrypted rooms (MLS) later: `ciphertext` holds those bytes and must never go through redaction.
--
-- Ordering: `rooms.next_seq` is bumped in the same statement that inserts an event, which takes the room row's lock, so two
-- writers cannot get out of order and a reader that has seen seq N can never miss an event that commits later with a lower seq.
-- Event ids are chosen by the client, so a retried send is idempotent.
--
-- Access: like 154 and 155, RLS is ON with NO policies and the Supabase API roles are revoked, so the public anon key can neither
-- read nor write a row. The application checks membership on every call (services/rooms.py).
--
-- Retention: none. History is kept until a person deletes it or the owner deletes the room. Idempotent.

CREATE TABLE IF NOT EXISTS rooms (
    id          uuid PRIMARY KEY,
    owner_sub   text NOT NULL CHECK (char_length(owner_sub) BETWEEN 1 AND 128),
    title       text NOT NULL CHECK (title ~ '^#[a-z0-9][a-z0-9_.-]{0,78}$'),
    mode        text NOT NULL DEFAULT 'open' CHECK (mode IN ('open', 'private')),
    org_id      uuid,
    next_seq    bigint NOT NULL DEFAULT 0 CHECK (next_seq >= 0),
    created_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

-- one live room per (owner, title): a second device adopting "#general" finds the first one instead of making another
CREATE UNIQUE INDEX IF NOT EXISTS rooms_owner_title_live ON rooms (owner_sub, title) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS room_members (
    id          uuid PRIMARY KEY,
    room_id     uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    member_sub  text CHECK (member_sub IS NULL OR char_length(member_sub) BETWEEN 1 AND 128),
    agent_id    uuid REFERENCES delegate_agents(id) ON DELETE CASCADE,
    role        text NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
    added_by    text,
    added_at    timestamptz NOT NULL DEFAULT now(),
    removed_at  timestamptz,
    CHECK ((member_sub IS NULL) <> (agent_id IS NULL))
);

-- a person or an agent is in a room once; re-adding someone who left reactivates the same row
CREATE UNIQUE INDEX IF NOT EXISTS room_members_person ON room_members (room_id, member_sub);
CREATE UNIQUE INDEX IF NOT EXISTS room_members_agent ON room_members (room_id, agent_id);
CREATE INDEX IF NOT EXISTS room_members_by_person ON room_members (member_sub) WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS room_events (
    id             uuid PRIMARY KEY,
    room_id        uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    seq            bigint NOT NULL,
    sender_sub     text NOT NULL CHECK (char_length(sender_sub) BETWEEN 1 AND 128),
    sender_kind    text NOT NULL CHECK (sender_kind IN ('person', 'agent')),
    sender_handle  text NOT NULL CHECK (char_length(sender_handle) BETWEEN 1 AND 80),
    kind           text NOT NULL CHECK (kind IN ('msg', 'edit', 'delete', 'react', 'system', 'task', 'task_update', 'note')),
    body           text NOT NULL DEFAULT '' CHECK (char_length(body) <= 8000),
    ciphertext     bytea,
    target         uuid,
    reply_to       uuid,
    lamport        bigint NOT NULL DEFAULT 0 CHECK (lamport >= 0),
    mentions       text[] NOT NULL DEFAULT '{}',
    depth          integer NOT NULL DEFAULT 0 CHECK (depth BETWEEN 0 AND 4),
    device_id      text CHECK (device_id IS NULL OR char_length(device_id) <= 64),
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (room_id, seq)
);

CREATE INDEX IF NOT EXISTS room_events_target_idx ON room_events (room_id, target) WHERE target IS NOT NULL;

CREATE TABLE IF NOT EXISTS room_reads (
    room_id        uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    member_sub     text NOT NULL CHECK (char_length(member_sub) BETWEEN 1 AND 128),
    last_read_seq  bigint NOT NULL DEFAULT 0 CHECK (last_read_seq >= 0),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, member_sub)
);

CREATE TABLE IF NOT EXISTS room_invites (
    id           uuid PRIMARY KEY,
    room_id      uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    secret_hash  text NOT NULL,
    created_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    max_uses     integer NOT NULL CHECK (max_uses BETWEEN 1 AND 100),
    uses         integer NOT NULL DEFAULT 0 CHECK (uses >= 0),
    revoked_at   timestamptz
);

ALTER TABLE rooms ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_invites ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rooms', 'room_members', 'room_events', 'room_reads', 'room_invites'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON %I FROM anon', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON %I FROM authenticated', t);
        END IF;
    END LOOP;
END
$$;
