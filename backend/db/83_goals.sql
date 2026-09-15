-- Migration 83: canonical `goals` table (founder directive "ingestion.md"
-- Sec 2-3, 2026-09-15) -- Goal becomes a real, stable-ID object instead of
-- two independent, uncoordinated free-text TEXT columns
-- (`procedures.goal`, `implementations.goal` -- migration 80's own
-- docstring already called that pair "the founder directive's own minimal
-- goal model... because no entity exists"). This migration is that entity.
--
-- WHY NOW: confirmed by direct audit (not docs) that NO goal entity exists
-- anywhere in schema/models/services -- only the two TEXT columns above,
-- linked by convention (exact-string lookup) and never by FK. Procedure
-- achieves a Goal, an Implementation satisfies a Goal, a ProcedureStep
-- references a Goal (ingestion.md Sec 3) -- none of that is expressible
-- without a table with a real primary key.
--
-- Deliberately LIGHTWEIGHT (ingestion.md Sec 2's own instruction: "do NOT
-- make Goal a giant knowledge ontology... significantly lighter than
-- Procedure or Implementation"). No steps, no invariants, no verification
-- statistics living here -- those stay on `procedures`/`implementations`.
--
-- Conventions followed, matching every table since 18/33 (see CLAUDE.md
-- "Code conventions that are easy to violate"):
--   - id UUID PK, app-side uuid7() at real write paths (app/utils/ids.py),
--     gen_random_uuid() DB default only as last-resort fallback for a
--     non-repository write (procedures'/implementations' own pattern).
--   - bitemporal t_valid/t_invalid/t_created/t_expired + version, so a
--     Goal can be corrected via invalidate-and-append later without ever
--     mutating a row other tables' FKs already point at.
--   - mandatory visibility + owner_id pair (access.py::visibility_predicate()
--     requires both -- ticket 09 pair rule, procedures'/implementations' own
--     comment).
--   - scope_type/scope_entity_id nullable at the DB layer on purpose (V0
--     gate -- app/services/v0_gate.py -- owns the "scope is required"
--     error message; the DB column itself stays nullable, same as every
--     other V0-gated table. CLAUDE.md: "the DB columns are nullable on
--     purpose.").
--   - TEXT + CHECK for status (not a DB enum) -- matches `implementations`'
--     own stated reasoning: closed-vocabulary-lives-in-Python, DB CHECK is
--     defense-in-depth against a direct-SQL writer, not the source of truth.
--
-- Dedup (ingestion.md Sec 8, tier 1 ONLY -- exact normalized-name match):
-- `normalize_goal_name()` (SQL function below) MUST stay in exact lock-step
-- with its Python twin (app/services/goals.py::normalize_goal_name) --
-- both lowercase, strip non-alphanumerics to spaces, collapse whitespace.
-- A partial unique index enforces this at the DB layer, not just in
-- application code, split into a GLOBAL-scope index (dedups across the
-- whole corpus) and a LOCAL-scope index (dedups only within one
-- (scope_type, scope_entity_id) pair -- ingestion.md Sec 9: a local goal
-- at one project must not silently collide with a same-named local goal
-- at a different one). Tiers 2-5 (aliases, semantic normalization,
-- embedding similarity, LLM adjudication) are explicitly NOT implemented
-- here -- see app/services/goals.py's own module docstring.
--
-- Next free migration number confirmed: 84 (highest existing before this
-- file was 82).

CREATE OR REPLACE FUNCTION normalize_goal_name(input TEXT) RETURNS TEXT AS $$
    SELECT trim(
        regexp_replace(
            regexp_replace(lower(trim(input)), '[^a-z0-9 ]', ' ', 'g'),
            '\s+', ' ', 'g'
        )
    );
$$ LANGUAGE sql IMMUTABLE;

CREATE TABLE IF NOT EXISTS goals (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    canonical_name           TEXT NOT NULL,
    -- Dedup key (tier 1). Populated by the writer (app/services/goals.py),
    -- never a GENERATED column -- this repo's own stated convention
    -- (migration 44's retrieval_document: "no generated column") of
    -- keeping derived-text columns as plain writer-populated fields.
    normalized_name          TEXT NOT NULL,

    description              TEXT,
    input_schema             JSONB NOT NULL DEFAULT '{}',
    -- Semantic state/result intended after success. NEVER fabricated for
    -- a Goal where nothing gives real evidence of it -- same "do not
    -- fabricate expected_outcome" rule migration 80 already stated for
    -- Implementation; here it stays the empty-object default, not a
    -- guessed value.
    expected_outcome         JSONB NOT NULL DEFAULT '{}',
    verification_requirement JSONB NOT NULL DEFAULT '{}',

    -- 'candidate' (freshly extracted/user-submitted, not yet reviewed),
    -- 'active' (live, retrievable), 'deprecated' (superseded, kept for
    -- history), 'merged' (dedup collapsed into merged_into_id -- explicit
    -- merge workflow, ingestion.md Sec 8's last paragraph: "support
    -- explicit merge/review workflow if ambiguity exists", never a
    -- silent overwrite of the losing row).
    status                   TEXT NOT NULL DEFAULT 'candidate',
    merged_into_id           UUID REFERENCES goals(id) ON DELETE SET NULL,

    -- V0 gate fields (Band 1.3 discipline, same as procedures/claims):
    -- nullable at the DB layer, required by app/services/v0_gate.py at
    -- the write boundary.
    provenance               TEXT,
    -- Free text describing which ingestion path produced this row (e.g.
    -- 'skill_ingestion', 'trace_extraction', 'user_created',
    -- 'backfill_migration_83') -- ingestion.md Sec 2's `created_from`
    -- field. Not an enum: the real vocabulary of sources will grow.
    created_from             TEXT,

    aliases                  TEXT[] NOT NULL DEFAULT '{}',
    tags                     TEXT[] NOT NULL DEFAULT '{}',

    version                  INTEGER NOT NULL DEFAULT 1,

    t_valid                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid                TIMESTAMPTZ,
    t_created                TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired                TIMESTAMPTZ,

    created_by               TEXT,
    visibility                visibility_level NOT NULL DEFAULT 'public',
    owner_id                 TEXT,
    scope_type               TEXT,
    scope_entity_id          TEXT
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'goals_canonical_name_chk') THEN
        ALTER TABLE goals ADD CONSTRAINT goals_canonical_name_chk
            CHECK (length(trim(canonical_name)) > 0);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'goals_status_chk') THEN
        ALTER TABLE goals ADD CONSTRAINT goals_status_chk
            CHECK (status IN ('candidate', 'active', 'deprecated', 'merged'));
    END IF;

    -- A row is 'merged' iff it actually points somewhere -- prevents the
    -- exact silent-inconsistency class CLAUDE.md's conventions repeatedly
    -- guard against elsewhere (implementations_deprecated_at_chk is the
    -- same shape: a lifecycle flag and its pointer/timestamp must agree).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'goals_merged_consistency_chk') THEN
        ALTER TABLE goals ADD CONSTRAINT goals_merged_consistency_chk
            CHECK ((status = 'merged') = (merged_into_id IS NOT NULL));
    END IF;

    -- Same vocabulary + same NOT VALID discipline as
    -- scope_type_chk_implementations (migration 33) -- V0 gate owns the
    -- Python-side error message; this is defense-in-depth only.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'goals_scope_type_chk') THEN
        ALTER TABLE goals ADD CONSTRAINT goals_scope_type_chk
            CHECK (scope_type IS NULL OR scope_type IN (
                'global', 'organization', 'team', 'project', 'repository',
                'branch', 'user', 'session', 'task', 'entity')) NOT VALID;
    END IF;
END $$;

-- Tier-1 dedup, enforced at the DB layer (not just in
-- app/services/goals.py::find_or_create_goal). Split global/local exactly
-- as ingestion.md Sec 9 requires: a GLOBAL goal's canonical_name is unique
-- across the whole corpus; a LOCAL goal's canonical_name is unique only
-- within its own (scope_type, scope_entity_id) pair. `status <> 'merged'`
-- so a merged (superseded) row never blocks a fresh insert from reclaiming
-- its normalized_name.
CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_global_normalized_name
    ON goals(normalized_name)
    WHERE t_invalid IS NULL AND status <> 'merged'
      AND (scope_type IS NULL OR scope_type = 'global');

CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_local_normalized_name
    ON goals(normalized_name, scope_type, scope_entity_id)
    WHERE t_invalid IS NULL AND status <> 'merged'
      AND scope_type IS NOT NULL AND scope_type <> 'global';

CREATE INDEX IF NOT EXISTS idx_goals_scope ON goals(scope_type, scope_entity_id);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_public ON goals(id)
    WHERE visibility = 'public' AND t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_goals_aliases ON goals USING gin (aliases);
CREATE INDEX IF NOT EXISTS idx_goals_fts ON goals
    USING gin (to_tsvector('english', canonical_name || ' ' || COALESCE(description, '')));

-- ============================================================
-- Relationships (ingestion.md Sec 3): Procedure achieves a Goal;
-- Implementation satisfies a Goal. Both ADDITIVE and nullable -- the
-- existing `procedures.goal`/`implementations.goal` TEXT columns are
-- UNTOUCHED (still written, still read by every existing caller); these
-- are new columns alongside them, not a replacement. ProcedureStep's own
-- goal_id (ingestion.md Sec 3's "ProcedureStep: goal_id") is NOT added
-- here -- steps live as JSONB list items (`procedures.steps`), not a
-- table with its own columns, so a per-step goal reference is a
-- writer-populated `"goal_id"` key inside that JSONB, not a schema
-- change. That wiring is app/services/procedure_extraction's job, not
-- this migration's -- flagged as real, not silently done.
-- ============================================================

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS achieves_goal_id UUID REFERENCES goals(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_procedures_achieves_goal
    ON procedures(achieves_goal_id) WHERE achieves_goal_id IS NOT NULL;

ALTER TABLE implementations
    ADD COLUMN IF NOT EXISTS goal_id UUID REFERENCES goals(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_implementations_goal_id
    ON implementations(goal_id) WHERE goal_id IS NOT NULL;

-- ============================================================
-- Backfill (ingestion.md Sec 24: "Backfill Goal identities from existing
-- ProcedureStep.goal, capability fields, Implementation semantics...
-- Generate migration/backfill tooling" -- an explicit, stated exception to
-- CLAUDE.md's default fresh-start/no-backfill rule for this initiative,
-- same precedent migration 80 already used for `classification`).
--
-- Deliberately simplified for this pass, stated honestly (non-blocking,
-- not silently claimed as full fidelity): every backfilled Goal is
-- created at scope_type='global' regardless of the source row's own
-- scope -- ingestion.md Sec 9's own "prefer generalized global Goals
-- where possible", and avoids a local-goal explosion from thousands of
-- historical per-project procedure/implementation rows that likely
-- express the same underlying outcome. A future pass MAY re-scope
-- genuinely local-only historical goals; not attempted here.
--
-- `goals` is empty before this runs (just created above), so no
-- conflict against a pre-existing row is possible -- the GROUP BY below
-- is the only dedup needed within this statement.
-- ============================================================

INSERT INTO goals (
    canonical_name, normalized_name, status, provenance, created_from,
    scope_type, created_by, visibility
)
SELECT
    -- Shortest non-empty original text sharing this normalized form, as a
    -- readable representative -- never fabricated, always a real value
    -- that existed in the corpus.
    (ARRAY_AGG(original_goal ORDER BY length(original_goal) ASC))[1] AS canonical_name,
    normalized,
    'active',                     -- already proven by real, live rows -- not an unreviewed candidate
    'system_pending_review',
    'backfill_migration_83',
    'global',
    'migration_83_backfill',
    'public'
FROM (
    SELECT goal AS original_goal, normalize_goal_name(goal) AS normalized
    FROM procedures
    WHERE goal IS NOT NULL AND length(trim(goal)) > 0 AND t_invalid IS NULL
    UNION ALL
    SELECT goal AS original_goal, normalize_goal_name(goal) AS normalized
    FROM implementations
    WHERE goal IS NOT NULL AND length(trim(goal)) > 0
) AS all_goal_text
GROUP BY normalized
HAVING normalized <> '';

-- Without this, the planner has no real row-count estimate for the
-- table it just populated in this same transaction (default/stale
-- stats badly underestimate it), and picks a nested loop that re-scans
-- `procedures`/`implementations` once per `goals` row instead of an
-- index/hash lookup -- confirmed live: >5 minutes without this line,
-- ~15 seconds with it, same two UPDATE statements, same data.
ANALYZE goals;

UPDATE procedures p
SET achieves_goal_id = g.id
FROM goals g
WHERE g.scope_type = 'global'
  AND g.created_from = 'backfill_migration_83'
  AND g.normalized_name = normalize_goal_name(p.goal)
  AND p.goal IS NOT NULL
  AND p.achieves_goal_id IS NULL;

UPDATE implementations i
SET goal_id = g.id
FROM goals g
WHERE g.scope_type = 'global'
  AND g.created_from = 'backfill_migration_83'
  AND g.normalized_name = normalize_goal_name(i.goal)
  AND i.goal IS NOT NULL
  AND i.goal_id IS NULL;
