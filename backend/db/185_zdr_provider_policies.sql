-- 185_zdr_provider_policies.sql
--
-- Next free number: 184 (team_trial_codes) was highest before this file.
--
-- Two changes, both owner decisions of 2026-11-07. Additive and idempotent.
--
-- 1. The platform egress policy for kell's own provider connections (deepinfra, zai) permits USER_PRIVATE and ORG_PRIVATE, matching
--    f8ad0b3d. That commit allowed private code for paying accounts on the zero-retention connections in deploy/provider_connections.json
--    and in the account policy (app/services/accounts.py) but not in model_provider_policies, whose zai `pilot-v1` row
--    (scripts/setup_pilot.py) still allowed public classes only, so every desktop and CLI turn (sent as USER_PRIVATE) was refused with
--    "zai/* policy pilot-v1 does NOT permit USER_PRIVATE". The zero retention is the owner's declaration, not a recorded DPA
--    (docs/provider_catalog.md); the classes above ORG_PRIVATE (PERSONAL_DATA, CONFIDENTIAL_DATA, ...) stay out. Older rows are kept
--    for the record: the newest effective row wins (app/services/provider_policy.py).
--
-- 2. The models offered on kell's credits are now glm-5.3-flash, deepseek-v4.1-flash, mimo-v2.6-flash, deepseek-v4-pro and kimi-k3,
--    all through DeepInfra, with Z.ai as glm-5.3-flash's fallback; General Compute (gemma-4-31b-it) is no longer offered. Account
--    and pilot-team policies were written once at creation (ON CONFLICT DO NOTHING), so the rows still holding those defaults
--    (updated_by 'wallet_account' or 'pilot_purchase': never edited by an admin) are brought to the new lists here.

INSERT INTO model_provider_policies
    (provider, model, region, retention_policy, training_or_improvement_use, dpa_available, allowed_data_classes, policy_version, notes)
SELECT p.provider, '*', p.region, 'zero-retention (owner declaration)', FALSE, FALSE,
       ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE','USER_PRIVATE','ORG_PRIVATE'], 'zdr-v1', p.notes
  FROM (VALUES
        ('deepinfra', 'US', 'DeepInfra API on kell''s zero-retention connection (owner declaration, no DPA on record): private code allowed, personal and confidential data not.'),
        ('zai', 'unknown', 'Z.ai API on kell''s zero-retention connection (owner declaration, no DPA on record): private code allowed, personal and confidential data not.')
       ) AS p(provider, region, notes)
 WHERE NOT EXISTS (SELECT 1 FROM model_provider_policies m
                    WHERE m.provider = p.provider AND m.model = '*' AND m.policy_version = 'zdr-v1' AND m.tenant_id IS NULL);

UPDATE org_policies
   SET allowed_providers = ARRAY['deepinfra','zai'],
       allowed_models = ARRAY['glm-5.3-flash','deepseek-v4.1-flash','mimo-v2.6-flash','deepseek-v4-pro','kimi-k3']
 WHERE updated_by IN ('wallet_account', 'pilot_purchase')
   AND (allowed_providers IS DISTINCT FROM ARRAY['deepinfra','zai']
        OR allowed_models IS DISTINCT FROM ARRAY['glm-5.3-flash','deepseek-v4.1-flash','mimo-v2.6-flash','deepseek-v4-pro','kimi-k3']);
