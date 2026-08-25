-- HARDENING H1: identity substrate — organizations / users / roles /
-- memberships born additively.
--
-- Why these tables, why now: V0 put `tenant_id` on the core tables and
-- then no query ever filtered by it — isolation looked implemented and
-- was decorative (03_access.sql's own confession). The predicate-builder
-- half of H1 lives in services/access.py; this migration births what the
-- builder resolves AGAINST. A tenancy predicate is only as real as the
-- organizations it names.
--
-- Design decisions:
--   * Identity master data is [V]-class (versioned-mutable), NOT [H]:
--     renames, deactivations and role grants are ordinary corrections,
--     not history to freeze. Soft-delete via t_expired follows the repo
--     convention; nothing here is append-only truth.
--   * A user's identity is (issuer, external_subject), not subject
--     alone — an OIDC `sub` is only unique PER ISSUER, and two IdPs
--     colliding on a sub must never merge into one user.
--   * Roles are catalog ROWS, deliberately not an enum: adding a role
--     must be an INSERT by whoever governs identity, never a schema
--     migration. The four builtins ship seeded with FIXED uuids so the
--     seed is deterministic across environments.
--   * Memberships allow several roles per (user, organization): the PK
--     is the triple. Revocation expires the row (t_expired), it does
--     not delete it — grants stay auditable.
--   * The seeded "commons" organization REUSES the V0 default tenant
--     uuid ('00000000-0000-0000-0000-000000000001') as its id. Every
--     row ever written carries that tenant_id today; seeding this exact
--     id turns the decorative column's placeholder into a real
--     organization without touching a single existing row — additive
--     birth, fresh-start compliant, zero backfill.
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS organizations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    t_created  TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired  TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_org_slug ON organizations(slug);

CREATE TABLE IF NOT EXISTS users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer            TEXT NOT NULL,
    external_subject  TEXT NOT NULL,
    display_name      TEXT,
    email             TEXT,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    t_created         TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired         TIMESTAMPTZ
);

-- (issuer, subject) is THE identity key — see decision above.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_issuer_subject
    ON users(issuer, external_subject);

CREATE TABLE IF NOT EXISTS roles (
    id           UUID PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_name ON roles(name);

CREATE TABLE IF NOT EXISTS org_memberships (
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    user_id          UUID NOT NULL REFERENCES users(id),
    role_id          UUID NOT NULL REFERENCES roles(id),
    granted_by       UUID REFERENCES users(id),
    t_created        TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired        TIMESTAMPTZ,
    PRIMARY KEY (organization_id, user_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_membership_user
    ON org_memberships(user_id) WHERE t_expired IS NULL;
CREATE INDEX IF NOT EXISTS idx_membership_org
    ON org_memberships(organization_id) WHERE t_expired IS NULL;

-- ============================================================
-- Seeds. Deterministic ids; ON CONFLICT keeps re-runs quiet.
-- ============================================================

-- The commons organization: id == V0's default_tenant_id, so the
-- tenant_id column every core table already carries resolves to a real
-- organization from the moment this migration runs.
INSERT INTO organizations (id, name, slug)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Commons',
    'commons'
)
ON CONFLICT (id) DO NOTHING;

-- Builtin role catalog. Fixed uuids (stable across environments);
-- governance of WHO holds which role is membership data, not schema.
INSERT INTO roles (id, name, description)
VALUES
    ('10000000-0000-0000-0000-000000000001', 'owner',  'Full control of the organization, including membership and deletion.'),
    ('10000000-0000-0000-0000-000000000002', 'admin',  'Manages content and members; cannot delete the organization.'),
    ('10000000-0000-0000-0000-000000000003', 'member', 'Creates and edits content within the organization.'),
    ('10000000-0000-0000-0000-000000000004', 'viewer', 'Read-only access to the organization''s content.')
ON CONFLICT (name) DO NOTHING;
