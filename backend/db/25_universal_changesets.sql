-- Band 1.9c: universal ChangeSet persistence (spec §19/§20, Appendix C row #7).
--
-- Until now `models/change.py` represented debate *proposals* only; nothing
-- recorded mutations of [V] objects after the fact. Invariant #7 ("every change
-- to a versioned object creates a version/change record") was therefore
-- unverifiable. This migration gives ChangeSets a first-class [H] home.
--
-- Scope of v1 (honest): records lifecycle/status mutations that exist today
-- (procedure approval/rejection/quarantine-disable). Supersession and revision
-- paths for knowledge_nodes / observations arrive with their §20 revision
-- machinery and wire into record_change_set() as they are built.
--
-- Append-only per invariant #19: triggers reject DELETE outright and any UPDATE
-- (ChangeSets are post-hoc records of already-applied mutations; review-gated
-- ChangeSets arrive with the approval-flow work and will widen the permitted
-- UPDATE shape explicitly, the way db/24 does for evidence tombstones).

CREATE TABLE IF NOT EXISTS change_sets (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author             TEXT NOT NULL,
    reason             TEXT,
    scope_type         TEXT,
    scope_entity_id    TEXT,
    review_status      TEXT NOT NULL DEFAULT 'applied'
                       CHECK (review_status IN ('pending', 'approved', 'rejected', 'applied')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS change_set_operations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_set_id   UUID NOT NULL REFERENCES change_sets(id) ON DELETE CASCADE,
    operation       TEXT NOT NULL CHECK (operation IN (
                        'invalidate', 'revise', 'create_version',
                        'create', 'relate', 'status_change')),
    target_table    TEXT NOT NULL CHECK (target_table IN (
                        -- [V] objects that exist today; extended as tables land.
                        -- task_graphs deliberately absent: nodes inherit plan scope
                        -- and the table is [D→frozen], not [V] (see db/23 header).
                        'knowledge_nodes', 'task_nodes', 'procedures',
                        'observations', 'states', 'evidence')),
    target_id       TEXT,
    detail          JSONB NOT NULL DEFAULT '{}',
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cso_target
    ON change_set_operations(target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_cs_created ON change_sets(created_at);

-- ============================================================
-- Append-only enforcement (invariant #19), db/24 house pattern.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_changesets_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '%.% is append-only (Band 1.9c, invariant #19): DELETE rejected',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    RAISE EXCEPTION '%.% is append-only (Band 1.9c, invariant #19): UPDATE rejected',
        TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_change_sets_append_only') THEN
        CREATE TRIGGER tg_change_sets_append_only
            BEFORE UPDATE OR DELETE ON change_sets
            FOR EACH ROW EXECUTE FUNCTION sl_changesets_append_only();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_cso_append_only') THEN
        CREATE TRIGGER tg_cso_append_only
            BEFORE UPDATE OR DELETE ON change_set_operations
            FOR EACH ROW EXECUTE FUNCTION sl_changesets_append_only();
    END IF;
END $$;
