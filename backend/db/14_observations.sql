-- Ticket 04 (memory-substrate map): a dedicated `observations` table,
-- diverging from claims deliberately -- observations are immutable
-- (re-derived under a new extractor version rather than superseded), so
-- knowledge_nodes' bi-temporal machinery would go unused, and
-- observations are the highest-volume object in the system (ticket 10
-- made claims high-volume too by turning state into claim rows, so
-- piling both onto one table compounds risk rather than reusing
-- infrastructure).
CREATE TABLE IF NOT EXISTS observations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_type      TEXT NOT NULL,   -- e.g. 'file_touched', 'test_run',
                                            -- 'command_executed', 'commit_made',
                                            -- 'semantic_label' -- free TEXT, not a
                                            -- CHECK enum, same reasoning ticket 06
                                            -- already established for trace_events'
                                            -- event_type (the vocabulary isn't fixed)
    label                 TEXT NOT NULL,   -- the actual observation content,
                                            -- human-readable
    -- Extraction version stamped as real COMPONENTS, not one opaque hash
    -- (ticket 04's own answer): "a single hash is more compact but
    -- destroys the ability to ask 'which observations came from model X'".
    extractor_kind        TEXT NOT NULL CHECK (extractor_kind IN ('deterministic', 'model')),
    extractor_name        TEXT NOT NULL,
    code_version          TEXT NOT NULL,
    model_id              TEXT,            -- NULL for deterministic extractions
    prompt_hash           TEXT,            -- NULL for deterministic
    decoding_params_hash  TEXT,            -- NULL for deterministic
    properties            JSONB NOT NULL DEFAULT '{}',
    -- Deliberately NO confidence field -- ticket 04's own words: "the
    -- sharpest call in the ticket... raw token/sequence probabilities
    -- are overconfident and uncalibrated; verbalized self-confidence is
    -- uncorrelated with accuracy... storing an uncalibrated float
    -- invites every downstream consumer to treat it as signal."
    visibility            visibility_level NOT NULL DEFAULT 'public',
    owner_id              TEXT,
    created_by            TEXT,            -- provenance, same convention
                                            -- claims.py/failure_capture.py use
    extracted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_observations_visibility ON observations(visibility)
    WHERE visibility = 'private';

-- Ticket 04's chosen design over both alternatives it considered: a
-- dedicated join table, not an array of event ids on the observation row
-- (which is cheaper but has nowhere to carry per-link metadata and needs
-- a GIN index for the reverse direction), and not the polymorphic `edges`
-- table (CHECK-constrained to the graph's node tables; extending its
-- polymorphism at the highest write volume in the system trades a
-- purpose-built join for a general one that then has to carry the graph
-- *and* this).
CREATE TABLE IF NOT EXISTS observation_events (
    observation_id  UUID NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    event_id        UUID NOT NULL REFERENCES trace_events(id),
    PRIMARY KEY (observation_id, event_id)
);

-- Reverse traversal ("which observations cite this event") is
-- load-bearing for the provenance invariant ("why is this in memory?")
-- per ticket 04's own answer -- both directions indexed. The primary key
-- above already covers the forward direction (observation -> its events).
CREATE INDEX IF NOT EXISTS idx_observation_events_event ON observation_events(event_id);
