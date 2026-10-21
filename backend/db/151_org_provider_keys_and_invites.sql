-- 151_org_provider_keys_and_invites.sql
--
-- Next free number: 150 was highest before this file.
--
-- What this adds: the two tables behind "the company's admin connects the company's provider key once, and every
-- developer just joins".
--
--   1. org_provider_keys  one row per provider connection an organisation's admins entered in the dashboard. The API
--                         key is stored ENCRYPTED (AES-256-GCM, app/services/secret_box.py) and is never returned by any
--                         route; only a hint (the last four characters) is readable. The connection is served to the
--                         org's members through the governed gateway (/v1/chat/completions), so the organisation's
--                         policy, budget and ledger apply to every call made with it.
--   2. org_invites        a developer invitation: a random secret whose SHA-256 is stored (the secret is shown once, at
--                         creation), with an expiry, a use cap and a revoke stamp.
--
-- Same rule as 136_org_governance.sql: NO FALLBACK. Strict row-level security (an unset tenant setting sees no rows),
-- an invite that is expired, revoked or used up redeems nothing, and a key row without ciphertext cannot exist.
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS org_provider_keys (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       UUID NOT NULL REFERENCES organizations(id),
    connection_id         TEXT NOT NULL CHECK (connection_id ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    provider              TEXT NOT NULL CHECK (length(btrim(provider)) > 0),
    kind                  TEXT NOT NULL DEFAULT 'openai_compatible',
    base_url              TEXT NOT NULL CHECK (base_url ~ '^https://'),
    -- the units (models) this key serves, as providers/registry.py reads them:
    -- [{"model": "...", "provider_model": "...", "input_per_mtok": 0.1, "output_per_mtok": 0.3}, ...]
    units                 JSONB NOT NULL CHECK (jsonb_typeof(units) = 'array' AND jsonb_array_length(units) > 0),
    allowed_data_classes  TEXT[] NOT NULL CHECK (cardinality(allowed_data_classes) > 0),
    secret_ciphertext     TEXT NOT NULL CHECK (length(secret_ciphertext) > 0),
    key_version           SMALLINT NOT NULL DEFAULT 1,
    key_hint              TEXT NOT NULL,
    enabled               BOOLEAN NOT NULL DEFAULT TRUE,
    created_by            TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, connection_id)
);
CREATE INDEX IF NOT EXISTS idx_org_provider_keys_org ON org_provider_keys (organization_id) WHERE enabled;

CREATE TABLE IF NOT EXISTS org_invites (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    secret_hash      TEXT NOT NULL CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
    label            TEXT,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    max_uses         INTEGER NOT NULL CHECK (max_uses > 0),
    uses             INTEGER NOT NULL DEFAULT 0,
    revoked_at       TIMESTAMPTZ,
    revoked_by       TEXT,
    CHECK (uses >= 0 AND uses <= max_uses),
    CHECK ((revoked_at IS NULL) = (revoked_by IS NULL)),
    UNIQUE (secret_hash)
);
CREATE INDEX IF NOT EXISTS idx_org_invites_org ON org_invites (organization_id, created_at DESC);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['org_provider_keys', 'org_invites'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = t AND policyname = 'org_strict') THEN
            EXECUTE format(
                'CREATE POLICY org_strict ON %I FOR ALL USING (sl_org_strict(organization_id)) '
                'WITH CHECK (sl_org_strict(organization_id))', t);
        END IF;
    END LOOP;
END $$;
