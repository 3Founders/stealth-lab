-- Migration 131: a Goal's exact-match name key is computed from canonical_name, never stored, and no longer lossy.
--
-- Before: goals.normalized_name (and goal_names.normalized_name) held lower(name) with every character outside
-- [a-z0-9] replaced by a space. That merged different Goals at the exact-match tier, where no judge runs:
-- "Add C++ support" = "Add C# support" = "add c support"; "a+b" = "a-b"; ".NET" = "NET"; and every name in a
-- non-Latin script became the empty key, so all of them collided.
--
-- After:
--   * normalize_goal_name(text) removes only what never changes meaning: Unicode compatibility form (NFKC),
--     letter case, quote marks and backticks, repeated whitespace, and sentence punctuation at the very end.
--     Its Python twin is app/services/goals.py::normalize_goal_name (used only for in-memory keys; every
--     database comparison computes the key in SQL on both sides).
--   * goals has no normalized_name column: uniqueness is enforced on normalize_goal_name(canonical_name).
--   * goal_names (the cross-shard exact-identity registry) stores canonical_name, unique on
--     (scope_key, normalize_goal_name(canonical_name)).
--
-- The new key only ever separates names the old one merged (apart from NFKC-equivalent spellings), so existing
-- rows cannot collide; the migration checks anyway and stops rather than dropping anything.

CREATE OR REPLACE FUNCTION normalize_goal_name(input TEXT) RETURNS TEXT AS $$
    SELECT regexp_replace(
        trim(regexp_replace(
            regexp_replace(lower(normalize(coalesce(input, ''), NFKC)), U&'["''`\2018\2019\201C\201D]', '', 'g'),
            '\s+', ' ', 'g')),
        '[\s.!?,;:]+$', '', 'g');
$$ LANGUAGE sql IMMUTABLE;

-- ---------------------------------------------------------------- goals

DO $$
DECLARE clash TEXT;
BEGIN
    SELECT string_agg(k, '; ') INTO clash FROM (
        SELECT normalize_goal_name(canonical_name) || ' @' || COALESCE(scope_type, 'global') AS k
        FROM goals WHERE t_invalid IS NULL AND status <> 'merged'
        GROUP BY normalize_goal_name(canonical_name), COALESCE(scope_type, 'global'), scope_entity_id
        HAVING count(*) > 1 LIMIT 20) x;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'migration 131: live Goals share a name key; merge them first: %', clash;
    END IF;
END $$;

DROP INDEX IF EXISTS idx_goals_global_normalized_name;
DROP INDEX IF EXISTS idx_goals_local_normalized_name;

CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_global_name_key
    ON goals (normalize_goal_name(canonical_name))
    WHERE t_invalid IS NULL AND status <> 'merged' AND (scope_type IS NULL OR scope_type = 'global');

CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_local_name_key
    ON goals (normalize_goal_name(canonical_name), scope_type, scope_entity_id)
    WHERE t_invalid IS NULL AND status <> 'merged' AND scope_type IS NOT NULL AND scope_type <> 'global';

-- ---------------------------------------------------------------- goal_names (control database registry)

DO $$
BEGIN
    IF to_regclass('goal_names') IS NULL THEN
        RETURN;   -- a knowledge shard: the registry lives on the control database only
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'goal_names' AND column_name = 'normalized_name') THEN
        ALTER TABLE goal_names ADD COLUMN IF NOT EXISTS canonical_name TEXT;
        UPDATE goal_names n SET canonical_name = g.canonical_name FROM goals g WHERE g.id = n.goal_id;
        IF EXISTS (SELECT 1 FROM goal_names WHERE canonical_name IS NULL) THEN
            RAISE EXCEPTION 'migration 131: % goal_names rows point at Goals not on this database; backfill '
                            'canonical_name from their home shards first',
                            (SELECT count(*) FROM goal_names WHERE canonical_name IS NULL);
        END IF;
        ALTER TABLE goal_names DROP CONSTRAINT IF EXISTS goal_names_pkey;
        ALTER TABLE goal_names DROP COLUMN normalized_name;
        ALTER TABLE goal_names ALTER COLUMN canonical_name SET NOT NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_names_key
    ON goal_names (scope_key, normalize_goal_name(canonical_name));

-- The registry follows the Goal's own name (a rename or scope change replaces the Goal's row, no stale key left).
CREATE OR REPLACE FUNCTION sl_goal_names_sync() RETURNS trigger AS $$
BEGIN
    IF NEW.home_shard_id <> 'K000' THEN RETURN NEW; END IF;   -- remote goals: maintained by app code on the control DB
    DELETE FROM goal_names WHERE goal_id = NEW.id;
    IF NEW.status <> 'merged' AND NEW.t_invalid IS NULL THEN
        INSERT INTO goal_names (scope_key, canonical_name, goal_id, home_shard_id)
        VALUES (sl_goal_scope_key(NEW.scope_type, NEW.scope_entity_id), NEW.canonical_name, NEW.id, NEW.home_shard_id)
        ON CONFLICT (scope_key, normalize_goal_name(canonical_name)) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_goals_names_sync ON goals;
DO $$
BEGIN
    IF to_regclass('goal_names') IS NOT NULL THEN
        CREATE TRIGGER tg_goals_names_sync
            AFTER INSERT OR UPDATE OF canonical_name, status, t_invalid, scope_type, scope_entity_id ON goals
            FOR EACH ROW EXECUTE FUNCTION sl_goal_names_sync();
    END IF;
END $$;

-- ---------------------------------------------------------------- drop the stored key

ALTER TABLE goals DROP COLUMN IF EXISTS normalized_name;
