-- Migration 113. Next free number: 115.

ALTER TABLE goal_relations
    ADD COLUMN IF NOT EXISTS tenant_id UUID,
    ADD COLUMN IF NOT EXISTS scope_type TEXT,
    ADD COLUMN IF NOT EXISTS scope_entity_id TEXT,
    ADD COLUMN IF NOT EXISTS decision_metadata JSONB,
    ADD COLUMN IF NOT EXISTS decided_by TEXT,
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'goal_relations'::regclass
          AND conname = 'goal_relations_tenant_id_fkey'
    ) THEN
        ALTER TABLE goal_relations
            ADD CONSTRAINT goal_relations_tenant_id_fkey
            FOREIGN KEY (tenant_id) REFERENCES organizations(id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'goal_relations'::regclass
          AND conname = 'goal_relations_decision_metadata_object_chk'
    ) THEN
        ALTER TABLE goal_relations
            ADD CONSTRAINT goal_relations_decision_metadata_object_chk
            CHECK (decision_metadata IS NULL OR jsonb_typeof(decision_metadata) = 'object');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'goal_relations'::regclass
          AND conname = 'goal_relations_decided_authority_chk'
    ) THEN
        ALTER TABLE goal_relations
            ADD CONSTRAINT goal_relations_decided_authority_chk
            CHECK (
                status NOT IN ('accepted', 'rejected')
                OR decision_id IS NOT NULL
                OR NULLIF(BTRIM(decided_by), '') IS NOT NULL
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'goal_relations'::regclass
          AND conname = 'goal_relations_decision_metadata_present_chk'
    ) THEN
        ALTER TABLE goal_relations
            ADD CONSTRAINT goal_relations_decision_metadata_present_chk
            CHECK (
                status NOT IN ('accepted', 'rejected')
                OR (decision_metadata IS NOT NULL AND decision_metadata <> '{}'::jsonb)
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'goal_relations'::regclass
          AND conname = 'goal_relations_accepted_tenant_scope_chk'
    ) THEN
        ALTER TABLE goal_relations
            ADD CONSTRAINT goal_relations_accepted_tenant_scope_chk
            CHECK (
                relation_type <> 'SPECIALIZES'
                OR status <> 'accepted'
                OR scope_type IS NOT NULL
            ) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_goal_relations_accepted_specific
    ON goal_relations(specific_goal_id, abstract_goal_id)
    WHERE relation_type = 'SPECIALIZES' AND status = 'accepted';
CREATE INDEX IF NOT EXISTS idx_goal_relations_accepted_abstract
    ON goal_relations(abstract_goal_id, specific_goal_id)
    WHERE relation_type = 'SPECIALIZES' AND status = 'accepted';
