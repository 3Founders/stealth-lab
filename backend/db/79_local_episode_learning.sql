-- Migration 79: local MCP-execution learning wiring.
--
-- Closes the confirmed gap (found by reading durable_run.py/durable_resume.py/
-- observations.py/claims.py directly, not from the audit docs, which are
-- stale on this point): execution_runs.id is never linked to episodes.id
-- anywhere in the codebase, and observation_events.event_id is hard-FK'd to
-- trace_events(id) only, so nothing derived from a local MCP-driven
-- execution_run can ever be cited as Observation provenance. Both are real,
-- additive, idempotent schema seams -- no new tables, no changes to
-- procedures/evidence/traces/episode_links.
--
-- Next free number: 79 (78 is highest).

-- ============================================================
-- 1. episodes: local execution runs get a real Episode row.
--    episodes already carries everything a local Episode needs
--    (parent_episode_id, session_id, start_ts/end_ts, visibility,
--    owner_id, scope_type/scope_entity_id from migration 21/17) --
--    only a link back to the execution_runs row it was assembled
--    from, and a new episode_type value, are missing.
-- ============================================================
-- ON DELETE CASCADE matches every other real child-of-one-execution_run
-- table (execution_run_nodes: migration 36, verification_results:
-- migration 55, execution_run_events: migration 61) -- an execution_run
-- is never actually deleted in production, but a plain FK with no
-- delete policy broke the real e2e test suite's own cleanup (`DELETE
-- FROM execution_runs WHERE id=...`), found by actually running it
-- against a real throwaway Postgres in this session.
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS execution_run_id UUID
    REFERENCES execution_runs(id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_execution_run
    ON episodes(execution_run_id) WHERE execution_run_id IS NOT NULL;

ALTER TABLE episodes DROP CONSTRAINT IF EXISTS episodes_episode_type_check;
ALTER TABLE episodes ADD CONSTRAINT episodes_episode_type_check
    CHECK (episode_type IN ('document', 'trace', 'debate_transcript', 'execution'));

-- ============================================================
-- 2. observation_events: allow an Observation to cite an
--    execution_run_events row instead of a trace_events row.
--    event_id was part of the table's composite PK (NOT NULL by
--    virtue of that), so making it optional requires a surrogate
--    key -- done once, guarded so re-running this file is a no-op.
-- ============================================================
ALTER TABLE observation_events ADD COLUMN IF NOT EXISTS execution_run_event_id UUID
    REFERENCES execution_run_events(id);
ALTER TABLE observation_events ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();

UPDATE observation_events SET id = gen_random_uuid() WHERE id IS NULL;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'observation_events_pkey' AND conrelid = 'observation_events'::regclass
           AND array_length(conkey, 1) = 2
    ) THEN
        ALTER TABLE observation_events DROP CONSTRAINT observation_events_pkey;
        ALTER TABLE observation_events ALTER COLUMN event_id DROP NOT NULL;
        ALTER TABLE observation_events ALTER COLUMN id SET NOT NULL;
        ALTER TABLE observation_events ADD CONSTRAINT observation_events_pkey PRIMARY KEY (id);
    END IF;
END $$;

-- Same uniqueness the old composite PK gave the trace_events side;
-- an equivalent partial-unique for the execution_run_events side.
CREATE UNIQUE INDEX IF NOT EXISTS uq_observation_events_trace_event
    ON observation_events(observation_id, event_id) WHERE event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_observation_events_run_event
    ON observation_events(observation_id, execution_run_event_id) WHERE execution_run_event_id IS NOT NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'observation_events_exactly_one_event_chk'
    ) THEN
        ALTER TABLE observation_events ADD CONSTRAINT observation_events_exactly_one_event_chk
            CHECK ((event_id IS NOT NULL) <> (execution_run_event_id IS NOT NULL));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_observation_events_run_event ON observation_events(execution_run_event_id)
    WHERE execution_run_event_id IS NOT NULL;

-- ============================================================
-- 3. episodes.metadata carries the consolidation status marker
--    (consolidated_at / consolidation_state) -- no new column
--    needed, episodes.metadata is already JSONB with no fixed
--    shape; documented here so the convention is discoverable.
-- ============================================================
