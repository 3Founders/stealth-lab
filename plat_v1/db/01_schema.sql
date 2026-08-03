-- plat_v1 schema. Idempotent: safe to re-run.
--
-- Named and shaped for the eventual merge with backend_v2 (see the
-- "Convergence" section of implement.md). The decisions that look like
-- overkill for a single-operator v1 -- bi-temporal columns nothing sets,
-- polymorphic edges that only ever point task->task, a provenance enum with
-- values v1 never writes -- are all things that cost nothing to add now and
-- become a data migration later.

-- Wrapped in an explicit transaction, and that is load-bearing rather than
-- tidy. psql defaults to autocommit on and ON_ERROR_STOP *off*, so a bare
-- `RAISE EXCEPTION` in the guard below would print an error and psql would
-- cheerfully carry on with the rest of the file. BEGIN/COMMIT means the
-- failed guard aborts the transaction, every following statement fails with
-- it, and the closing COMMIT degrades to a rollback. Nothing is applied.
BEGIN;

-- Refuse to run into a shared schema.
--
-- Every object below is unqualified, so it lands wherever the caller's
-- search_path points. `scripts/seed.py` points it at plat_v1's own schema.
-- Pasted into psql or the Supabase SQL editor the default is `public` --
-- where backend_v2 keeps a `task_nodes` with different columns. Without this
-- guard, `CREATE TABLE IF NOT EXISTS task_nodes` would silently skip onto
-- that table and the CREATE INDEX statements further down would then build
-- a unique index, an HNSW index and an FTS index on another application's
-- live data. All three would succeed.
DO $guard$ BEGIN
    IF current_schema() IS NULL OR current_schema() IN ('public', 'pg_catalog') THEN
        RAISE EXCEPTION
            'refusing to create plat_v1 objects in %. Run `python scripts/seed.py`, '
            'or SET search_path TO <your plat_v1 schema> first.',
            COALESCE(current_schema(), '(no schema)');
    END IF;
END $guard$;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- The same four values backend_v2 ends up with (three in its 01_ontology.sql
-- plus `public_generated`, added by its 05_decomposition.sql).
--
-- Note what actually happens when both schemas share a database: CREATE TYPE
-- targets `current_schema()`, so plat_v1 gets its *own* copy and the
-- duplicate_object guard never fires against backend_v2's. That is the safe
-- outcome -- our tables reference our enum -- but it is not "the shared enum
-- is used", which is what this comment used to claim.
DO $$ BEGIN
    CREATE TYPE provenance_source AS ENUM (
        'company_ingested', 'company_debate', 'prior_library', 'public_generated'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Deliberately NOT called `edge_type`: backend_v2 already owns a type by that
-- name with a different value set, and colliding on it would make the two
-- schemas mutually exclusive in one database for no gain.
DO $$ BEGIN
    CREATE TYPE task_edge_type AS ENUM ('REQUIRES', 'PRODUCES', 'DECOMPOSES_TO');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ---------------------------------------------------------------------------
-- Task nodes
-- ---------------------------------------------------------------------------
-- No `superseded_by` column. Supersession is expressed by setting `t_invalid`
-- on the old row, which is how backend_v2 already does it -- carrying both
-- mechanisms would mean two sources of truth for "is this row live".
CREATE TABLE IF NOT EXISTS task_nodes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    description       TEXT,
    kind              TEXT NOT NULL DEFAULT 'leaf' CHECK (kind IN ('leaf', 'composite')),
    input_schema      JSONB NOT NULL DEFAULT '{}',
    output_schema     JSONB NOT NULL DEFAULT '{}',
    success_criteria  JSONB NOT NULL DEFAULT '{}',
    -- Which input properties form this task's cache fingerprint. NULL means
    -- all of them, which is right for a stage whose input *is* the document.
    --
    -- It is wrong for a stage downstream of extraction. `map_to_schema`
    -- receives typed_grid, columns and target_schema; fingerprinting all
    -- three hashes the actual cell values, so two invoices from the same
    -- vendor with different amounts get different fingerprints -- for
    -- precisely the one stage that costs a model call. Naming
    -- ["columns","target_schema"] makes the key the table's *shape*, which
    -- is what the cached mapping is actually a function of.
    cache_key         JSONB,
    embedding         VECTOR(1024),
    version           INTEGER NOT NULL DEFAULT 1,
    provenance        provenance_source NOT NULL DEFAULT 'company_ingested',
    -- Bi-temporal columns. v1 only ever sets t_valid and t_created; the other
    -- two exist so adding real bi-temporality later is not a migration.
    t_valid           TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid         TIMESTAMPTZ,
    t_created         TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired         TIMESTAMPTZ,
    created_by        TEXT
);