CREATE INDEX IF NOT EXISTS idx_goal_relations_accepted_scope
    ON goal_relations(scope_type, scope_entity_id, specific_goal_id)
    WHERE relation_type = 'SPECIALIZES' AND status = 'accepted' AND scope_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_goal_relations_accepted_tenant
    ON goal_relations(tenant_id, specific_goal_id)
    WHERE relation_type = 'SPECIALIZES' AND status = 'accepted' AND tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_goal_relations_accepted_tenant_edge
    ON goal_relations(tenant_id, specific_goal_id, abstract_goal_id)
    INCLUDE (decision_id, updated_at)
    WHERE relation_type = 'SPECIALIZES' AND status = 'accepted' AND tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_goal_relations_accepted_tenant_scope_edge
    ON goal_relations(tenant_id, scope_type, scope_entity_id, specific_goal_id, abstract_goal_id)
    WHERE relation_type = 'SPECIALIZES' AND status = 'accepted' AND tenant_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS goal_abstraction_state (
    goal_id                  UUID PRIMARY KEY,
    abstraction_level        INTEGER NOT NULL DEFAULT 0,
    parent_count             INTEGER NOT NULL DEFAULT 0,
    direct_child_count       INTEGER NOT NULL DEFAULT 0,
    coverage_total_count     BIGINT NOT NULL DEFAULT 0,
    coverage_resolved_count  BIGINT NOT NULL DEFAULT 0,
    coverage_ratio           REAL NOT NULL DEFAULT 0,
    direct_resolved_at       TIMESTAMPTZ,
    goal_status              TEXT NOT NULL DEFAULT 'active',
    goal_version             INTEGER NOT NULL DEFAULT 1,
    visibility               visibility_level NOT NULL DEFAULT 'public',
    owner_id                 TEXT,
    scope_type               TEXT NOT NULL DEFAULT 'global',
    scope_entity_id          TEXT,
    tenant_id                UUID REFERENCES organizations(id),
    home_shard_id            TEXT NOT NULL REFERENCES knowledge_shards(shard_id),
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT goal_abstraction_state_level_chk CHECK (abstraction_level >= 0),
    CONSTRAINT goal_abstraction_state_parent_count_chk CHECK (parent_count >= 0),
    CONSTRAINT goal_abstraction_state_child_count_chk CHECK (direct_child_count >= 0),
    CONSTRAINT goal_abstraction_state_coverage_total_chk CHECK (coverage_total_count >= 0),
    CONSTRAINT goal_abstraction_state_coverage_resolved_chk CHECK (
        coverage_resolved_count >= 0 AND coverage_resolved_count <= coverage_total_count
    ),
    CONSTRAINT goal_abstraction_state_coverage_ratio_chk CHECK (
        coverage_ratio >= 0 AND coverage_ratio <= 1
    ),
    CONSTRAINT goal_abstraction_state_goal_status_chk CHECK (
        goal_status IN ('candidate', 'active')
    ),
    CONSTRAINT goal_abstraction_state_goal_version_chk CHECK (goal_version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_goal_abstraction_scope
    ON goal_abstraction_state(scope_type, scope_entity_id, abstraction_level);
CREATE INDEX IF NOT EXISTS idx_goal_abstraction_access
    ON goal_abstraction_state(visibility, tenant_id, scope_type, scope_entity_id);

DO $$
BEGIN
    IF EXISTS (
        WITH RECURSIVE reach(source_goal_id, goal_id) AS (
            SELECT specific_goal_id, abstract_goal_id
            FROM goal_relations
            WHERE relation_type = 'SPECIALIZES' AND status = 'accepted'
            UNION
            SELECT reach.source_goal_id, r.abstract_goal_id
            FROM reach
            JOIN goal_relations r ON r.specific_goal_id = reach.goal_id
            WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
        )
        SELECT 1 FROM reach WHERE source_goal_id = goal_id
    ) THEN
        RAISE EXCEPTION 'goal_relations contains an accepted SPECIALIZES cycle'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION sl_goal_relation_cycle_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.relation_type = 'SPECIALIZES' AND NEW.status = 'accepted' THEN
        PERFORM pg_advisory_xact_lock(hashtext('goal-relations-accepted-dag'));
        IF NEW.specific_goal_id = NEW.abstract_goal_id THEN
            RAISE EXCEPTION 'Goal relation % -> % is a self edge', NEW.specific_goal_id, NEW.abstract_goal_id
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF EXISTS (
                WITH RECURSIVE ancestors(goal_id) AS (
                    SELECT NEW.abstract_goal_id
                    UNION
                    SELECT r.abstract_goal_id
                    FROM goal_relations r
                    JOIN ancestors a ON r.specific_goal_id = a.goal_id
                    WHERE r.relation_type = 'SPECIALIZES'
                      AND r.status = 'accepted'
                      AND NOT (
                          r.specific_goal_id = OLD.specific_goal_id
                          AND r.abstract_goal_id = OLD.abstract_goal_id
                          AND r.relation_type = OLD.relation_type
                      )
                )
                SELECT 1 FROM ancestors WHERE goal_id = NEW.specific_goal_id
            ) THEN
                RAISE EXCEPTION 'Goal relation % -> % would create an accepted cycle', NEW.specific_goal_id, NEW.abstract_goal_id
                    USING ERRCODE = '23514';
            END IF;
        ELSIF EXISTS (
            WITH RECURSIVE ancestors(goal_id) AS (
                SELECT NEW.abstract_goal_id
                UNION
                SELECT r.abstract_goal_id
                FROM goal_relations r
                JOIN ancestors a ON r.specific_goal_id = a.goal_id
                WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
            )
            SELECT 1 FROM ancestors WHERE goal_id = NEW.specific_goal_id
        ) THEN
            RAISE EXCEPTION 'Goal relation % -> % would create an accepted cycle', NEW.specific_goal_id, NEW.abstract_goal_id
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_goal_relation_cycle_guard ON goal_relations;
CREATE TRIGGER tg_goal_relation_cycle_guard
    BEFORE INSERT OR UPDATE OF specific_goal_id, abstract_goal_id, relation_type, status
    ON goal_relations
    FOR EACH ROW
    EXECUTE FUNCTION sl_goal_relation_cycle_guard();
