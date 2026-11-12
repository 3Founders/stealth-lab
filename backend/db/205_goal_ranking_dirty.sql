-- 205_goal_ranking_dirty.sql
--
-- Next free number: 204 (goal submissions, which added `ai_approved`) was highest before this file.
--
-- What this adds: a small queue of Goals whose materialized tool ranking (db/200) is stale.
-- The ranking is a function of the card→Goal map, the procedure→card map and verified
-- outcomes, so it must be recomputed when any of those change:
--
--   * someone adds or remaps a card        -> tool_card_goals
--   * someone adds or changes a Way        -> procedures / procedure_tool_cards
--   * a designed test or a real run ends   -> task_runs / tool_observations
--
-- The triggers only mark the Goal; they never recompute. `app.tool_routing.ranking.drain()`
-- (the app's background loop, or `refresh_goal_rankings.py --drain`) refreshes the marked
-- Goals, coalescing a burst of writes into one refresh. A failed refresh leaves the Goal in
-- the queue for the next pass.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS goal_ranking_dirty (
    goal_id    uuid PRIMARY KEY,
    marked_at  timestamptz NOT NULL DEFAULT now(),
    reason     text
);

CREATE INDEX IF NOT EXISTS goal_ranking_dirty_at ON goal_ranking_dirty (marked_at);

ALTER TABLE goal_ranking_dirty ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON goal_ranking_dirty FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON goal_ranking_dirty FROM authenticated;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION mark_goal_ranking_dirty() RETURNS trigger AS $$
DECLARE
    gid      uuid;
    old_gid  uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF TG_TABLE_NAME = 'tool_card_goals' THEN
            gid := OLD.goal_id;
        ELSIF TG_TABLE_NAME = 'procedures' THEN
            gid := OLD.achieves_goal_id;
        ELSIF TG_TABLE_NAME = 'procedure_tool_cards' THEN
            SELECT achieves_goal_id INTO gid FROM procedures WHERE id = OLD.procedure_id;
        ELSIF TG_TABLE_NAME IN ('task_runs', 'tool_observations') THEN
            gid := OLD.goal_id;
        END IF;
    ELSE
        IF TG_TABLE_NAME = 'tool_card_goals' THEN
            gid := NEW.goal_id;
            old_gid := OLD.goal_id;
        ELSIF TG_TABLE_NAME = 'procedures' THEN
            gid := NEW.achieves_goal_id;
            old_gid := OLD.achieves_goal_id;
        ELSIF TG_TABLE_NAME = 'procedure_tool_cards' THEN
            SELECT achieves_goal_id INTO gid FROM procedures WHERE id = NEW.procedure_id;
            SELECT achieves_goal_id INTO old_gid FROM procedures WHERE id = OLD.procedure_id;
        ELSIF TG_TABLE_NAME IN ('task_runs', 'tool_observations') THEN
            gid := NEW.goal_id;
            old_gid := OLD.goal_id;
        END IF;
    END IF;

    IF gid IS NOT NULL THEN
        INSERT INTO goal_ranking_dirty (goal_id, marked_at, reason)
        VALUES (gid, now(), TG_TABLE_NAME)
        ON CONFLICT (goal_id) DO UPDATE SET marked_at = now(), reason = EXCLUDED.reason;
    END IF;
    IF old_gid IS NOT NULL AND old_gid IS DISTINCT FROM gid THEN
        INSERT INTO goal_ranking_dirty (goal_id, marked_at, reason)
        VALUES (old_gid, now(), TG_TABLE_NAME || ':old')
        ON CONFLICT (goal_id) DO UPDATE SET marked_at = now(), reason = EXCLUDED.reason;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_goal_dirty_card_goals ON tool_card_goals;
CREATE TRIGGER trg_goal_dirty_card_goals
    AFTER INSERT OR UPDATE OR DELETE ON tool_card_goals
    FOR EACH ROW EXECUTE FUNCTION mark_goal_ranking_dirty();

DROP TRIGGER IF EXISTS trg_goal_dirty_procedures ON procedures;
CREATE TRIGGER trg_goal_dirty_procedures
    AFTER INSERT OR UPDATE OR DELETE ON procedures
    FOR EACH ROW EXECUTE FUNCTION mark_goal_ranking_dirty();

DROP TRIGGER IF EXISTS trg_goal_dirty_procedure_cards ON procedure_tool_cards;
CREATE TRIGGER trg_goal_dirty_procedure_cards
    AFTER INSERT OR UPDATE OR DELETE ON procedure_tool_cards
    FOR EACH ROW EXECUTE FUNCTION mark_goal_ranking_dirty();

DROP TRIGGER IF EXISTS trg_goal_dirty_task_runs ON task_runs;
CREATE TRIGGER trg_goal_dirty_task_runs
    AFTER INSERT OR UPDATE OR DELETE ON task_runs
    FOR EACH ROW EXECUTE FUNCTION mark_goal_ranking_dirty();

DROP TRIGGER IF EXISTS trg_goal_dirty_observations ON tool_observations;
CREATE TRIGGER trg_goal_dirty_observations
    AFTER INSERT OR UPDATE OR DELETE ON tool_observations
    FOR EACH ROW EXECUTE FUNCTION mark_goal_ranking_dirty();