-- One live task per name. Makes seeding idempotent via ON CONFLICT and stops
-- two versions of the same task both being retrievable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_nodes_live_name
    ON task_nodes(name) WHERE t_invalid IS NULL;


-- ---------------------------------------------------------------------------
-- Edges
-- ---------------------------------------------------------------------------
-- Polymorphic source/target, matching backend_v2's `edges`. v1 only ever
-- writes 'task_nodes' on both sides; the CHECK admits 'knowledge_nodes' so the
-- constraint doesn't have to be rewritten when it does.
CREATE TABLE IF NOT EXISTS task_edges (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type     task_edge_type NOT NULL,
    source_id     UUID NOT NULL,
    source_table  TEXT NOT NULL DEFAULT 'task_nodes'
                  CHECK (source_table IN ('task_nodes', 'knowledge_nodes')),
    target_id     UUID NOT NULL,
    target_table  TEXT NOT NULL DEFAULT 'task_nodes'
                  CHECK (target_table IN ('task_nodes', 'knowledge_nodes')),
    properties    JSONB NOT NULL DEFAULT '{}',
    provenance    provenance_source NOT NULL DEFAULT 'company_ingested',
    t_valid       TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid     TIMESTAMPTZ,
    t_created     TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired     TIMESTAMPTZ
);


