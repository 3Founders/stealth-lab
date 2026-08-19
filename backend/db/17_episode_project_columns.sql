-- Real number 17, not "13": IMPLEMENTATION_HANDOFF.md calls this
-- "Migration 13", written before 13_claim_subject_index.sql and
-- 14_observations.sql landed. migrate.py sorts lexically by filename
-- (scripts/migrate.py's own docstring), not by a number a doc used
-- before the file existed, so this is the real next available slot
-- (15 was already skipped historically; 16 is taken by
-- 16_state_projection_index.sql).
--
-- Two things land together, per the handoff's own instruction not to
-- split them and NOT to edit migration 12 (scripts/migrate.py checksums
-- applied files; editing one is a hard error by this repo's own design).
--
-- Idempotent: safe to re-run, same idiom as every other migration here.

-- 1. project_id -- ticket 09 (09-isolation-and-auth.md:84-90) explicitly
-- deferred this "until ticket 06's actual trace/episode schema is being
-- built." That schema landed in migration 12 without it. Added now
-- because episode assembly (ticket 11) is exactly the consumer that
-- groups by repo/workspace, and retrofitting after trace data
-- accumulates is worse than adding it while both tables are still empty
-- in production. cwd + gitBranch are on every real transcript line
-- (confirmed by sniff_schema.py against 36 real sessions, see
-- experiments/episode_assembly/FINDINGS.md) -- project_id is a derived,
-- stable identifier computed from those at collection time, not a new
-- kind of fact the schema has to invent from nothing.
ALTER TABLE agent_traces
    ADD COLUMN IF NOT EXISTS project_id TEXT;

ALTER TABLE episodes
    ADD COLUMN IF NOT EXISTS project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_traces_project ON agent_traces(project_id)
    WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id)
    WHERE project_id IS NOT NULL;

-- 2. episodes columns -- the table (01_ontology.sql) has id/tenant_id/
-- episode_type/content/content_ref/timestamp/metadata, plus t_invalid
-- (migration 12's bi-temporal fix). It has none of what ticket 11's
-- real hierarchical/session-scoped design needs:
--
-- - parent_episode_id: ticket 11 requires hierarchical nesting (a
--   session-level episode containing sub-episodes). Self-referencing,
--   nullable (top-level episodes have none), ON DELETE SET NULL rather
--   than CASCADE -- deleting a parent must not cascade-delete real
--   recorded children; it should just orphan them to top-level, an
--   explicit follow-up decision, not a silent mass deletion.
-- - session_id: episodes assembled from trace data need to trace back
--   to the real agent_traces.session_id they were built from. TEXT, not
--   a FK to agent_traces(trace_id) -- ticket 06's own note (real,
--   confirmed by trace_worker.py) is that trace_id currently always
--   equals session_id (the fallback in _insert_event fires 100% of the
--   time, since the collector never sets a real trace_id), so a hard FK
--   here would be pinning behavior to a fallback that's expected to
--   change once real per-execution trace_ids exist.
-- - start_ts/end_ts: episodes span a duration (that's the entire point
--   of segmentation, per segment.py's real findings) -- the existing
--   single `timestamp` column is a point, not a range, and was already
--   being used loosely for "when was this episode recorded" rather than
--   "when did it start/end". Both added explicitly rather than
--   overloading `timestamp` to mean one or the other.
-- - owner_id/visibility: 03_access.sql never covered this table at all
--   (ticket 09 flags this explicitly) -- same tenant_id-shaped gap this
--   session already found and fixed on knowledge_nodes/observations/
--   agent_traces/trace_events. Fixed here at the schema level; wiring a
--   real writer (whatever eventually turns experiments/episode_assembly's
--   prototype into a real service) is separate, follow-on work -- this
--   migration does not claim that wiring exists yet.
ALTER TABLE episodes
    ADD COLUMN IF NOT EXISTS parent_episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS session_id TEXT,
    ADD COLUMN IF NOT EXISTS start_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS end_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS visibility visibility_level NOT NULL DEFAULT 'public',
    ADD COLUMN IF NOT EXISTS owner_id TEXT;

CREATE INDEX IF NOT EXISTS idx_episodes_parent ON episodes(parent_episode_id)
    WHERE parent_episode_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_public ON episodes(id)
    WHERE visibility = 'public';

-- 3. A2/A7 follow-on, same theme, small enough to land in the same
-- migration rather than a separate one: the code review found
-- drop_count "computed, stored in the file, never read by the worker,
-- no column to land in" -- a real, silent-loss-invisible-in-the-DB gap
-- (the exact failure mode ticket 18's own redaction docstring warns
-- about for a different reason: detected-but-unsurfaced is not the same
-- as fixed). Gives the worker a real column to write the collector's
-- drop_count into per run, so a lossy collector period becomes visible
-- in the database, not just in a local sidecar file nobody queries.
ALTER TABLE agent_traces
    ADD COLUMN IF NOT EXISTS collector_drop_count INTEGER NOT NULL DEFAULT 0;
