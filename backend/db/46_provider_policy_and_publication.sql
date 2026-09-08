-- Next free migration number: 47.
--
-- Launch compliance Phases 4, 5, 6, 7. Additive + idempotent. No backfill
-- (fresh-start rule). No existing row changes ownership or visibility.
--
-- 1. model_provider_policies  (Phase 5 / LC-005)
--    The registry ProviderPolicyService.can_send() consults before any
--    external model/embedding call. API-key existence is NOT authorization;
--    a call is permitted only if an EFFECTIVE policy row for the
--    (provider, model) pair lists the data's classification in
--    allowed_data_classes.
--
-- 2. publication_records  (Phase 4 / data-flow spec §24)
--    The durable contribution record for every private/org -> Global
--    Commons publication. This is the audit/provenance proof of HOW an
--    object entered the Commons; it is NOT the procedure itself.
--
-- 3. data_requests  (Phase 6 / LC-007)
--    Export and deletion request lifecycle (DPDP/GDPR rights workflow).
--
-- audit_events + registered_workspaces already exist (migration 41).

-- ---------------------------------------------------------------------------
-- 1. model_provider_policies
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_provider_policies (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider                TEXT NOT NULL,
    model                   TEXT NOT NULL DEFAULT '*',      -- '*' = every model on this provider
    endpoint                TEXT,
    region                  TEXT,
    data_residency          TEXT,
    retention_policy        TEXT,
    training_or_improvement_use  BOOLEAN NOT NULL DEFAULT FALSE,
    subprocessors           JSONB NOT NULL DEFAULT '[]'::jsonb,
    dpa_available            BOOLEAN NOT NULL DEFAULT FALSE,
    transfer_mechanism      TEXT,
    deletion_semantics      TEXT,
    -- the whitelist. A classification NOT in this array is DENIED egress
    -- to this provider/model.
    allowed_data_classes    TEXT[] NOT NULL DEFAULT '{}',
    policy_version          TEXT NOT NULL,
    effective_from          TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until         TIMESTAMPTZ,
    tenant_id               UUID,                           -- NULL = global default policy
    notes                   TEXT,
    t_created               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_provider_policies_lookup
    ON model_provider_policies (provider, model, effective_from DESC);
CREATE INDEX IF NOT EXISTS idx_provider_policies_tenant
    ON model_provider_policies (tenant_id) WHERE tenant_id IS NOT NULL;

-- Seed the providers this deployment actually uses. Conservative:
-- external providers may carry PUBLIC_* and GLOBAL_PROCEDURE data only;
-- USER_PRIVATE / ORG_PRIVATE / EXECUTION_SECRET / PERSONAL_DATA /
-- CONFIDENTIAL_DATA / SECURITY_DATA / AUDIT_DATA never leave the trust
-- boundary by default. 'local' (self-hosted, OpenAI-compatible) is the
-- only provider allowed private classes. Operators widen a row explicitly
-- once a DPA is in place.
INSERT INTO model_provider_policies
    (provider, model, region, retention_policy, training_or_improvement_use,
     dpa_available, allowed_data_classes, policy_version, notes)
VALUES
    ('gemini',  '*', 'US/global', 'provider default', FALSE, FALSE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'Google Generative Language API. No DPA on record; public data only.'),
    ('voyage',  '*', 'US',        'provider default', FALSE, FALSE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'Voyage AI embeddings. Public data only until a DPA is recorded.'),
    ('anthropic','*','US',        'zero-retention (API)', FALSE, TRUE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'Anthropic API. DPA available; still public-only until tenant opts in.'),
    ('openai',  '*', 'US',        'provider default', FALSE, TRUE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'OpenAI API.'),
    ('google',  '*', 'US/global', 'provider default', FALSE, FALSE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'Google (judge). Public data only.'),
    ('fireworks','*','US',        'provider default', FALSE, FALSE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'Fireworks AI.'),
    ('openrouter','*','US/global','provider default', FALSE, FALSE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE'], 'seed-v1',
     'OpenRouter transport - upstream model varies; public data only.'),
    ('local',   '*', 'self-hosted','operator-controlled', FALSE, TRUE,
     ARRAY['PUBLIC_SOURCE','PUBLIC_DERIVED','GLOBAL_PROCEDURE',
           'ORG_PRIVATE','USER_PRIVATE','PERSONAL_DATA','CONFIDENTIAL_DATA'],
     'seed-v1', 'Self-hosted OpenAI-compatible endpoint inside the trust boundary.')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. publication_records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS publication_records (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_object_type      TEXT NOT NULL,           -- 'procedure' | 'claim'
    source_object_id        TEXT NOT NULL,
    published_object_type    TEXT,
    published_object_id     TEXT,                    -- the fresh Global Candidate
    actor_subject           TEXT NOT NULL,
    actor_user_id           UUID,
    organization_id         UUID,
    destination_scope       TEXT NOT NULL,           -- 'global_candidate' | 'organization'
    authorization_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    sanitization_version    TEXT,
    sanitization_record     JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance_version      TEXT,
    dependency_report       JSONB NOT NULL DEFAULT '{}'::jsonb,
    classification_report   JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_license          TEXT,
    review_state            TEXT NOT NULL DEFAULT 'candidate',   -- candidate|approved|rejected
    withdrawal_state        TEXT,                    -- NULL|withdrawn|withdrawn_from_retrieval|requires_remediation|retained_as_independently_sourced
    t_created               TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_updated               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_publication_records_source
    ON publication_records (source_object_type, source_object_id);
CREATE INDEX IF NOT EXISTS idx_publication_records_published
    ON publication_records (published_object_type, published_object_id);
CREATE INDEX IF NOT EXISTS idx_publication_records_actor
    ON publication_records (actor_subject, t_created);

-- ---------------------------------------------------------------------------
-- 3. data_requests
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_type        TEXT NOT NULL,              -- 'export' | 'deletion'
    subject             TEXT NOT NULL,              -- the authenticated token subject the request is about
    actor_user_id       UUID,
    status              TEXT NOT NULL DEFAULT 'requested',  -- requested|processing|completed|failed|rejected
    scope               JSONB NOT NULL DEFAULT '{}'::jsonb, -- what was asked for / what was covered
    result_summary      JSONB NOT NULL DEFAULT '{}'::jsonb,
    legal_hold          BOOLEAN NOT NULL DEFAULT FALSE,
    t_requested         TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_completed         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_data_requests_subject
    ON data_requests (subject, t_requested DESC);
