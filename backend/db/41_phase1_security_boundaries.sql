-- ---------------------------------------------------------------------------
-- 1. 'org' visibility value (ORG_PRIVATE). Existing rows untouched:
--    'public' rows remain the GLOBAL shared commons (Phase 0 decision 3 —
--    no re-owning of existing data), 'private' rows keep their meaning.
--    New ORG_PRIVATE data writes visibility='org' + tenant_id=<org uuid>,
--    enforced by services/access.py's org-membership predicate extension.
--
--    NOTE: ALTER TYPE ... ADD VALUE cannot run inside a transaction block
--    on Postgres < 12 transaction semantics; this migration is therefore
--    run statement-by-statement by the existing migration runner, which
--    matches how db/03 created the enum originally.
-- ---------------------------------------------------------------------------
ALTER TYPE visibility_level ADD VALUE IF NOT EXISTS 'org';

-- ---------------------------------------------------------------------------
-- 2. audit_events: append-only, security-sensitive transition record.
-- Schema matches services/audit.py's writer exactly (the ONE writer; this
-- migration is its table). Append-only by construction: no app code path
-- UPDATEs or DELETEs here. RLS deliberately not applied — the table is
-- cross-tenant by nature; reads go through authorized service code
-- (db/29's documented posture).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_events (
    id             BIGSERIAL PRIMARY KEY,
    t_created      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_subject  TEXT NOT NULL,
    actor_user_id  UUID,
    action         TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    tenant_id      UUID,
    details        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_events_object
    ON audit_events (object_type, object_id, t_created);
CREATE INDEX IF NOT EXISTS idx_audit_events_actor
    ON audit_events (actor_subject, t_created);
CREATE INDEX IF NOT EXISTS idx_audit_events_action
    ON audit_events (action, t_created);

-- ---------------------------------------------------------------------------
-- 3. registered_workspaces: hosted repository identity boundary (P0).
-- A hosted execution resolves a workspace's server-side storage_path from
-- THIS registry by workspace id + tenant authorization — a caller-controlled
-- filesystem path is NEVER the authorization mechanism. Local/loopback
-- callers keep using repo_path directly (default posture, unchanged);
-- hosted mode (settings.hosted_execution_enabled) requires the path to
-- name a registered workspace root.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS registered_workspaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    -- Server-side absolute, normalized root. Set at REGISTRATION by an
    -- authorized actor; never accepted from an execution caller.
    storage_path    TEXT NOT NULL,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    created_by      TEXT NOT NULL,
    t_created       TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired       TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_tenant_name
    ON registered_workspaces(tenant_id, name) WHERE t_expired IS NULL;
CREATE INDEX IF NOT EXISTS idx_workspace_storage_path
    ON registered_workspaces(storage_path) WHERE t_expired IS NULL;