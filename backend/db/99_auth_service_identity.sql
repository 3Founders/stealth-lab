-- Migration 99: auth hardening — service identities, credentials, platform roles, job authority.
--
-- Supabase Auth remains the human identity provider; nothing here stores passwords or sessions.
-- Adds the pieces Postgres must own so authorization does not depend on Supabase-specific schemas:
--   service_identities  registered workers/maintenance services and the scopes they MAY hold
--   service_credentials issued short-lived credentials (jti + SHA-256 fingerprint only; never the token)
--   platform_role_grants revocable platform roles (reviewer / platform_admin) — distinct from org roles
--   ingestion_jobs.*    immutable authorization metadata stamped by the API at submit time
-- Idempotent.

CREATE TABLE IF NOT EXISTS service_identities (
    service_id      TEXT PRIMARY KEY,
    roles           TEXT[] NOT NULL DEFAULT '{}',
    allowed_scopes  TEXT[] NOT NULL DEFAULT '{}',
    environment     TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    t_created       TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS service_credentials (
    credential_id   TEXT PRIMARY KEY,                       -- the token's jti
    service_id      TEXT NOT NULL REFERENCES service_identities(service_id),
    fingerprint     TEXT NOT NULL,                          -- sha256(token); the token itself is never stored
    expires_at      TIMESTAMPTZ NOT NULL,
    created_by      TEXT NOT NULL,
    t_created       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    revoked_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_service_credentials_service ON service_credentials (service_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_role_grants (
    user_id     UUID NOT NULL REFERENCES users(id),
    role        TEXT NOT NULL CHECK (role IN ('reviewer', 'platform_admin')),
    granted_by  TEXT NOT NULL,
    t_created   TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired   TIMESTAMPTZ,
    PRIMARY KEY (user_id, role)
);

-- Job authority: written by the API from the verified AuthContext, read (never written) by workers.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS submitted_by_user_id    TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS submitted_by_service_id TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS auth_tenant_id          UUID;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS auth_scope              TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS auth_visibility         TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS source_access_scope     TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS publication_allowed     BOOLEAN NOT NULL DEFAULT FALSE;

-- Break-glass: short-lived, reasoned, two-person cross-tenant elevation (grants tenancy:cross until expires_at).
CREATE TABLE IF NOT EXISTS break_glass_grants (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id),
    granted_by  TEXT NOT NULL,
    reason      TEXT NOT NULL CHECK (length(reason) >= 20),
    t_created   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_break_glass_active ON break_glass_grants (user_id) WHERE revoked_at IS NULL;
