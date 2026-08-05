-- Agent Store: catalog, review state machine, review results.
--
-- Mirrors the ontology/debate schema's own discipline deliberately:
-- bi-temporal on the catalog table, an explicit transition table for
-- review state (not scattered status updates), an immutable event log
-- for every transition. See AGENT_STORE_PLAN.md for the full reasoning.
--
-- Idempotent: safe to re-run.

DO $$ BEGIN
    CREATE TYPE agent_source AS ENUM
        ('internal', 'graph_derived', 'user_submitted', 'external_marketplace');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE agent_execution_mode AS ENUM ('local_skill', 'remote_http');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE agent_review_state AS ENUM
        ('ingested', 'under_review', 'pending_human_approval', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS agents (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL,
    embedding               VECTOR(1024),

    source                  agent_source NOT NULL,
    -- Set only for source='graph_derived' -- traceable back to the
    -- decomposition this was promoted from. Not a foreign key to a
    -- specific table on purpose: decompositions currently live in the
    -- `decompositions` table (V2 public path), but a graph-derived
    -- agent could plausibly be promoted from other sources later, and
    -- this column shouldn't need a migration when that happens.
    source_decomposition_id UUID,

    execution_mode          agent_execution_mode NOT NULL,
    skill_ref               TEXT,    -- for local_skill: resolves via SkillRegistry
    remote_config           JSONB,   -- for remote_http: {"url": ..., "auth": ...}

    input_schema            JSONB NOT NULL DEFAULT '{}',
    output_schema           JSONB NOT NULL DEFAULT '{}',

    review_state            agent_review_state NOT NULL DEFAULT 'ingested',
    -- Deliberately separate from review_state = 'approved'. A
    -- code-sourced agent can be discoverable and even approved for
    -- *listing* before it is cleared to actually *execute* -- see
    -- AGENT_STORE_PLAN.md Section 3b. Never set true by a migration or
    -- default; only an explicit review action may flip this.
    runnable                 BOOLEAN NOT NULL DEFAULT FALSE,

    visibility               visibility_level NOT NULL DEFAULT 'public',
    owner_id                 TEXT,

    t_valid                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid                TIMESTAMPTZ,
    t_created                TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by               TEXT,

    CONSTRAINT agent_execution_config_present CHECK (
        (execution_mode = 'local_skill' AND skill_ref IS NOT NULL)
        OR (execution_mode = 'remote_http' AND remote_config IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_agents_public ON agents(id)
    WHERE visibility = 'public' AND t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_agents_embedding ON agents
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_agents_fts ON agents
    USING gin (to_tsvector('english', name || ' ' || description));
CREATE INDEX IF NOT EXISTS idx_agents_review_state ON agents(review_state)
    WHERE t_invalid IS NULL;

-- One row per completed Layer 1 evaluation of an agent under review.
-- Mirrors `scorecards`' relationship to a debate candidate: the agent
-- row is the entity, this is a review artifact about a specific pass at
-- reviewing it. A re-review (e.g. after the agent is updated) gets its
-- own row rather than overwriting the last one.
CREATE TABLE IF NOT EXISTS agent_reviews (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            UUID NOT NULL REFERENCES agents(id),
    fallacy_flags       JSONB NOT NULL DEFAULT '[]',
    constructive        BOOLEAN NOT NULL DEFAULT TRUE,
    groundedness_score  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unresolved_cites    JSONB NOT NULL DEFAULT '[]',
    structural_problems JSONB NOT NULL DEFAULT '[]',
    passed              BOOLEAN NOT NULL DEFAULT FALSE,
    notes               TEXT NOT NULL DEFAULT '',
    reviewed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_reviews_agent ON agent_reviews(agent_id, reviewed_at DESC);

-- Immutable audit log of every review-state transition. Mirrors
-- debate_events exactly, same discipline: no scattered status updates,
-- one place every transition is recorded.
CREATE TABLE IF NOT EXISTS agent_review_events (
    id           BIGSERIAL PRIMARY KEY,
    agent_id     UUID NOT NULL REFERENCES agents(id),
    from_state   agent_review_state,
    to_state     agent_review_state NOT NULL,
    reason       TEXT,
    actor        TEXT,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_review_events_agent
    ON agent_review_events(agent_id, occurred_at);
