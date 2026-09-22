-- Migration 102: V1 contribution + verification + ranking + Credits system
-- (founder directive "V1 contribution/verification/ranking/Credits system").
--
-- WHY THIS LANDS NOW: the product loop is Goal -> Procedures/Benchmarks ->
-- Run -> Evidence -> Verification -> Ranking -> Reuse -> Reward ->
-- Improvement. Everything from "Run" through "Ranking" already exists
-- (execution_runs/evidence/procedures.verification_state, product_model.py's
-- Wilson-bound leaderboard). What does NOT exist yet is the CONTRIBUTION
-- workflow in front of it (a reviewable submission, distinct from a
-- procedure's own candidate/verified lifecycle) and the REWARD workflow
-- behind it (Credits). This migration adds exactly those two things, plus
-- the one new signal both depend on: a usage event that distinguishes
-- self-use from independent reuse.
--
-- REUSE, NOT DUPLICATION (confirmed by direct audit before writing this
-- file, not assumed):
--   - Goal (for this loop's purposes) = the EXISTING `problems` table
--     (migration 35: objective + constraints + benchmarks + solutions).
--     The lighter `goals` table (migration 83) is untouched and still
--     serves procedures.achieves_goal_id exactly as before -- a Procedure
--     submitted here still resolves/creates its `goals` row the same way
--     every other capture_procedure() caller does. Two Goal-shaped tables
--     already coexist in this schema (confirmed pre-existing, not
--     introduced here); this migration does not add a third.
--   - Benchmark = the EXISTING `benchmarks` table (migration 35). A
--     `benchmark_submissions` review row here creates/updates a `benchmarks`
--     row on acceptance via the existing product_model.create_benchmark();
--     no new "what a benchmark is" table.
--   - Procedure = the EXISTING `procedures` table. A `procedure_submissions`
--     review row here creates a `procedures` row (via the existing
--     app.services.procedures.capture_procedure(), always born candidate,
--     ticket 13's rule -- unchanged) at SUBMISSION time, so it is
--     immediately inspectable; the submission's own status
--     (candidate/needs_review/accepted/rejected) is a SEPARATE, orthogonal
--     workflow track governing goal-page listing + reward eligibility, not
--     a second copy of procedures.verification_state or .approval_status.
--   - Run / Evidence / verified-vs-claimed = the EXISTING execution_runs /
--     evidence tables and procedures.record_execution_outcome() promotion
--     logic (candidate -> verified, 10 successes / 3 distinct contexts /
--     zero failures). This migration does not touch that promotion logic;
--     `procedure_usage_events` sits ALONGSIDE it, adding exactly the one
--     fact evidence/execution_runs do not carry today: whether the
--     executor was the procedure's own contributor (self-use, never
--     rewarded) or someone else (independent reuse, the strongest signal).
--   - Ranking = the EXISTING Wilson-interval helper
--     (app.services.procedure_extraction.capability.wilson_interval,
--     already used by product_model.py's Solution leaderboard). No second
--     statistics implementation is added; app/economy/ranking.py (Python,
--     no new table) calls the same function against a Procedure's
--     evidence_summary.
--   - Rate limiting = the EXISTING rate_limit_events table +
--     app.services.governance.RateLimiter. No new table.
--   - Duplicate/near-duplicate detection = the EXISTING pgvector
--     `embedding <=> $1::vector` pattern (app/services/dedup.py). No new
--     similarity engine; procedure_submissions/benchmark_submissions each
--     get their own `embedding VECTOR(1024)` column, scored with the same
--     operator against sibling submissions and existing procedures/goal
--     solutions.
--
-- GENUINELY NEW (nothing to reuse -- confirmed no prior art in db/*.sql):
--   - A submission/review workflow in front of Procedure and Benchmark
--     creation (procedure_submissions, benchmark_submissions).
--   - A self-use-vs-independent-reuse usage signal (procedure_usage_events).
--   - Contributor Credits as an immutable, reconstructable ledger
--     (credit_ledger_events). This is a DELIBERATE departure from
--     contributor_profiles' migration-48 stated philosophy ("there is no
--     per-actor score... cannot be re-derived from provenance") -- Credits
--     genuinely is a new, real, spendable-later-if-ever number the founder
--     directive asks for, so it gets its own honestly-named, explicitly
--     append-only table rather than being folded into contributor_profiles.
--     Contributor STANDING, by contrast, follows the migration-48
--     philosophy exactly and stores NOTHING here -- it stays a pure
--     read-time computation in app/economy/standing.py over rows that
--     already exist (accepted submissions, verified usage events,
--     evidence), the same pattern app/services/contributors.py already
--     uses for verified_procedures/procedures_authored.
--
-- Conventions followed (matching every table since 18/33/35/83 -- the
-- CLAUDE.md reference several of those files make does not exist in this
-- checkout, so these conventions are reconstructed from the migrations
-- themselves, same posture migration 83 already documents):
--   - id UUID PK, app-side uuid7() at real write paths.
--   - mandatory owner_id + visibility pair (ticket 09).
--   - scope_type/scope_entity_id nullable at the DB layer, V0-gated at the
--     write boundary; CHECK is NOT VALID, identical 10-value vocabulary.
--   - provenance as free TEXT, not an enum.
--   - status as TEXT + CHECK, not a native enum (closed vocabulary lives in
--     Python; the DB CHECK is defense-in-depth only).
--   - created_at/updated_at (not the bitemporal quartet) for these
--     workflow/association rows -- they are siblings of problems/
--     benchmarks/solutions/evaluations (migration 35), which use the same
--     simpler pattern for the same reason: a submission's status changes
--     in place, it does not need point-in-time correction of prior content.
--   - the credit ledger is the one exception: genuinely append-only, so it
--     gets its own immutability trigger (same shape as execution_plans'
--     blanket freeze trigger) instead of created_at/updated_at semantics
--     implying mutability.
--   - idempotent: every CREATE uses IF NOT EXISTS; every named constraint
--     is wrapped in the standard DO $$ ... IF NOT EXISTS (pg_constraint)
--     guard.
--
-- Next free migration number: 103.

-- ============================================================
-- 1. procedure_submissions -- the review workflow in front of a Procedure
-- ============================================================
CREATE TABLE IF NOT EXISTS procedure_submissions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    goal_id                     UUID NOT NULL REFERENCES problems(id),
    submission_type             TEXT NOT NULL,          -- 'new' | 'improvement'
    parent_procedure_row_id     UUID REFERENCES procedures(id) ON DELETE SET NULL,
    procedure_row_id            UUID REFERENCES procedures(id) ON DELETE SET NULL,
    -- Set the moment the submission is captured (capture_procedure() is
    -- called synchronously) -- "accepted" governs reward/listing
    -- eligibility, never whether the Procedure exists at all.

    name                        TEXT NOT NULL,
    content                     JSONB NOT NULL DEFAULT '{}',   -- raw submitted steps/preconditions/etc, kept
                                                                 -- verbatim even if the live procedures row is
                                                                 -- later superseded -- an audit snapshot, not a
                                                                 -- second source of truth.
    rationale                   TEXT,
    applicability_context       JSONB NOT NULL DEFAULT '{}',
    constraints                 JSONB NOT NULL DEFAULT '[]',
    implementation_requirements JSONB NOT NULL DEFAULT '{}',   -- free-form (tools/executors/etc) -- there is no
                                                                 -- Implementation object (migration 98); this is
                                                                 -- descriptive text/JSON, never a foreign key.
    supporting_evidence         JSONB NOT NULL DEFAULT '[]',    -- links/refs the submitter supplied at submission
                                                                 -- time; distinct from real `evidence` rows, which
                                                                 -- are only ever created by an actual run.

    status                      TEXT NOT NULL DEFAULT 'candidate',
    status_reason               TEXT,
    layer1_result                JSONB NOT NULL DEFAULT '{}',   -- deterministic/schema/duplicate check output
    layer2_result                JSONB NOT NULL DEFAULT '{}',   -- LLM/heuristic evaluation output; NEVER the sole
                                                                 -- source of an 'accepted' status (see CHECK below).
    embedding                   VECTOR(1024),
    duplicate_of_submission_id  UUID REFERENCES procedure_submissions(id) ON DELETE SET NULL,
    duplicate_score              REAL,

    submitted_by                TEXT NOT NULL,
    reviewed_by                  TEXT,
    reviewed_at                  TIMESTAMPTZ,

    provenance                  TEXT,
    scope_type                  TEXT,
    scope_entity_id              TEXT,
    owner_id                     TEXT,
    visibility                   TEXT NOT NULL DEFAULT 'public',

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_submissions_type_chk') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_type_chk
            CHECK (submission_type IN ('new','improvement'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_submissions_status_chk') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_status_chk
            CHECK (status IN ('candidate','needs_review','accepted','rejected'));
    END IF;
    -- Anti-fabrication, same discipline as migration 35's evaluation-lineage
    -- trigger: a submission cannot be 'accepted' or 'rejected' without a
    -- human reviewer of record -- Layer 1/2 (deterministic + LLM/heuristic)
    -- may only ever leave a submission at candidate or needs_review.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_submissions_review_requires_reviewer_chk') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_review_requires_reviewer_chk
            CHECK ((status NOT IN ('accepted','rejected')) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_submissions_improvement_requires_parent_chk') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_improvement_requires_parent_chk
            CHECK ((submission_type <> 'improvement') OR (parent_procedure_row_id IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_submissions_visibility_chk') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT procedure_submissions_visibility_chk
            CHECK (visibility IN ('public','private','unlisted'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='scope_type_chk_procedure_submissions') THEN
        ALTER TABLE procedure_submissions ADD CONSTRAINT scope_type_chk_procedure_submissions
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_procedure_submissions_goal        ON procedure_submissions(goal_id);
CREATE INDEX IF NOT EXISTS idx_procedure_submissions_status      ON procedure_submissions(status);
CREATE INDEX IF NOT EXISTS idx_procedure_submissions_submitter   ON procedure_submissions(submitted_by);
CREATE INDEX IF NOT EXISTS idx_procedure_submissions_parent      ON procedure_submissions(parent_procedure_row_id) WHERE parent_procedure_row_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_procedure_submissions_procedure   ON procedure_submissions(procedure_row_id) WHERE procedure_row_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_procedure_submissions_touch ON procedure_submissions;
CREATE TRIGGER trg_procedure_submissions_touch BEFORE UPDATE ON procedure_submissions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================
-- 2. benchmark_submissions -- the review workflow in front of a Benchmark
-- ============================================================
CREATE TABLE IF NOT EXISTS benchmark_submissions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    goal_id               UUID NOT NULL REFERENCES problems(id),
    benchmark_id          UUID REFERENCES benchmarks(id) ON DELETE SET NULL,

    name                  TEXT NOT NULL,
    description           TEXT,
    success_criteria      JSONB NOT NULL DEFAULT '{}',
    invariants            JSONB NOT NULL DEFAULT '[]',   -- constraints the benchmark holds fixed
    verification_method    JSONB NOT NULL DEFAULT '{}',   -- how success is actually checked (executable / manual
                                                            -- rubric / external report -- a Goal may not have a
                                                            -- conventional test suite; this is intentionally free-form)

    status                TEXT NOT NULL DEFAULT 'candidate',
    status_reason         TEXT,
    layer1_result          JSONB NOT NULL DEFAULT '{}',
    layer2_result          JSONB NOT NULL DEFAULT '{}',
    embedding             VECTOR(1024),
    duplicate_of_submission_id UUID REFERENCES benchmark_submissions(id) ON DELETE SET NULL,
    duplicate_score        REAL,

    submitted_by          TEXT NOT NULL,
    reviewed_by            TEXT,
    reviewed_at            TIMESTAMPTZ,

    provenance            TEXT,
    scope_type            TEXT,
    scope_entity_id        TEXT,
    owner_id               TEXT,
    visibility              TEXT NOT NULL DEFAULT 'public',

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='benchmark_submissions_status_chk') THEN
        ALTER TABLE benchmark_submissions ADD CONSTRAINT benchmark_submissions_status_chk
            CHECK (status IN ('candidate','needs_review','accepted','rejected'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='benchmark_submissions_review_requires_reviewer_chk') THEN
        ALTER TABLE benchmark_submissions ADD CONSTRAINT benchmark_submissions_review_requires_reviewer_chk
            CHECK ((status NOT IN ('accepted','rejected')) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='benchmark_submissions_visibility_chk') THEN
        ALTER TABLE benchmark_submissions ADD CONSTRAINT benchmark_submissions_visibility_chk
            CHECK (visibility IN ('public','private','unlisted'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='scope_type_chk_benchmark_submissions') THEN
        ALTER TABLE benchmark_submissions ADD CONSTRAINT scope_type_chk_benchmark_submissions
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_benchmark_submissions_goal      ON benchmark_submissions(goal_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_submissions_status    ON benchmark_submissions(status);
CREATE INDEX IF NOT EXISTS idx_benchmark_submissions_submitter ON benchmark_submissions(submitted_by);

DROP TRIGGER IF EXISTS trg_benchmark_submissions_touch ON benchmark_submissions;
CREATE TRIGGER trg_benchmark_submissions_touch BEFORE UPDATE ON benchmark_submissions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================
-- 3. procedure_usage_events -- the one new signal: self-use vs independent
-- reuse, with an explicit outcome state so a self-report can never read as
-- verified. This sits ALONGSIDE evidence/execution_runs, not instead of
-- them -- evidence_id/execution_run_id below are optional links into that
-- existing machinery where a real run produced them.
-- ============================================================
CREATE TABLE IF NOT EXISTS procedure_usage_events (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    procedure_row_id      UUID NOT NULL REFERENCES procedures(id),
    procedure_id          UUID NOT NULL,     -- stable handle, copied for cross-version rollups (no FK --
                                              -- procedures.procedure_id is not unique by itself, matches
                                              -- the same no-FK convention execution_runs.procedure_id uses)
    goal_id               UUID REFERENCES problems(id) ON DELETE SET NULL,
    benchmark_id           UUID REFERENCES benchmarks(id) ON DELETE SET NULL,

    executed_by            TEXT NOT NULL,
    contributor_id          TEXT NOT NULL,    -- the procedure's owner/contributor AT THE TIME of this event
                                              -- (snapshotted, so attribution survives a later reassignment)
    is_self_use             BOOLEAN NOT NULL,

    outcome_state          TEXT NOT NULL DEFAULT 'unknown',
    verification_layer      INTEGER NOT NULL DEFAULT 0,   -- highest verification layer reached for THIS event:
                                                           -- 0 none, 1 deterministic, 2 LLM/heuristic,
                                                           -- 3 execution/benchmark, 4 independent reuse, 5 human review
    execution_run_id        UUID REFERENCES execution_runs(id) ON DELETE SET NULL,
    evidence_id             UUID REFERENCES evidence(id) ON DELETE SET NULL,
    context_key             TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}',

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_usage_events_outcome_chk') THEN
        ALTER TABLE procedure_usage_events ADD CONSTRAINT procedure_usage_events_outcome_chk
            CHECK (outcome_state IN ('unknown','claimed_success','verified_success','verified_failure'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_usage_events_layer_chk') THEN
        ALTER TABLE procedure_usage_events ADD CONSTRAINT procedure_usage_events_layer_chk
            CHECK (verification_layer BETWEEN 0 AND 5);
    END IF;
    -- The whole point of this table: a self-report ('claimed_success') can
    -- never satisfy 'verified_*' just by being asserted -- it must have
    -- reached at least Layer 3 (real execution/benchmark result).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='procedure_usage_events_verified_requires_layer_chk') THEN
        ALTER TABLE procedure_usage_events ADD CONSTRAINT procedure_usage_events_verified_requires_layer_chk
            CHECK (outcome_state NOT IN ('verified_success','verified_failure') OR verification_layer >= 3);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_procedure_usage_events_procedure   ON procedure_usage_events(procedure_row_id);
CREATE INDEX IF NOT EXISTS idx_procedure_usage_events_contributor ON procedure_usage_events(contributor_id);
CREATE INDEX IF NOT EXISTS idx_procedure_usage_events_goal        ON procedure_usage_events(goal_id) WHERE goal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_procedure_usage_events_outcome     ON procedure_usage_events(outcome_state);
CREATE INDEX IF NOT EXISTS idx_procedure_usage_events_executor_time ON procedure_usage_events(executed_by, created_at DESC);

-- ============================================================
-- 4. credit_ledger_events -- append-only. Balance is SUM(amount), never a
-- mutable column (§8 of the founder directive: "do not model Credits only
-- as a mutable balance"). Genuinely new -- no prior art to reuse.
-- ============================================================
CREATE TABLE IF NOT EXISTS credit_ledger_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    contributor_id           TEXT NOT NULL,
    amount                  NUMERIC NOT NULL,
    reason                  TEXT NOT NULL,

    goal_id                  UUID REFERENCES problems(id) ON DELETE SET NULL,
    procedure_row_id         UUID REFERENCES procedures(id) ON DELETE SET NULL,
    procedure_id             UUID,             -- stable handle snapshot, no FK (same reasoning as usage_events)
    usage_event_id           UUID REFERENCES procedure_usage_events(id) ON DELETE SET NULL,
    submission_id            UUID REFERENCES procedure_submissions(id) ON DELETE SET NULL,
    reversal_of_event_id     UUID REFERENCES credit_ledger_events(id) ON DELETE SET NULL,

    metadata                 JSONB NOT NULL DEFAULT '{}',
    created_by                TEXT NOT NULL,    -- system actor that recorded the event (service name or admin subject)
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='credit_ledger_events_reason_chk') THEN
        ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_reason_chk
            CHECK (reason IN ('new_procedure','improvement','verified_reuse','clawback','admin_adjustment'));
    END IF;
    -- Sign discipline: a reward is always positive, a clawback is always
    -- negative (it is a reversal row, never an edit -- see trigger below);
    -- admin_adjustment is the one deliberately-unconstrained escape hatch.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='credit_ledger_events_amount_sign_chk') THEN
        ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_amount_sign_chk
            CHECK (
                (reason IN ('new_procedure','improvement','verified_reuse') AND amount > 0)
                OR (reason = 'clawback' AND amount < 0)
                OR (reason = 'admin_adjustment')
            );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='credit_ledger_events_clawback_requires_reversal_chk') THEN
        ALTER TABLE credit_ledger_events ADD CONSTRAINT credit_ledger_events_clawback_requires_reversal_chk
            CHECK ((reason = 'clawback') = (reversal_of_event_id IS NOT NULL));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_contributor       ON credit_ledger_events(contributor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_contributor_reason ON credit_ledger_events(contributor_id, reason, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_usage_event       ON credit_ledger_events(usage_event_id) WHERE usage_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_submission        ON credit_ledger_events(submission_id) WHERE submission_id IS NOT NULL;

-- Append-only: no UPDATE, no DELETE, ever -- a correction is a new reversal
-- row (reason='clawback', reversal_of_event_id set), same "never mutate,
-- always append" discipline execution_plans/task_graphs/executions already
-- use, just expressed with this table's own trigger function so this
-- migration does not depend on another migration's function still being
-- named the same thing.
CREATE OR REPLACE FUNCTION sl_credit_ledger_events_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'credit_ledger_events is append-only: row % cannot be %, insert a reversal instead',
        OLD.id, lower(TG_OP);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_credit_ledger_events_immutable ON credit_ledger_events;
CREATE TRIGGER trg_credit_ledger_events_immutable
    BEFORE UPDATE OR DELETE ON credit_ledger_events
    FOR EACH ROW EXECUTE FUNCTION sl_credit_ledger_events_immutable();

-- Convenience read model ONLY -- reconstructed from the ledger on every
-- query, never a stored/mutable balance column (matches the founder
-- directive's §8 requirement directly).
CREATE OR REPLACE VIEW credit_balances AS
    SELECT contributor_id, COALESCE(SUM(amount), 0) AS balance, COUNT(*) AS event_count, MAX(created_at) AS last_event_at
    FROM credit_ledger_events
    GROUP BY contributor_id;

-- Next free migration number: 103.
