-- target: search
-- Migration 121: project-B copies of the model-recommender log tables (see 120_model_routing.sql).
-- Runs only with `scripts/migrate.py --target search`. Next free number: 122. Idempotent.

CREATE TABLE IF NOT EXISTS routing_observations (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source             TEXT NOT NULL CHECK (source IN ('live', 'sweep', 'public_import')),
    goal_id            UUID NOT NULL,
    procedure_id       UUID,
    model_key          TEXT NOT NULL,
    scaffold           TEXT NOT NULL,
    instance_key       TEXT NOT NULL,              -- attempts on the same concrete task share it
    attempt_index      INTEGER NOT NULL DEFAULT 0,
    check_kind         TEXT NOT NULL CHECK (check_kind IN ('benchmark', 'tests', 'procedure_check', 'judge', 'self_report')),
    accepted           BOOLEAN NOT NULL,
    gold_correct       BOOLEAN,                    -- audit label against the frozen benchmark / a reviewer
    pass_fraction      REAL CHECK (pass_fraction IS NULL OR (pass_fraction >= 0 AND pass_fraction <= 1)),
    tokens_in          INTEGER CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out         INTEGER CHECK (tokens_out IS NULL OR tokens_out >= 0),
    tokens_cached      INTEGER CHECK (tokens_cached IS NULL OR tokens_cached >= 0),
    cost_usd           NUMERIC,
    latency_ms         INTEGER,
    reporter           TEXT,                       -- NULL = observed by Stealth itself (sandbox / sweep / import)
    recommendation_id  UUID,
    visibility         TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private', 'org')),
    owner_id           TEXT,
    occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_routing_obs_goal ON routing_observations (goal_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_routing_obs_time ON routing_observations (occurred_at);

CREATE TABLE IF NOT EXISTS routing_decisions (
    id              UUID PRIMARY KEY,
    goal_id         UUID NOT NULL,
    procedure_id    UUID,
    instance_key    TEXT NOT NULL,
    params_version  BIGINT,
    candidates      JSONB NOT NULL,
    ladder          JSONB NOT NULL,
    propensity      DOUBLE PRECISION NOT NULL,
    meets_target    BOOLEAN NOT NULL,
    predicted       JSONB NOT NULL,
    constraints     JSONB NOT NULL DEFAULT '{}'::jsonb,
    visibility      TEXT NOT NULL DEFAULT 'public',
    owner_id        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_routing_decisions_goal ON routing_decisions (goal_id, created_at);
