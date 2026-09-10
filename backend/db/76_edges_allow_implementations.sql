-- Migration 76 (B37 STRICT CLOSURE): allow 'implementations' as a real
-- edges.source_table/target_table value.
--
-- WHY: hierarchy.py's tree (B37's literal "coarse domain/topic
-- routing... index entries reference canonical IDs") now extends to
-- `implementations` (migration 33) -- a real, dedicated hierarchy_group
-- row (identified by `requirements->>'_hierarchy_group'`, status=
-- 'disabled' so it can never surface as a real resolution candidate)
-- PARENT_OF-owns its real member implementations via the SAME `edges`
-- table every other hierarchy (`task_nodes`/`knowledge_nodes`/
-- `procedures`, migration 18) already uses -- never a second edge
-- table. `edges_source_table_check`/`edges_target_table_check`
-- (migration 18) did not yet include 'implementations' in their closed
-- vocabulary; this migration adds it, additive and idempotent, exactly
-- migration 18's own drop-and-recreate pattern.
--
-- Next free number: 76 (75 was highest before this file).

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'edges'::regclass
      AND pg_get_constraintdef(oid) LIKE '%source_table%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE edges DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE edges ADD CONSTRAINT edges_source_table_check
        CHECK (source_table IN ('knowledge_nodes', 'task_nodes', 'procedures', 'implementations'));
END $$;

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'edges'::regclass
      AND pg_get_constraintdef(oid) LIKE '%target_table%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE edges DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE edges ADD CONSTRAINT edges_target_table_check
        CHECK (target_table IN ('knowledge_nodes', 'task_nodes', 'procedures', 'implementations'));
END $$;
