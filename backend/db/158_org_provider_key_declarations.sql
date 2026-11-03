-- 158_org_provider_key_declarations.sql
--
-- Next free number: 157 was highest before this file.
--
-- What this adds: the data-handling DECLARATIONS an organisation admin makes about a provider connection
-- (zdr, no_training, region, dpa_signed -- app/providers/types.py Connection.compliance). The dashboard form and
-- PUT /v1/orgs/{org}/provider-keys/{id} already accepted them and validated them, but nothing stored them: the INSERT
-- and UPDATE in services/org_keys.py never wrote them and OrgKeyStore built every connection with declarations={}, so
-- /v1/models always reported them as unknown. One JSONB object holds them.
--
-- A key that is ABSENT from the object means "not declared" (unknown); a key present with false means the admin
-- declared "no". Unknown is never read as yes. They are declarations by the admin, not proof.
--
-- Fresh start: no backfill. Existing rows get '{}', which is exactly "nothing declared". Idempotent.

ALTER TABLE org_provider_keys
    ADD COLUMN IF NOT EXISTS declarations JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(declarations) = 'object');
