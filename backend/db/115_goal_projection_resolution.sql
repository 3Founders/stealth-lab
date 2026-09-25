-- Migration 115: Goal projection carries resolution and creation time. Next free number: 116.
--
-- Why: Goal lists, root browsing, search pages and hierarchy coverage must work when
-- canonical Goals live on many shard databases. They filter by resolution and sort by
-- creation time; both were only readable from the canonical `goals` table, i.e. only
-- for Goals homed on the control database. With these two derived columns the control
-- database can filter, order and page ALL Goals, then read just the page's canonical
-- rows from their home shards.
--
-- `goals.resolved_at` stays the ONE authoritative resolution signal; this column is a
-- rebuildable copy (admin reindex / projection_outbox), never written by anything else.
-- Idempotent.

ALTER TABLE goal_search_index ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE goal_search_index ADD COLUMN IF NOT EXISTS t_created   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_goal_search_created ON goal_search_index (t_created DESC, goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_search_resolved ON goal_search_index (resolved_at) WHERE resolved_at IS NOT NULL;

-- Backfill rows whose canonical Goal is in this database (every Goal before sharding).
UPDATE goal_search_index gi
   SET resolved_at = g.resolved_at, t_created = g.t_created
  FROM goals g
 WHERE g.id = gi.goal_id
   AND (gi.t_created IS DISTINCT FROM g.t_created OR gi.resolved_at IS DISTINCT FROM g.resolved_at);

-- Rows homed on a remote shard are refreshed by the outbox drainer (it reads the
-- canonical row from the Goal's home shard).
INSERT INTO projection_outbox (object_type, object_id)
SELECT 'goal', goal_id FROM goal_search_index WHERE t_created IS NULL
ON CONFLICT (object_type, object_id) WHERE status = 'pending' DO NOTHING;
