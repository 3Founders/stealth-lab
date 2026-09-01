-- Migration 33: durable Implementation registry.
--
-- Why this lands now: app/execution/implementations.py already owns a
-- closed KIND vocabulary + a registry of which kinds have a real
-- executor STRATEGY today (frontier, deterministic -- see
-- app/execution/providers.py, landed the previous wave). Neither module
-- persists anything -- there is no durable row a PlanNode.implementation_id
-- or executions.implementation_id (both real, typed, UUID-capable columns
-- since migration 23, confirmed by grep to have ZERO real writers) can
-- actually point at. This migration adds exactly that durable object,
-- and nothing else -- it does not touch, replace, or widen
-- IMPLEMENTATION_KINDS, resolve_implementation(), or ImplementationProvider.
--
-- Two tables, not five (IMPLEMENTATION REGISTRY DIRECTIVE Sec 71's own
-- "do not create five tables unnecessarily" instruction):
--   1. implementations       -- one row per concrete, addressable
--      implementation identity (a specific provider+name+version).
--   2. implementation_tasks  -- link table, because one implementation
--      may genuinely satisfy more than one task (directive Sec 18) and a
--      single task_node_id column would force a false one-to-one model.
-- No implementation_versions/implementation_capability_stats/
-- implementation_provenance/implementation_credentials tables: version
-- identity lives on `implementations` itself (a new version = a new row,
-- same invalidate-and-append shape `procedures`/`claims` already use --
-- NOT a new pattern); capability stats are DERIVED from `evidence`
-- (evidence_target_type_chk in migration 24 already allows
-- target_type='implementation' -- confirmed, no ALTER needed here),
-- exactly like procedure_evidence_stats already derives from evidence
-- rather than a separate counter table; provenance/attribution are
-- first-class COLUMNS here (source_ref/author/license/derived_from),
-- not a separate table, because they are 1:1 facts about one
-- implementation row, not a 1:many relationship; credentials are
-- NEVER stored here at all (directive Sec 13/50's explicit rule) --
-- auth_requirements below stores only a credential_ref (a pointer the
-- actor/session resolves), never a secret.
--
-- Additive, idempotent, safe against both an empty DB and a populated
-- one -- same discipline as every migration since 18. Next free number
-- confirmed: 32 was highest before this file.

-- ============================================================
-- 0. implementations
--
-- Identity: (name, provider, version) is the natural key a human reads
-- ("Graphify.query_graph v2"); `id` is the real UUID other tables
-- reference. `kind` is TEXT + CHECK (not a DB enum) matching
-- implementations.py's own closed-vocabulary-lives-in-Python pattern --
-- the SAME discipline scope_type uses (v0_gate.py owns the Python-side
-- vocabulary + error message, the DB CHECK is defense-in-depth against
-- a direct-SQL writer, migration 22's own stated split).
-- ============================================================
CREATE TABLE IF NOT EXISTS implementations (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name                 TEXT NOT NULL,
    description          TEXT,
    kind                 TEXT NOT NULL,
    provider             TEXT NOT NULL,
    version              INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),

    -- Lifecycle status (directive Sec 5) -- an operational axis, mutable
    -- in place (UPDATE, not invalidate-and-append): a row's identity
    -- (name/provider/version/locator) never changes after creation: only
    -- ITS OWN status/verification_status/deprecated_at/disabled_at do.
    -- Historical `executions.implementation_id` bindings stay resolvable
    -- forever regardless of a later status change, because the row is
    -- never deleted and never renumbered -- exactly directive Sec 63's
    -- "historical executions remain interpretable" requirement, achieved
    -- by simply never deleting or mutating identity, not by a second
    -- append-only version chain this object does not need.
    status               TEXT NOT NULL DEFAULT 'candidate',

    -- Separate axis from `status` (directive Sec 61): AVAILABLE-but-
    -- UNVERIFIED must never be conflated with VERIFIED. `status='active'`
    -- answers "is this the current, non-deprecated identity"; this
    -- column answers "has anyone actually validated it works" --
    -- orthogonal, same reasoning `procedures.verification_state` and
    -- `procedures.staleness` stay two separate ENUM columns rather than
    -- one collapsed status.
    verification_status  TEXT NOT NULL DEFAULT 'unverified',

    -- Content identity (directive Sec 60) -- sha256 or equivalent.
    -- Nullable: not every implementation is artifact-backed (a frontier
    -- model call has no content hash; a WASM artifact does).
    content_hash         TEXT,

    -- WHERE it lives / HOW to invoke (directive Sec 9-10). Genuinely
    -- variable per protocol (mcp/https/wasm/model/local) -- JSONB is the
    -- correct choice here per this migration's own JSONB-discipline rule
    -- below, not a shortcut. NEVER credentials (enforced at the service
    -- boundary; auth_requirements below is the one legal place a
    -- credential REFERENCE may live).
    locator               JSONB NOT NULL DEFAULT '{}',
    invocation            JSONB NOT NULL DEFAULT '{}',

    -- I/O contract (directive Sec 11) -- the actual invocation interface,
    -- distinct from a task_nodes.io_schema's semantic requirement.
    input_schema          JSONB NOT NULL DEFAULT '{}',
    output_schema         JSONB NOT NULL DEFAULT '{}',

    -- Requirements (directive Sec 12-14), each genuinely variable in
    -- shape per implementation kind/provider -- JSONB.
    requirements           JSONB NOT NULL DEFAULT '{}',
    auth_requirements       JSONB NOT NULL DEFAULT '{}',
    resource_requirements    JSONB NOT NULL DEFAULT '{}',

    -- Provenance / attribution / license (directive Sec 15-16/52/68).
    -- First-class columns, not JSONB: these are the fields a future
    -- Solution/Implementation page filters and displays directly.
    -- Unknown license is represented as NULL, never guessed (directive
    -- Sec 16's explicit rule) -- application code must never coalesce
    -- this to a default string.
    source_ref             TEXT,
    author                 TEXT,
    license                TEXT,

    -- Forking lineage (directive Sec 69) -- real self-FK, same table,
    -- so referential integrity is enforceable (unlike the cross-table
    -- "forward-compatible handle, no FK" pattern evidence.source_id
    -- uses for tables that don't exist yet -- this one does).
    derived_from            UUID REFERENCES implementations(id),

    deprecated_at            TIMESTAMPTZ,
    disabled_at               TIMESTAMPTZ,

    created_by                TEXT,
    -- Ticket 09 pair rule: every new table carries visibility + owner_id
    -- (access.py::visibility_predicate()/scope_predicates() require both).
    visibility                 visibility_level NOT NULL DEFAULT 'public',
    owner_id                    TEXT,
    scope_type                   TEXT,
    scope_entity_id                TEXT,

    t_created                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_kind_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_kind_chk
            -- Mirrors app.execution.implementations.IMPLEMENTATION_KINDS
            -- exactly (deterministic/tool/slm/frontier/human) PLUS the
            -- directive's own named future-compatible kinds (wasm/
            -- computer_use/api) so a row can be REGISTERED as one of
            -- those ahead of a real Python-side executor existing --
            -- directive Sec 6: "future/runtime kinds should remain
            -- compatible with wasm/computer_use/api" -- without this
            -- meaning any of them are claimed runnable (that claim lives
            -- in the registry's own resolve()/discover() logic, never in
            -- the mere ability to store a row).
            CHECK (kind IN (
                'deterministic', 'tool', 'slm', 'frontier', 'human',
                'wasm', 'computer_use', 'api'
            ));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_status_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_status_chk
            CHECK (status IN ('candidate', 'active', 'deprecated', 'disabled', 'quarantined'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_verification_status_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_verification_status_chk
            CHECK (verification_status IN ('unverified', 'verified'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_name_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_name_chk
            CHECK (length(trim(name)) > 0);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_provider_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_provider_chk
            CHECK (length(trim(provider)) > 0);
    END IF;

    -- Consistency between the two lifecycle timestamps and `status`: a
    -- deprecated_at/disabled_at value without the matching status (or
    -- vice versa) would be exactly the kind of silently-drifting derived
    -- fact this repo's own conventions refuse to allow.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_deprecated_at_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_deprecated_at_chk
            CHECK ((deprecated_at IS NULL) OR (status IN ('deprecated', 'disabled', 'quarantined')));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_disabled_at_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_disabled_at_chk
            CHECK ((disabled_at IS NULL) OR (status IN ('disabled', 'quarantined')));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_implementations') THEN
        ALTER TABLE implementations ADD CONSTRAINT scope_type_chk_implementations
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

-- (name, provider, version) is the natural identity a caller resolves
-- by -- unique so two rows can never silently claim the same identity
-- (directive Sec 4/19's "globally addressable" + "distinguishable
-- versions" requirements, enforced, not just documented).
CREATE UNIQUE INDEX IF NOT EXISTS idx_implementations_identity
    ON implementations(name, provider, version);

-- Hot query paths (directive Sec 72): status-filtered listing, provider
-- browsing, kind-filtered candidate search, content-hash lookup for
-- replay/dedup, scope-filtered reads via the standard predicate pair.
CREATE INDEX IF NOT EXISTS idx_implementations_status
    ON implementations(status);
CREATE INDEX IF NOT EXISTS idx_implementations_provider
    ON implementations(provider);
CREATE INDEX IF NOT EXISTS idx_implementations_kind
    ON implementations(kind);
CREATE INDEX IF NOT EXISTS idx_implementations_content_hash
    ON implementations(content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_implementations_scope
    ON implementations(scope_type, scope_entity_id);

-- ============================================================
-- 1. implementation_tasks -- link table (directive Sec 18: "do not
-- require a one-to-one implementation/task model if the semantics
-- don't justify it"). A real FK to task_nodes: unlike evidence's
-- polymorphic targets (which span tables that didn't all exist when
-- evidence was designed), task_nodes is one single, stable, always-
-- present table, so an enforced FK is the honest choice here, not
-- under-specification.
-- ============================================================
CREATE TABLE IF NOT EXISTS implementation_tasks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    implementation_id  UUID NOT NULL REFERENCES implementations(id),
    task_node_id       UUID NOT NULL REFERENCES task_nodes(id),
    created_by         TEXT,
    t_created          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (implementation_id, task_node_id)
);

CREATE INDEX IF NOT EXISTS idx_implementation_tasks_task
    ON implementation_tasks(task_node_id);
CREATE INDEX IF NOT EXISTS idx_implementation_tasks_implementation
    ON implementation_tasks(implementation_id);

-- ============================================================
-- 2. Sequencing note, matching migration 24's own precedent: evidence
-- rows targeting an implementation are ALREADY representable today
-- (evidence_target_type_chk has allowed target_type='implementation'
-- since migration 24) -- no ALTER needed here. This migration only adds
-- the object those rows can now meaningfully point AT; it adds no
-- capability-stats view of its own (directive Sec 36 explicitly asks
-- the capability SERVICE layer to reuse the existing
-- procedure_extraction/capability.py machinery, not a second SQL view
-- duplicating procedure_evidence_stats' own shape for a different
-- target_type -- that is real service-layer work, not a schema change).
-- ============================================================
