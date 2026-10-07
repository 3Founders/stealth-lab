-- Migration 147: model-side priors for the recommender (docs/plan_2026-10_priors_library_survey.md §2).
-- Next free number: 149 (148 is its project-B twin; 140-146 are taken on other branches). Additive only. Idempotent.
--
--   routing_model_cards        what is public about a model before anyone runs it on our Goals: release /
--                              cutoff dates, size, open weights, reasoning, list prices, family. The joint
--                              fit regresses ability and skills on these (app/routing/cards.py), so a model
--                              with no predecessor starts from its card instead of a pure guess.
--   routing_evidence_items     public benchmark items that are NOT our Goals (e.g. SWE-bench Verified):
--                              they enter the global fit as evidence Goals under a benchmark node, with the
--                              structural features of their reference patch, and never get a stored posterior.
--   routing_aggregate_results  leaderboards that publish only a percentage: k of n resolved, per
--                              (benchmark node, model, scaffold) -- a binomial likelihood in the fit.
--
--   routing_model_updates      between-nightly posterior of each fitted model's ability drift, from the public
--                              observations since the active fit, aligned to its draws (app/routing/model_update.py).
--
-- routing_observations gains item_created_at (contamination: an item published before a model's training
-- cutoff) and dedupe_key (one source per (item, model, scaffold, attempt): imports never double count).

CREATE TABLE IF NOT EXISTS routing_model_cards (
    model_key        TEXT PRIMARY KEY,
    family           TEXT,
    provider         TEXT,
    release_date     DATE,
    training_cutoff  DATE,
    open_weights     BOOLEAN,
    params_b         REAL CHECK (params_b IS NULL OR params_b > 0),
    active_params_b  REAL CHECK (active_params_b IS NULL OR active_params_b > 0),
    reasoning        REAL CHECK (reasoning IS NULL OR (reasoning >= 0 AND reasoning <= 1)),
    context_k        REAL CHECK (context_k IS NULL OR context_k > 0),
    price_in         NUMERIC CHECK (price_in IS NULL OR price_in >= 0),     -- USD per million tokens (list price)
    price_out        NUMERIC CHECK (price_out IS NULL OR price_out >= 0),
    aliases          TEXT[] NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL DEFAULT '',
    as_of            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_routing_model_cards_aliases ON routing_model_cards USING gin (aliases);

CREATE TABLE IF NOT EXISTS routing_evidence_items (
    item_key     TEXT PRIMARY KEY,              -- "<benchmark>:<instance id>", e.g. "swe-bench-verified:django__django-11099"
    goal_id      UUID NOT NULL,                 -- our Goal when the item is one, else uuid5(item_key)
    is_goal      BOOLEAN NOT NULL DEFAULT FALSE,
    benchmark    TEXT NOT NULL,
    repo         TEXT,
    created_at   DATE,                          -- when the item became public (contamination)
    features     JSONB NOT NULL DEFAULT '{}'::jsonb   -- app/routing/goal_features.py
);
CREATE INDEX IF NOT EXISTS idx_routing_evidence_goal ON routing_evidence_items (goal_id);
CREATE INDEX IF NOT EXISTS idx_routing_evidence_benchmark ON routing_evidence_items (benchmark, repo);

CREATE TABLE IF NOT EXISTS routing_aggregate_results (
    id                 BIGSERIAL PRIMARY KEY,
    source             TEXT NOT NULL,           -- where the number was published
    benchmark          TEXT NOT NULL,
    model_key          TEXT NOT NULL,
    scaffold           TEXT NOT NULL,
    n                  INTEGER NOT NULL CHECK (n > 0),
    k                  INTEGER NOT NULL CHECK (k >= 0 AND k <= n),
    item_created_min   DATE,
    item_created_max   DATE,
    occurred_at        TIMESTAMPTZ NOT NULL,
    dedupe_key         TEXT NOT NULL UNIQUE     -- benchmark|model|scaffold|split
);

CREATE TABLE IF NOT EXISTS routing_model_updates (
    params_version  BIGINT NOT NULL REFERENCES routing_params(version) ON DELETE CASCADE,
    model_key       TEXT NOT NULL,
    drift           BYTEA NOT NULL,             -- npz {"drift": (S,)} aligned with the params' draws
    n_observations  INTEGER NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (params_version, model_key)
);

ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS item_created_at DATE;
ALTER TABLE routing_observations ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_obs_dedupe ON routing_observations (dedupe_key) WHERE dedupe_key IS NOT NULL;
