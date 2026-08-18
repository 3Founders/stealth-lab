-- Step 2 of the memory-substrate implementation order (handoff.md):
-- trace_events + trace-header + the collector/job pipeline (tickets 06,
-- 16, 18). This file is the schema half; scripts/collector.py and
-- scripts/trace_worker.py are the code half.

-- ============================================================
-- Ticket 18 fix: episodes need a real bi-temporal column before
-- deletion can be tombstoned. Confirmed missing during ticket 18's own
-- review: episodes had id/tenant_id/episode_type/content/content_ref/
-- timestamp/metadata only.
-- ============================================================
ALTER TABLE episodes
    ADD COLUMN IF NOT EXISTS t_invalid TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_episodes_validity ON episodes(t_invalid);

-- Ticket 18 fix: episode_links was ON DELETE CASCADE, the opposite of
-- tombstoning -- a raw DELETE on episodes would silently destroy the
-- links a tombstone is supposed to preserve. Confirmed real constraint
-- name against a live database before writing this
-- (episode_links_episode_id_fkey, Postgres's own default naming for this
-- column/table pair -- IF EXISTS guards against a differently-named
-- constraint on some other real database).
ALTER TABLE episode_links DROP CONSTRAINT IF EXISTS episode_links_episode_id_fkey;
ALTER TABLE episode_links
    ADD CONSTRAINT episode_links_episode_id_fkey
    FOREIGN KEY (episode_id) REFERENCES episodes(id);
-- Deliberately no ON DELETE clause -- Postgres's default (NO ACTION) means
-- a hard DELETE on an episode that still has real links now fails loudly
-- at the database, instead of silently cascading. Tombstoning
-- (UPDATE ... SET t_invalid = now()) is the correct path and is
-- unaffected by this -- it never touches episode_links at all.


-- ============================================================
-- Ticket 06: the trace-header table. Deliberately left unnamed by ticket
-- 06 itself ("Naming note... Not yet specified" in map.md) -- agent_traces
-- is a provisional name, chosen to avoid colliding with the existing,
-- differently-shaped `traces` table (kept untouched, per ticket 06's own
-- answer), not a settled decision. Rename freely if a better name
-- surfaces; nothing downstream depends on this specific string yet.
--
-- One row per causally-connected execution (one turn / one subagent run
-- -- see ticket 06's own open question on the exact boundary rule).
-- Fields follow spec.md's Identity/Intent/Environment groups directly
-- (ticket 06's answer, citing spec.md's own field list).
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id          TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    parent_trace_id   TEXT,               -- self-referential, no FK -- same
                                           -- deliberate choice as traces.parent_trace_id
    agent_id          TEXT,
    actor_id          TEXT,
    provider          TEXT,               -- e.g. 'claude-code'
    provider_version  TEXT,
    intent            TEXT,               -- spec.md's Intent group (user_goal)
    cwd               TEXT,               -- spec.md's Environment group
    repo              TEXT,
    branch            TEXT,
    commit_hash       TEXT,
    started_at        TIMESTAMPTZ NOT NULL,
    ended_at          TIMESTAMPTZ,
    outcome           TEXT,               -- nullable, unlike traces.outcome --
                                           -- most Claude Code events have none
                                           -- (ticket 06's own finding)
    schema_version    TEXT NOT NULL,      -- ticket 06: stamped at normalization
                                           -- time, never trusted from the provider
    visibility        visibility_level NOT NULL DEFAULT 'public',
    owner_id          TEXT,               -- ticket 09's pair rule, both columns present
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_session ON agent_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_traces_visibility ON agent_traces(visibility)
    WHERE visibility = 'private';


-- ============================================================
-- Ticket 06: the atomic event table. One row per tool call / model
-- invocation / lifecycle event. event_type is deliberately TEXT, not a
-- CHECK-constrained enum -- a hard 3-value CHECK is exactly what made the
-- existing `traces` table unable to hold Claude Code's real ~31 event
-- types (ticket 06's own reasoning for why `traces` couldn't be reused).
-- ============================================================
CREATE TABLE IF NOT EXISTS trace_events (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id          TEXT NOT NULL REFERENCES agent_traces(trace_id),
    session_id        TEXT NOT NULL,
    sequence          BIGINT NOT NULL,     -- monotonic within session, collector-assigned
    event_type        TEXT NOT NULL,
    "timestamp"       TIMESTAMPTZ NOT NULL,
    actor_id          TEXT,
    tool_name         TEXT,
    tool_call_id      TEXT,
    parent_event_id   UUID REFERENCES trace_events(id),
    tool_input        JSONB,
    tool_output       JSONB,
    success           BOOLEAN,
    duration_ms        INTEGER,
    permission_state  TEXT,
    error             TEXT,
    -- Ticket 06's dedup answer: hooks carry no native event id, so the
    -- collector computes this deterministically (session_id + event
    -- type + sequence + payload hash) rather than trusting one to
    -- arrive pre-formed. Real idempotency, mirroring /v1/traces'
    -- existing ON CONFLICT DO NOTHING pattern.
    dedup_key         TEXT NOT NULL UNIQUE,
    -- Ticket 06's raw-payload answer: large tool outputs get a pointer,
    -- not inlined -- same idiom as episodes.content_ref.
    raw_payload_ref   TEXT,
    schema_version    TEXT NOT NULL,
    visibility        visibility_level NOT NULL DEFAULT 'public',
    owner_id          TEXT,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trace_events_trace ON trace_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_events_session_seq ON trace_events(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_trace_events_visibility ON trace_events(visibility)
    WHERE visibility = 'private';


-- ============================================================
-- Ticket 16: the durability/compilation job table. SKIP LOCKED makes a
-- future multi-worker deployment safe without redesign, even though
-- milestone 1 runs exactly one in-process worker.
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id            BIGSERIAL PRIMARY KEY,
    job_type      TEXT NOT NULL,      -- e.g. 'normalize_trace_event' -- extensible,
                                       -- deliberately not CHECK-constrained yet;
                                       -- episode assembly (ticket 11) will add its own
    payload       JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_pending ON ingestion_jobs(id)
    WHERE status = 'pending';
