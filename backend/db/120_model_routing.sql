-- Migration 120: the per-Goal model recommender (docs/model_routing_plan.md). Next free number: 122
-- (121 is its project-B twin).
--
-- Additive only: no existing table changes. Control database (A):
--
--   routing_models       model registry: predecessor (a new version starts from its
--                        predecessor's ability), open-weights / local flags for
--                        hard constraints
--   routing_prices       live price table. Dollars are computed at decision time from
--                        learned TOKENS x these prices, so a price change applies
--                        instantly without re-learning
--   routing_params       versioned global posterior draws (the nightly joint fit)
--   routing_posteriors   per-Goal / per-Procedure posterior draws (local refits and the
--                        nightly fit), aligned to the global draws by index
--
-- routing_observations and routing_decisions are LOG tables: they live on project B
-- when SEARCH_DATABASE_URL is set (migration 121) and here otherwise. Creating them here
-- too keeps single-database deployments working with no extra step (the same pattern
-- migration 117 relies on). They are NOT pruned by prune-operational: the model is
-- fitted on the full observation history, and off-policy evaluation needs the
-- decision log.
-- Idempotent.

CREATE TABLE IF NOT EXISTS routing_models (
    model_key            TEXT PRIMARY KEY,           -- "provider/model@version", as reported
    predecessor_model_key TEXT REFERENCES routing_models(model_key),
    open_weights         BOOLEAN NOT NULL DEFAULT FALSE,
    runs_locally         BOOLEAN NOT NULL DEFAULT FALSE,
    notes                TEXT,
    t_created            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS routing_prices (
    model_key                  TEXT NOT NULL REFERENCES routing_models(model_key),
    input_usd_per_mtok         NUMERIC NOT NULL CHECK (input_usd_per_mtok >= 0),
    output_usd_per_mtok        NUMERIC NOT NULL CHECK (output_usd_per_mtok >= 0),
    cached_input_usd_per_mtok  NUMERIC CHECK (cached_input_usd_per_mtok IS NULL OR cached_input_usd_per_mtok >= 0),
    effective_from             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_key, effective_from)
);

CREATE TABLE IF NOT EXISTS routing_params (
    version      BIGSERIAL PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'failed')),
    fitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    method       TEXT NOT NULL,                      -- 'nuts' | 'flow_vi'
    draws        BYTEA NOT NULL,                     -- npz: global parameter draws
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb, -- index maps (models, scaffolds, checks, reporters), PCA basis, token model
    diagnostics  JSONB NOT NULL DEFAULT '{}'::jsonb  -- r-hat, ESS, divergences, SBC summary
);
CREATE UNIQUE INDEX IF NOT EXISTS routing_params_one_active ON routing_params ((status)) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS routing_posteriors (
    entity_kind          TEXT NOT NULL CHECK (entity_kind IN ('goal', 'procedure')),
    entity_id            UUID NOT NULL,
    params_version       BIGINT NOT NULL REFERENCES routing_params(version),
    draws                BYTEA NOT NULL,             -- npz: {"x": (S, D) draws, "global_index": (S,) }
    n_observations       INTEGER NOT NULL DEFAULT 0,
    last_observation_at  TIMESTAMPTZ,
    method               TEXT NOT NULL,              -- 'joint' | 'local_nuts'
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_kind, entity_id)
);

-- Log tables (see header). Identical definitions in 121_model_routing_search.sql.
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
