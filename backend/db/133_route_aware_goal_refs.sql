-- Migration 133 (storage layout v2, docs/storage_layout_v2.md): the product/economy tables that stay on the control
-- database (solutions, evaluations, procedure_submissions, procedure_usage_events) reference Goals that are now homed
-- on knowledge shards. A physical foreign key to the local `goals` table would reject every Goal placed on a shard,
-- so -- exactly as migration 96 did for procedures, goal_relations and the rest -- each becomes the route-aware
-- check `sl_check_ref` (the Goal exists locally OR is routed to a shard in object_routes).
--
-- The referenced side keeps what the foreign keys gave: a hard DELETE of a Goal that a solution, evaluation or
-- submission still references on this database is refused (NO ACTION before), and usage events lose their goal_id
-- (ON DELETE SET NULL before). Goals are tombstoned in normal operation; this only guards hand-run deletes.
--
-- Idempotent.

ALTER TABLE solutions DROP CONSTRAINT IF EXISTS solutions_goal_id_fkey;
ALTER TABLE evaluations DROP CONSTRAINT IF EXISTS evaluations_goal_id_fkey;
ALTER TABLE procedure_submissions DROP CONSTRAINT IF EXISTS procedure_submissions_goal_id_fkey;
ALTER TABLE procedure_usage_events DROP CONSTRAINT IF EXISTS procedure_usage_events_goal_id_fkey;

DO $$
DECLARE spec RECORD;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('solutions',              'goal_id', 'goal', ''),
        ('evaluations',            'goal_id', 'goal', ''),
        ('procedure_submissions',  'goal_id', 'goal', ''),
        ('procedure_usage_events', 'goal_id', 'goal', '')
    ) AS t(tbl, col, typ, mode) LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tg_ref_%s_%s ON %I', spec.tbl, spec.col, spec.tbl);
        EXECUTE format(
            'CREATE TRIGGER tg_ref_%s_%s BEFORE INSERT OR UPDATE OF %I ON %I FOR EACH ROW EXECUTE FUNCTION sl_check_ref(%L, %L, %L)',
            spec.tbl, spec.col, spec.col, spec.tbl, spec.col, spec.typ, spec.mode);
    END LOOP;
END $$;

-- Which role this database plays. The provisioning script (scripts/provision_neon_shards.py) marks a knowledge
-- shard or search member; the control database has no row. sl_canonical_touch (migration 95) guarded remote Goals
-- and Procedures by their home_shard_id, but a claim row has no home column, so on a knowledge shard every claim
-- insert/update wrote a route and an outbox row into the shard's own, never-drained control tables. The app records
-- a remote claim's route and outbox on the control database (claim_placement / claim_identity), as for the others.
CREATE TABLE IF NOT EXISTS sl_database_role (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    role      TEXT NOT NULL CHECK (role IN ('knowledge_shard', 'search_member'))
);

CREATE OR REPLACE FUNCTION sl_canonical_touch() RETURNS trigger AS $$
DECLARE
    otype TEXT;
    oid   UUID;
    shard TEXT := 'K000';
BEGIN
    IF TG_TABLE_NAME = 'goals' THEN
        otype := 'goal'; oid := NEW.id; shard := NEW.home_shard_id;
    ELSIF TG_TABLE_NAME = 'procedures' THEN
        otype := 'procedure'; oid := NEW.procedure_id; shard := NEW.home_shard_id;
    ELSE
        otype := 'claim'; oid := NEW.id;
    END IF;
    -- Objects homed on a REMOTE shard are written by app code on that shard's database; the control
    -- plane (routes, outbox) for them is maintained by the app (knowledge_store / shards.record_route).
    -- Without this guard the same trigger firing on a shard database would write into that shard's
    -- (unused) control tables.
    IF shard <> 'K000' OR EXISTS (SELECT 1 FROM sl_database_role) THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        INSERT INTO object_routes (object_type, object_id, home_shard_id)
        VALUES (otype, oid, shard) ON CONFLICT (object_type, object_id) DO NOTHING;
    END IF;
    INSERT INTO projection_outbox (object_type, object_id) VALUES (otype, oid)
        ON CONFLICT (object_type, object_id) WHERE status = 'pending' DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sl_refuse_delete_if_referenced() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'goals' THEN
        IF EXISTS (SELECT 1 FROM procedures WHERE achieves_goal_id = OLD.id) THEN
            RAISE EXCEPTION 'goal % is still referenced by procedures; tombstone it instead', OLD.id USING ERRCODE = 'foreign_key_violation';
        END IF;
        -- migration 133: what the dropped NO ACTION foreign keys refused
        IF EXISTS (SELECT 1 FROM solutions WHERE goal_id = OLD.id)
           OR EXISTS (SELECT 1 FROM evaluations WHERE goal_id = OLD.id)
           OR EXISTS (SELECT 1 FROM procedure_submissions WHERE goal_id = OLD.id) THEN
            RAISE EXCEPTION 'goal % is still referenced by a solution, evaluation or submission; tombstone it instead', OLD.id
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        -- and what the dropped ON DELETE SET NULL did
        UPDATE procedure_usage_events SET goal_id = NULL WHERE goal_id = OLD.id;
    ELSIF TG_TABLE_NAME = 'procedures' THEN
        IF EXISTS (SELECT 1 FROM execution_plans WHERE procedure_row_id = OLD.id) THEN
            RAISE EXCEPTION 'procedure row % is referenced by an execution plan', OLD.id USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