-- ---------------------------------------------------------------------------
-- Implementations: what actually satisfies a task node
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS implementations (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_node_id         UUID NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    kind                 TEXT NOT NULL CHECK (kind IN ('command', 'python', 'model')),
    spec                 JSONB NOT NULL DEFAULT '{}',
    cost_estimate        NUMERIC NOT NULL DEFAULT 0,
    latency_estimate_ms  INTEGER NOT NULL DEFAULT 0,
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    provenance           provenance_source NOT NULL DEFAULT 'company_ingested',
    t_valid              TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid            TIMESTAMPTZ,
    t_created            TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired            TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_impl_live_name
    ON implementations(task_node_id, name) WHERE t_invalid IS NULL;


-- ---------------------------------------------------------------------------
-- Evals
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evals (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_node_id  UUID NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    -- [{"input": {...}, "expected": {...}}, ...]
    cases         JSONB NOT NULL DEFAULT '[]',
    scorer        TEXT NOT NULL DEFAULT 'exact_match',
    t_valid       TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid     TIMESTAMPTZ,
    t_created     TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired     TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_evals_live_name
    ON evals(task_node_id, name) WHERE t_invalid IS NULL;

CREATE TABLE IF NOT EXISTS eval_results (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    implementation_id  UUID NOT NULL REFERENCES implementations(id) ON DELETE CASCADE,
    eval_id            UUID NOT NULL REFERENCES evals(id) ON DELETE CASCADE,
    score              NUMERIC NOT NULL,
    cost               NUMERIC NOT NULL DEFAULT 0,
    latency_ms         INTEGER NOT NULL DEFAULT 0,
    detail             JSONB NOT NULL DEFAULT '{}',
    ran_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------------------------
-- Runs, proposals, traces, cache
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_text  TEXT NOT NULL,
    inputs        JSONB NOT NULL DEFAULT '{}',
    plan          JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'awaiting_approval', 'running',
                                    'succeeded', 'failed')),
    outputs       JSONB NOT NULL DEFAULT '{}',
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS proposals (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_text  TEXT NOT NULL,
    inputs        JSONB NOT NULL DEFAULT '{}',
    plan          JSONB NOT NULL DEFAULT '{}',
    -- {"ok": bool, "problems": [{"rule": ..., "message": ..., "refs": [...]}]}
    typecheck     JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by    TEXT,
    decided_at    TIMESTAMPTZ,
    run_id        UUID REFERENCES runs(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Column names deliberately overlap backend_v2's `traces` where the concept is
-- the same (task_node_id, outcome, cost, latency_ms, parent_trace_id,
-- timestamp) so the two tables reconcile into one with ADD COLUMN rather than
-- a rename-everything migration.
CREATE TABLE IF NOT EXISTS traces (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             UUID REFERENCES runs(id) ON DELETE CASCADE,
    task_node_id       UUID REFERENCES task_nodes(id) ON DELETE SET NULL,
    implementation_id  UUID REFERENCES implementations(id) ON DELETE SET NULL,
    node_ref           TEXT,
    attempt            INTEGER NOT NULL DEFAULT 0,
    input              JSONB NOT NULL DEFAULT '{}',
    output             JSONB NOT NULL DEFAULT '{}',
    outcome            TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
    error              TEXT,
    cache_hit          BOOLEAN NOT NULL DEFAULT FALSE,
    cost               NUMERIC NOT NULL DEFAULT 0,
    latency_ms         INTEGER NOT NULL DEFAULT 0,
    parent_trace_id    UUID REFERENCES traces(id) ON DELETE SET NULL,
    timestamp          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cache_entries (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_node_id       UUID NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    fingerprint        TEXT NOT NULL,
    implementation_id  UUID NOT NULL REFERENCES implementations(id) ON DELETE CASCADE,
    params             JSONB NOT NULL DEFAULT '{}',
    hits               INTEGER NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cache_task_fingerprint
    ON cache_entries(task_node_id, fingerprint);


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- HNSW rather than ivfflat: ivfflat derives centroids from existing rows, and
-- this index is built on an empty table at bootstrap.
--
-- PARTIAL on the live-row predicate, and this is the load-bearing part. An
-- unfiltered HNSW index keeps superseded rows in the proximity graph: the
-- search still walks them, they still consume the ef_search budget, and recall
-- against live tasks degrades silently as versions accumulate. Nothing errors;
-- the results just quietly get worse.
CREATE INDEX IF NOT EXISTS idx_tn_embedding ON task_nodes
    USING hnsw (embedding vector_cosine_ops) WHERE t_invalid IS NULL;

CREATE INDEX IF NOT EXISTS idx_tn_fts ON task_nodes
    USING gin (to_tsvector('english', name || ' ' || COALESCE(description, '')))
    WHERE t_invalid IS NULL;

CREATE INDEX IF NOT EXISTS idx_tn_validity ON task_nodes(t_valid, t_invalid);

-- One live edge of a given type between two nodes. Makes seeding idempotent
-- and rules out the parallel-duplicate-edge state, which nothing in the
-- traversal code would notice but which doubles a node's apparent fan-out.
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_edges_live
    ON task_edges(edge_type, source_id, target_id) WHERE t_invalid IS NULL;

CREATE INDEX IF NOT EXISTS idx_edges_source   ON task_edges(source_id, source_table);
CREATE INDEX IF NOT EXISTS idx_edges_target   ON task_edges(target_id, target_table);
CREATE INDEX IF NOT EXISTS idx_edges_validity ON task_edges(t_valid, t_invalid);

CREATE INDEX IF NOT EXISTS idx_impl_task    ON implementations(task_node_id) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_evals_task   ON evals(task_node_id) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_eval_results ON eval_results(implementation_id, ran_at DESC);

CREATE INDEX IF NOT EXISTS idx_traces_run    ON traces(run_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_traces_task   ON traces(task_node_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_traces_outcome ON traces(outcome);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status      ON runs(status, created_at DESC);

COMMIT;
