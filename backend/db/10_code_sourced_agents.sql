-- Storage for code-sourced agent submissions (AGENT_STORE_PLAN.md, stage 5).
--
-- Generic across the two code-sourced origins: for external_marketplace,
-- {"repo_url": ..., "code": "..."}; for user_submitted (a structured
-- request, not raw code, per Section 4's deliberately narrow scope),
-- {"requested_input": ..., "requested_output": ..., "category": ...}.
--
-- Idempotent: safe to re-run.

ALTER TABLE agents ADD COLUMN IF NOT EXISTS source_detail JSONB;

-- One row per completed code-sourced review pass. Separate from
-- agent_reviews (Layer 1, graph-derived only) since the fields genuinely
-- differ: this has scanner findings, not fallacy flags or a groundedness
-- score, and conflating the two shapes would make both harder to read.
CREATE TABLE IF NOT EXISTS agent_code_reviews (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id              UUID NOT NULL REFERENCES agents(id),
    reviewer_opinions     JSONB NOT NULL DEFAULT '[]',
        -- [{family, sound, concerns, matches_stated_purpose}, ...] --
        -- one entry per independent reviewer
    scan_findings         JSONB NOT NULL DEFAULT '[]',
        -- raw bandit findings, only present when source_detail.code exists
    scan_high_severity_count INTEGER NOT NULL DEFAULT 0,
    passed                BOOLEAN NOT NULL DEFAULT FALSE,
    notes                 TEXT NOT NULL DEFAULT '',
    reviewed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_code_reviews_agent
    ON agent_code_reviews(agent_id, reviewed_at DESC);
