-- 185_zdr_provider_policies.sql
--
-- Next free number: 184 (team_trial_codes) was highest before this file.
--
-- What this changes: the platform egress policy for kell's own provider connections (zai, general_compute) now permits USER_PRIVATE and
-- ORG_PRIVATE, matching the owner's decision in f8ad0b3d. That commit allowed private code for paying accounts on these zero-retention
-- connections in two of the three places that decide it (deploy/provider_connections.json and the account policy in
-- app/services/accounts.py) but not in model_provider_policies, whose `pilot-v1` rows (scripts/setup_pilot.py) still allowed public
-- classes only. So every desktop and CLI turn (sent as USER_PRIVATE) was refused with "zai/* policy pilot-v1 does NOT permit
-- USER_PRIVATE" (owner, 2026-11-07).
--
-- The zero retention is the owner's declaration, not a recorded DPA (docs/provider_catalog.md); the classes above ORG_PRIVATE
-- (PERSONAL_DATA, CONFIDENTIAL_DATA, ...) stay out. The pilot-v1 rows are kept for the record: the newest effective row wins
-- (app/services/provider_policy.py). Additive and idempotent.

INSERT INTO model_provider_policies
    (provider, model, region, retention_policy, training_or_improvement_use, dpa_available, allowed_data_classes, policy_version, notes)
SELECT p.provider, '*', 'unknown', 'zero-retention (owner declaration)', FALSE, FALSE,
       ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE','USER_PRIVATE','ORG_PRIVATE'], 'zdr-v1', p.notes
  FROM (VALUES
        ('zai', 'Z.ai API on kell''s zero-retention connection (owner declaration, no DPA on record): private code allowed, personal and confidential data not.'),
        ('general_compute', 'General Compute API on kell''s zero-retention connection (owner declaration, no DPA on record): private code allowed, personal and confidential data not.')
       ) AS p(provider, notes)
 WHERE NOT EXISTS (SELECT 1 FROM model_provider_policies m
                    WHERE m.provider = p.provider AND m.model = '*' AND m.policy_version = 'zdr-v1' AND m.tenant_id IS NULL);
