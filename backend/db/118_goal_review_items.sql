-- Migration 118: review queue for the Goal hierarchy. Next free number: 119.
--
-- Placement stores low-confidence edges as `proposed` and flags Goals it could not
-- place (`orphan`) or could not decide about (`uncertain`). Proposed edges already
-- live in goal_relations; this table holds the flagged Goals so a reviewer can see
-- them (the audit job used to be a no-op). A reviewer decides edges only through
-- goal_abstraction.persist_goal_relation (cycle/redundancy/scope checks apply);
-- nothing here can create an accepted edge. Control database (A). Idempotent.

CREATE TABLE IF NOT EXISTS goal_review_items (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id          UUID NOT NULL,
    reason           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    scope_type       TEXT,
    scope_entity_id  TEXT,
    owner_id         TEXT,
    visibility       TEXT NOT NULL DEFAULT 'public',
    tenant_id        UUID,               -- org Goals: their organization (visibility_predicate's org clause)
    detail           JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    resolved_by      TEXT,
    CONSTRAINT goal_review_items_reason_chk CHECK (reason IN ('orphan', 'uncertain')),
    CONSTRAINT goal_review_items_status_chk CHECK (status IN ('open', 'resolved', 'dismissed')),
    CONSTRAINT goal_review_items_resolution_chk CHECK ((status = 'open') = (resolved_at IS NULL))
);
-- one OPEN item per Goal and reason: the audit job is idempotent
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_review_items_open
    ON goal_review_items (goal_id, reason) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_goal_review_items_status ON goal_review_items (status, created_at);

DROP TRIGGER IF EXISTS tg_ref_goal_review_items_goal_id ON goal_review_items;
CREATE TRIGGER tg_ref_goal_review_items_goal_id
    BEFORE INSERT OR UPDATE OF goal_id ON goal_review_items
    FOR EACH ROW EXECUTE FUNCTION sl_check_ref('goal_id', 'goal', '');

CREATE INDEX IF NOT EXISTS idx_goal_relations_proposed ON goal_relations (created_at) WHERE status = 'proposed';
