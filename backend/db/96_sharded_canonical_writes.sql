-- Migration 96: make canonical writes to REMOTE knowledge shards possible.
--
-- Problem (docs/sharding.md, "Known blocker" in the previous pass): control-database
-- tables held physical foreign keys into goals / procedures / knowledge_nodes. A row
-- homed on another database cannot satisfy those.
--
-- Solution, per the target architecture ("do not pretend cross-database foreign keys
-- exist; application-level referential validation where physical FKs cannot span shards"):
--   1. the cross-object FKs are REPLACED (not just dropped) by a route-aware validation
--      trigger: a reference is valid iff the object exists in THIS database, or the global
--      routing table says it is homed on another shard. Same-shard integrity is kept.
--   2. goal_names is the GLOBAL unique index for exact goal identity (a per-database unique
--      index cannot see other shards).
--   3. `admin verify-refs` (app/ingestion/admin.py) checks remote references end to end.
--
-- Idempotent.

-- --------------------------------------------------------------- route-aware validation
CREATE OR REPLACE FUNCTION sl_ref_exists(otype TEXT, oid UUID, by_row_id BOOLEAN DEFAULT FALSE) RETURNS BOOLEAN AS $$
DECLARE ok BOOLEAN;
BEGIN
    IF oid IS NULL THEN RETURN TRUE; END IF;
    IF otype = 'goal' THEN
        SELECT EXISTS (SELECT 1 FROM goals WHERE id = oid) INTO ok;
    ELSIF otype = 'procedure' THEN
        IF by_row_id THEN
            SELECT EXISTS (SELECT 1 FROM procedures WHERE id = oid) INTO ok;
        ELSE
            SELECT EXISTS (SELECT 1 FROM procedures WHERE procedure_id = oid) INTO ok;
        END IF;
    ELSE
        SELECT EXISTS (SELECT 1 FROM knowledge_nodes WHERE id = oid) INTO ok;
    END IF;
    IF ok THEN RETURN TRUE; END IF;
    -- not local: valid only if the global routing table homes it on another shard
    -- (procedure row ids are routed by their stable procedure_id, see object_routes_by_row)
    IF otype = 'procedure' AND by_row_id THEN
        RETURN EXISTS (SELECT 1 FROM procedure_row_routes r WHERE r.row_id = oid AND r.home_shard_id <> 'K000');
    END IF;
    RETURN EXISTS (SELECT 1 FROM object_routes r WHERE r.object_type = otype AND r.object_id = oid AND r.home_shard_id <> 'K000');
END;
$$ LANGUAGE plpgsql STABLE;

-- procedures are versioned: execution_plans reference a specific ROW id (one per version)
CREATE TABLE IF NOT EXISTS procedure_row_routes (
    row_id        UUID PRIMARY KEY,
    procedure_id  UUID NOT NULL,
    version       INTEGER NOT NULL,
    home_shard_id TEXT NOT NULL REFERENCES knowledge_shards(shard_id)
);
CREATE INDEX IF NOT EXISTS idx_procedure_row_routes_pid ON procedure_row_routes(procedure_id, version);

CREATE OR REPLACE FUNCTION sl_check_ref() RETURNS trigger AS $$
-- TG_ARGV: 0 = column, 1 = object type, 2 = 'row' when the column holds a procedures.id
DECLARE v UUID;
BEGIN
    v := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
    IF v IS NOT NULL AND NOT sl_ref_exists(TG_ARGV[1], v, COALESCE(TG_ARGV[2], '') = 'row') THEN
        RAISE EXCEPTION 'reference %.% = % has no %, neither local nor routed to a shard',
            TG_TABLE_NAME, TG_ARGV[0], v, TG_ARGV[1] USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- canonical tables on a SHARD database do not own the shard registry (it lives in the control
-- database), so their home_shard_id cannot be a local foreign key; it is validated at write time
-- by the placement code and by admin verify-refs.
ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_home_shard_id_fkey;
ALTER TABLE procedures DROP CONSTRAINT IF EXISTS procedures_home_shard_id_fkey;

-- ------------------------------------------------ replace the physical cross-object FKs
ALTER TABLE procedures DROP CONSTRAINT IF EXISTS procedures_achieves_goal_id_fkey;
ALTER TABLE procedures DROP CONSTRAINT IF EXISTS procedures_family_id_fkey;
ALTER TABLE execution_plans DROP CONSTRAINT IF EXISTS execution_plans_procedure_id_procedure_version_fkey;
ALTER TABLE execution_plans DROP CONSTRAINT IF EXISTS execution_plans_procedure_row_id_fkey;
ALTER TABLE claim_sources DROP CONSTRAINT IF EXISTS claim_sources_claim_id_fkey;
ALTER TABLE implementations DROP CONSTRAINT IF EXISTS implementations_goal_id_fkey;
ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_merged_into_id_fkey;
ALTER TABLE implementation_execution_telemetry DROP CONSTRAINT IF EXISTS implementation_execution_telemetry_goal_id_fkey;
ALTER TABLE goal_relations DROP CONSTRAINT IF EXISTS goal_relations_abstract_goal_id_fkey;
ALTER TABLE goal_relations DROP CONSTRAINT IF EXISTS goal_relations_specific_goal_id_fkey;

DO $$
DECLARE spec RECORD;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('procedures',                          'achieves_goal_id',  'goal',      ''),
        ('procedures',                          'family_id',         'procedure', 'row'),
        ('execution_plans',                     'procedure_row_id',  'procedure', 'row'),
        ('claim_sources',                       'claim_id',          'claim',     ''),
        ('implementations',                     'goal_id',           'goal',      ''),
        ('goals',                               'merged_into_id',    'goal',      ''),
        ('implementation_execution_telemetry',  'goal_id',           'goal',      ''),
        ('goal_relations',                      'abstract_goal_id',  'goal',      ''),
        ('goal_relations',                      'specific_goal_id',  'goal',      '')
    ) AS t(tbl, col, typ, mode) LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tg_ref_%s_%s ON %I', spec.tbl, spec.col, spec.tbl);
        EXECUTE format(
            'CREATE TRIGGER tg_ref_%s_%s BEFORE INSERT OR UPDATE OF %I ON %I FOR EACH ROW EXECUTE FUNCTION sl_check_ref(%L, %L, %L)',
            spec.tbl, spec.col, spec.col, spec.tbl, spec.col, spec.typ, spec.mode);
    END LOOP;
