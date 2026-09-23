-- Migration 109: local project sync — device credentials, key/ciphertext
-- metadata columns, RLS backstop.
--
-- Implements the data model from docs/local_project_sync_security.md's
-- "Implementation Closure" (§1 sync device credentials, §5 server-access
-- review). Read that document before changing anything here.
--
-- sync_device_credentials: mirrors service_credentials' shape (migration
-- 99) but for a THIRD, distinct trust domain -- a local sync device acting
-- on behalf of one signed-in keळ account, never a worker/service and never
-- the browser's own Supabase session token. `owner_subject` is the account
-- this credential may sync FOR (same value space as
-- synced_projects.owner_subject); `project_id` scopes it further to one
-- project. `scope` is a real column (not a hardcoded assumption) even
-- though only 'sync:upload' exists today.
--
-- synced_projects gains the encryption-related columns the security ADR's
-- key hierarchy requires: wrapped_p_dek (ciphertext of the P-DEK, wrapped
-- client-side under the recovery-KEK via SubtleCrypto.wrapKey), the
-- Argon2id salt/params used to derive that KEK (not secret), and a
-- monotonic revision counter for idempotent incremental sync uploads.
-- snapshot_sha256/snapshot_locator/snapshot_size_bytes (migration 107) are
-- UNCHANGED in shape -- they now point at CIPHERTEXT rather than plaintext
-- JSON, a payload-semantics change enforced in application code
-- (app/stealth/project_sync.py), not by this schema.
--
-- RLS backstop, same idiom as migration 29's sl_tenant_scope_allows --
-- keyed on owner_subject (per-account) rather than tenant_id (per-org),
-- since this is personal, not org-tenant, isolation. Documented in the
-- ADR's closure §5 as defense-in-depth ONLY: this backend's own connection
-- role has BYPASSRLS (verified directly against the real database: `SELECT
-- rolbypassrls FROM pg_roles WHERE rolname = current_user` returned true
-- for the `postgres` role this backend connects as), so this policy is NOT
-- load-bearing against the backend's own queries today -- per Supabase's
-- own documentation, a role with bypassrls "operates outside RLS
-- protections" regardless of FORCE. It only becomes load-bearing if a
-- second, lower-privileged connection path is ever added. Written now
-- anyway because it is cheap, correct, and consistent with the existing
-- pattern; app-layer owner_subject scoping (app/api/me.py,
-- app/stealth/project_sync.py -- `WHERE owner_subject = $1`, sourced only
-- from a verified token, never a client-supplied field) remains the actual
-- enforcement today, unchanged by this migration.
--
-- Idempotent: CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
-- throughout, safe to re-run.

CREATE TABLE IF NOT EXISTS sync_device_credentials (
    credential_id   TEXT PRIMARY KEY,                          -- the token's jti
    owner_subject   TEXT NOT NULL,
    project_id      UUID NOT NULL REFERENCES synced_projects(project_id),
    fingerprint     TEXT NOT NULL,                              -- sha256(token); the token itself is never stored
    scope           TEXT NOT NULL DEFAULT 'sync:upload',
    expires_at      TIMESTAMPTZ NOT NULL,
    t_created       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    revoked_reason  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_device_credentials_owner
    ON sync_device_credentials (owner_subject) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_sync_device_credentials_project
    ON sync_device_credentials (project_id) WHERE revoked_at IS NULL;

ALTER TABLE synced_projects
    ADD COLUMN IF NOT EXISTS wrapped_p_dek  TEXT,      -- ciphertext of the P-DEK, wrapped client-side; never the raw key
    ADD COLUMN IF NOT EXISTS recovery_salt  TEXT,      -- Argon2id salt (not secret)
    ADD COLUMN IF NOT EXISTS kdf_params     JSONB,     -- {m, t, p} used, so parameters can be strengthened later without breaking old wraps
    ADD COLUMN IF NOT EXISTS revision       BIGINT NOT NULL DEFAULT 0;  -- monotonic; the idempotency key for incremental sync uploads (see ADR §16 -- NOT a content hash, ciphertext is non-deterministic by design)

-- ---- RLS backstop (defense-in-depth; see docstring above) ----------------

CREATE OR REPLACE FUNCTION sl_owner_scope_allows(p_row_owner TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT p_row_owner = COALESCE(NULLIF(current_setting('app.owner_subject', true), ''), p_row_owner);
$$;

ALTER TABLE synced_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE synced_projects FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'synced_projects' AND policyname = 'owner_isolation'
    ) THEN
        CREATE POLICY owner_isolation ON synced_projects
            FOR ALL
            USING (sl_owner_scope_allows(owner_subject))
            WITH CHECK (sl_owner_scope_allows(owner_subject));
    END IF;
END $$;

ALTER TABLE sync_device_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_device_credentials FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'sync_device_credentials' AND policyname = 'owner_isolation'
    ) THEN
        CREATE POLICY owner_isolation ON sync_device_credentials
            FOR ALL
            USING (sl_owner_scope_allows(owner_subject))
            WITH CHECK (sl_owner_scope_allows(owner_subject));
    END IF;
END $$;