END $$;

-- referenced-side protection that ON DELETE used to give: canonical objects are tombstoned
-- (t_invalid / status='merged'), never hard-deleted while referenced. Hard DELETE of a goal
-- or procedure that something still references is refused.
CREATE OR REPLACE FUNCTION sl_refuse_delete_if_referenced() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'goals' THEN
        IF EXISTS (SELECT 1 FROM procedures WHERE achieves_goal_id = OLD.id) THEN
            RAISE EXCEPTION 'goal % is still referenced by procedures; tombstone it instead', OLD.id USING ERRCODE = 'foreign_key_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'procedures' THEN
        IF EXISTS (SELECT 1 FROM execution_plans WHERE procedure_row_id = OLD.id) THEN
            RAISE EXCEPTION 'procedure row % is referenced by an execution plan', OLD.id USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS tg_goals_refuse_delete ON goals;
CREATE TRIGGER tg_goals_refuse_delete BEFORE DELETE ON goals FOR EACH ROW EXECUTE FUNCTION sl_refuse_delete_if_referenced();
DROP TRIGGER IF EXISTS tg_procedures_refuse_delete ON procedures;
CREATE TRIGGER tg_procedures_refuse_delete BEFORE DELETE ON procedures FOR EACH ROW EXECUTE FUNCTION sl_refuse_delete_if_referenced();

-- -------------------------------------------------- global exact goal identity
-- One row per LIVE goal name per scope, across ALL shards. Maintained by trigger for
-- goals in this database and by app code (knowledge_store) for remote goals.
CREATE TABLE IF NOT EXISTS goal_names (
    scope_key       TEXT NOT NULL,          -- 'global' or '<scope_type>:<scope_entity_id>'
    normalized_name TEXT NOT NULL,
    goal_id         UUID NOT NULL,
    home_shard_id   TEXT NOT NULL REFERENCES knowledge_shards(shard_id),
    PRIMARY KEY (scope_key, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_goal_names_goal ON goal_names(goal_id);

CREATE OR REPLACE FUNCTION sl_goal_scope_key(st TEXT, se TEXT) RETURNS TEXT AS $$
    SELECT CASE WHEN st IS NULL OR st = 'global' THEN 'global' ELSE st || ':' || COALESCE(se, '') END;
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION sl_goal_names_sync() RETURNS trigger AS $$
BEGIN
    IF NEW.home_shard_id <> 'K000' THEN RETURN NEW; END IF;   -- remote goals: maintained by app code on the control DB
    IF NEW.status = 'merged' OR NEW.t_invalid IS NOT NULL THEN
        DELETE FROM goal_names WHERE goal_id = NEW.id;
    ELSE
        INSERT INTO goal_names (scope_key, normalized_name, goal_id, home_shard_id)
        VALUES (sl_goal_scope_key(NEW.scope_type, NEW.scope_entity_id), NEW.normalized_name, NEW.id, NEW.home_shard_id)
        ON CONFLICT (scope_key, normalized_name) DO UPDATE SET home_shard_id = EXCLUDED.home_shard_id
            WHERE goal_names.goal_id = EXCLUDED.goal_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS tg_goals_names_sync ON goals;
CREATE TRIGGER tg_goals_names_sync AFTER INSERT OR UPDATE OF normalized_name, status, t_invalid, scope_type, scope_entity_id ON goals
    FOR EACH ROW EXECUTE FUNCTION sl_goal_names_sync();

INSERT INTO goal_names (scope_key, normalized_name, goal_id, home_shard_id)
SELECT sl_goal_scope_key(scope_type, scope_entity_id), normalized_name, id, home_shard_id
FROM goals WHERE status <> 'merged' AND t_invalid IS NULL
ON CONFLICT DO NOTHING;

-- procedure row routes for existing rows and future local writes
INSERT INTO procedure_row_routes (row_id, procedure_id, version, home_shard_id)
SELECT id, procedure_id, version, home_shard_id FROM procedures ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION sl_procedure_row_route() RETURNS trigger AS $$
BEGIN
    IF NEW.home_shard_id <> 'K000' THEN RETURN NEW; END IF;   -- remote rows: routed by app code
    INSERT INTO procedure_row_routes (row_id, procedure_id, version, home_shard_id)
    VALUES (NEW.id, NEW.procedure_id, NEW.version, NEW.home_shard_id) ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS tg_procedures_row_route ON procedures;
CREATE TRIGGER tg_procedures_row_route AFTER INSERT ON procedures FOR EACH ROW EXECUTE FUNCTION sl_procedure_row_route();

-- ---------------------------------------------- object storage locators (raw payloads)
-- Large raw artifacts live in object storage; the database keeps only the locator + hash.
CREATE TABLE IF NOT EXISTS raw_objects (
    sha256        TEXT PRIMARY KEY,
    locator       TEXT NOT NULL,            -- e.g. s3://bucket/ab/cd/<sha256>  or  file:///var/stealth/blobs/ab/cd/<sha256>
    backend       TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL,
    content_type  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------ claim identity decisions reuse identity_decisions
-- (object_type 'claim' is already permitted; nothing to add.)
