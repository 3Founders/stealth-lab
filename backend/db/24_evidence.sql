-- Migration 24 (Band 1.9a): Evidence as a first-class typed table with
-- independence groups, and procedure verification statistics as views
-- over evidence rather than counters on procedure rows.
--
-- Why this lands now (one-way-door logic, ROADMAP.md 1.9 / former Band 2.2):
-- every lifecycle transition, capability score, and belief computation
-- downstream stands on provenance -- "what outcome stream supports this?" --
-- and M1's founding loop needs a real join key from outcomes to objects.
-- Today the only record of a procedure's outcome history is
-- procedures.verification_stats, a JSONB counter blob that cannot answer
-- "which runs, in which contexts, from which independent sources?" and
-- grows more expensive to retrofit the more of history it summarizes.
-- Counter blobs also silently violate the fresh-start birth disciplines:
-- they mutate outside ChangeSets and cannot distinguish independent
-- evidence from re-measurement of the same run.
--
-- Shape authority: spec v4 §11 (the nine evidence types, strength
-- {score, method}, independence_group, failure_class), §9b (direction and
-- independence_group are named aggregation inputs), §36 (failure_class =
-- the six causes + false_reuse, recorded on the evidence row the failed
-- execution produced -- Band 0 item 0.5's structural home), schema.md
-- "Evidence [H]". Where the repo has a native convention (scope pair,
-- ticket-13 context_key, visibility pair, bi-temporal tombstones) the repo
-- convention wins, exactly as migrations 18/20/21/22/23 each did.
--
-- Fresh-start ruling: applied to an empty/rebuilt database; NO backfills,
-- NO transition machinery. Trial-era verification_stats counters stay
-- untouched until their writers are rewired onto these views (see the
-- sequencing note at the bottom of this file).
--
-- Idempotent: safe to re-run, same idiom as every other migration here.
-- Next free number: 23 was highest before this file.

-- ============================================================
-- 0. evidence_kind -- the nine types spec v4 §11 enumerates. A native
-- enum, not TEXT+CHECK: the vocabulary is closed by the spec (unlike
-- observations.observation_type, deliberately free TEXT in migration 14
-- because ITS vocabulary wasn't fixed), and pg_enum gives test_schema_
-- drift.py's live-database introspection something real to diff against
-- -- the exact mechanism that caught the embedding_joint ghost column.
-- ============================================================
DO $$ BEGIN
    CREATE TYPE evidence_kind AS ENUM (
        'execution_result',   -- a recorded run produced the outcome
        'observation',
        'experiment',
        'benchmark',
        'document',
        'human_review',
        'external_source',
        'artifact',
        'reproduction'        -- someone else re-ran it and got the same result
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================
-- 1. evidence [H] -- append-only with supersede-by-tombstone.
--
-- Every row answers: what (target), from where (source/content),
-- how strong (score + named method), how independent (group), for or
-- against (direction), and -- for outcome-bearing types -- what
-- terminal status with WHICH recorded success criteria (invariant #13).
--
-- Independence semantics (§9b / §11): rows sharing an
-- independence_group NEVER count as independent corroboration of each
-- other; NULL means the row is its own group. Aggregators count
-- DISTINCT COALESCE(independence_group, id::text) -- the views below
-- encode exactly that expression once, so Python never re-derives it.
-- ============================================================
CREATE TABLE IF NOT EXISTS evidence (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    evidence_type       evidence_kind NOT NULL,

    -- What this row is evidence FOR. Polymorphic by design: the same
    -- typed row serves claims (belief aggregation, §9b), procedures
    -- (verification stats + the verified-requires-evidence gate) and
    -- implementations (capability demotion, invariant #12's subject).
    -- Deliberately no single FK across heterogeneous targets -- same
    -- call traces.parent_trace_id made (01_ontology.sql). Views JOIN
    -- properly, so a dangling reference can never be COUNTED, merely
    -- stored; the boundary validator refuses unversioned procedure
    -- targets and the composite join below makes them uncountable.
    target_type         TEXT NOT NULL,
    target_id           UUID NOT NULL,
    target_version      INTEGER CHECK (target_version >= 1),

    -- §9b aggregation input; the §2 connection map renders it as the
    -- supports/contradicts edge, but the edge store here is CHECK-
    -- constrained to node tables, so the direction rides on the row.
    direction           TEXT NOT NULL,

    -- strength: {score, method} (§11). Both NOT NULL: strength is a
    -- NAMED aggregation input, and an unstated method invites every
    -- consumer to treat a bare float as signal (the same reasoning
    -- that kept observations confidence-free in migration 14 -- there
    -- the answer was "don't store"; here the contract REQUIRES the
    -- pair, so the answer is "store it, but never method-less").
    strength_score      REAL NOT NULL,
    strength_method     TEXT NOT NULL,

    -- Same-group evidence never counts as independent (§11). NULL =
    -- self-grouped. Ticket 13's "distinct contexts" rides alongside as
    -- context_key -- the caller's own notion of context, exactly the
    -- semantics record_execution_outcome already documents.
    independence_group  TEXT,
    context_key         TEXT,

    -- Forward-compatible handles: → Source and → Artifact have no
    -- tables yet, hence no FK -- the identical call migration 23 made
    -- for starting_state_id/implementation_id. Recordable now,
    -- joinable when their writers exist.
    source_id           UUID,
    content_ref         UUID,

    -- Outcome discipline (invariant #13). Terminal status reuses
    -- executions.outcome's exact closed vocabulary rather than
    -- inventing a fourth spelling of success/failure. success_criteria
    -- is the explicit predicate/criteria metrics a SUCCESS must stand
    -- on: '{}' means nobody recorded why this counted as success --
    -- i.e. a bare model-asserted self-report -- and the engine rejects
    -- exactly that below. This column pair is what makes an outcome
    -- distinguishable from a model saying so.
    outcome_status      TEXT,
    success_criteria    JSONB NOT NULL DEFAULT '{}',

    -- §36 classification (Band 0 item 0.5's structural home): NULL
    -- until a failure; seven values, mapped snake_case from §36's
    -- causes + false_reuse. Unclassified failures route to
    -- requires_review at the routing layer -- storage stays honest
    -- about the difference between "classified external" and
    -- "nobody classified this".
    failure_class       TEXT,

    -- Birth discipline (V0 gate): derived rows stamp "name@version"
    -- here -- same vocabulary as procedures.extracted_by (migration 20)
    -- and execution_plans.extractor_version (migration 23). Nullable
    -- COLUMN / required-at-boundary follows migration 21's stated
    -- split: the gate owns the error message, the column exists so
    -- direct-SQL writers have a place to put the truth.
    extractor_version   TEXT,

    created_by          TEXT,
    -- Ticket 09 pair rule: every new table carries BOTH columns
    -- (access.py::visibility_predicate() breaks otherwise). Invariant
    -- #9 (private evidence cannot automatically become public) reads
    -- this column on every future read path.
    visibility          visibility_level NOT NULL DEFAULT 'public',
    owner_id            TEXT,
    scope_type          TEXT,
    scope_entity_id     TEXT,

    -- Bi-temporal trio, procedure_extractors-style: t_valid/t_invalid =
    -- when true in the world (t_invalid = retraction tombstone), and
    -- t_created = when the system learned it. Retraction is the ONE
    -- permitted UPDATE shape (trigger below); everything else is
    -- supersede-by-append (invariant #19).
    t_valid             TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid           TIMESTAMPTZ,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- named, greppable CHECKs (migration 22's defense-in-depth idiom:
-- the service layer owns error messaging, the engine refuses garbage
-- from direct-SQL paths that bypass it) ----

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_target_type_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_target_type_chk
            CHECK (target_type IN ('claim', 'procedure', 'implementation'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_direction_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_direction_chk
            CHECK (direction IN ('supports', 'contradicts'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_strength_range_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_strength_range_chk
            CHECK (strength_score >= 0 AND strength_score <= 1);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_strength_method_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_strength_method_chk
            CHECK (length(trim(strength_method)) > 0);
    END IF;

    -- A BLANK independence_group would be worse than none: every
    -- blank-grouped row would silently share one group and stop
    -- corroborating each other. NULL means self-grouped; a named group
    -- must actually be named.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_independence_group_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_independence_group_chk
            CHECK (independence_group IS NULL OR length(trim(independence_group)) > 0);
    END IF;

    -- Outcome-bearing types must say what happened: an execution_result
    -- or reproduction WITHOUT a terminal status is not an outcome, it
    -- is a rumor about one.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_outcome_status_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_outcome_status_chk
            CHECK (outcome_status IS NULL OR outcome_status IN (
                'success', 'failure', 'needs_rework'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_outcome_bearing_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_outcome_bearing_chk
            CHECK (NOT evidence_type IN ('execution_result', 'reproduction')
                   OR outcome_status IS NOT NULL);
    END IF;

    -- Invariant #13, engine teeth: a SUCCESS with empty criteria is a
    -- bare model-asserted self-report -- unwritable, hence unable to
    -- enter any statistic computed over this table.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_success_criteria_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_success_criteria_chk
            CHECK (outcome_status IS DISTINCT FROM 'success'
                   OR success_criteria <> '{}'::jsonb);
    END IF;

    -- §36: failure_class is null until a failure, never decoration on
    -- a success row.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_failure_class_values_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_failure_class_values_chk
            CHECK (failure_class IS NULL OR failure_class IN (
                'procedure_wrong', 'implementation_wrong', 'environment_changed',
                'input_abnormal', 'verification_wrong', 'external_failure',
                'false_reuse'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_failure_class_target_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_failure_class_target_chk
            CHECK (failure_class IS NULL OR outcome_status = 'failure');
    END IF;

    -- Exact-version discipline (invariant #2's spirit, applied to
    -- evidence targeting procedures): an unversioned procedure target
    -- is not writable. Claims are row-id-addressed (knowledge_nodes has
    -- no version column), so the requirement is procedure-specific.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_proc_version_chk') THEN
        ALTER TABLE evidence ADD CONSTRAINT evidence_proc_version_chk
            CHECK (target_type <> 'procedure' OR target_version IS NOT NULL);
    END IF;

    -- Scope CHECK, migration 22/23's greppable one-block-per-table shape.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_evidence') THEN
        ALTER TABLE evidence ADD CONSTRAINT scope_type_chk_evidence
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

-- The provenance join key M1 needs: "every live piece of evidence for
-- this exact object", plus reverse traversal and group scans.
CREATE INDEX IF NOT EXISTS idx_evidence_target
    ON evidence(target_type, target_id) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_independence_group
    ON evidence(independence_group) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_scope
    ON evidence(scope_type, scope_entity_id);

-- ============================================================
-- 2. procedure_evidence_stats -- THE replacement for the JSONB counter
-- blob (ROADMAP 1.9: "procedure statistics become views over evidence
-- rather than counters on procedure rows"). One row per procedure
-- VERSION row (procedures.id), grouped by the logical pair
-- (procedure_id, version) consumers already use.
--
-- Columns mirror verification_stats' vocabulary 1:1 (attempts /
-- successes / failures / distinct_contexts) so the rewiring of readers
-- is mechanical, then add what counters could never carry:
-- independent_* counts via DISTINCT COALESCE(independence_group,
-- id::text) -- same-source repetition stops inflating trust at THIS
-- layer, before any aggregator even runs -- and failure classification
-- counts feeding §36's cause-routed updates.
--
-- Invariant #12 note (capability is evidence-based, not model-brand-
-- based): this SELECT reads ONLY type/direction/outcome/group/context/
-- target columns. Producer identity (created_by, owner_id) is stored
-- provenance and appears nowhere in any statistic -- identical outcome
-- streams tagged with different model brands yield byte-identical
-- rows from this view, which the proving tests pin statically.
-- ============================================================
CREATE VIEW IF NOT EXISTS procedure_evidence_stats AS
SELECT
    p.id                        AS procedure_row_id,
    p.procedure_id,
    p.version                   AS procedure_version,
    count(*)                    AS attempts,
    count(*) FILTER (WHERE e.outcome_status = 'success') AS successes,
    count(*) FILTER (WHERE e.outcome_status = 'failure') AS failures,
    count(DISTINCT e.context_key)                        AS distinct_contexts,
    count(DISTINCT COALESCE(e.independence_group, e.id::text)) AS independent_attempts,
    count(DISTINCT COALESCE(e.independence_group, e.id::text))
        FILTER (WHERE e.outcome_status = 'success')      AS independent_successes,
    count(DISTINCT COALESCE(e.independence_group, e.id::text))
        FILTER (WHERE e.direction = 'supports'
                AND e.evidence_type IN ('execution_result', 'reproduction'))
                                                     AS independent_supporting_required,
    count(*) FILTER (WHERE e.failure_class IS NOT NULL) AS classified_failures,
    count(*) FILTER (WHERE e.failure_class IS NULL
                      AND e.outcome_status = 'failure') AS unclassified_failures
FROM evidence e
JOIN procedures p
    ON e.target_type = 'procedure'
   AND e.target_id = p.id
   AND e.target_version = p.version
WHERE e.t_invalid IS NULL
  AND e.direction = 'supports'
  AND e.evidence_type IN ('execution_result', 'reproduction')
GROUP BY p.id, p.procedure_id, p.version;

-- ============================================================
-- 3. The freeze, with exactly one exception. Evidence is [H]:
-- append-only forever (invariant #19 names it directly), corrections
-- and contradictions are NEW rows. The single legal UPDATE is the
-- retraction tombstone (t_valid -> t_invalid, nothing else) -- the
-- same supersede mechanism 12_trace_ingestion_pipeline.sql already
-- established, and the mechanism spec §45's "Evidence E retracted ->
-- recomputation" flow needs. A retracted row stays queryable as
-- testimony (why did belief drop on Aug 3?) but drops out of every
-- live statistic via the t_invalid filters above. DELETE is refused
-- outright: deleting the evidence is exactly the silent-modification
-- failure mode the append-only invariants exist to prevent.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_evidence_only_tombstone() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '%.% is append-only (Band 1.9a, invariant #19): DELETE rejected -- retract via the t_invalid tombstone or append a superseding row',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    IF OLD.t_invalid IS NOT NULL THEN
        RAISE EXCEPTION 'evidence % is already retracted -- retraction is final, append a new row instead', OLD.id;
    END IF;
    -- Only t_invalid may change; every other column must be untouched.
    -- Enumerated explicitly (not a whole-row compare) so adding a
    -- column later fails THIS function loudly instead of silently
    -- widening what tombstones may edit.
    IF ROW(
            NEW.id, NEW.evidence_type, NEW.target_type, NEW.target_id,
            NEW.target_version, NEW.direction, NEW.strength_score,
            NEW.strength_method, NEW.independence_group, NEW.context_key,
            NEW.source_id, NEW.content_ref, NEW.outcome_status,
            NEW.success_criteria, NEW.failure_class, NEW.extractor_version,
            NEW.created_by, NEW.visibility, NEW.owner_id,
            NEW.scope_type, NEW.scope_entity_id, NEW.t_valid, NEW.t_created
        ) IS DISTINCT FROM
       ROW(
            OLD.id, OLD.evidence_type, OLD.target_type, OLD.target_id,
            OLD.target_version, OLD.direction, OLD.strength_score,
            OLD.strength_method, OLD.independence_group, OLD.context_key,
            OLD.source_id, OLD.content_ref, OLD.outcome_status,
            OLD.success_criteria, OLD.failure_class, OLD.extractor_version,
            OLD.created_by, OLD.visibility, OLD.owner_id,
            OLD.scope_type, OLD.scope_entity_id, OLD.t_valid, OLD.t_created
       ) THEN
        RAISE EXCEPTION 'evidence is append-only (Band 1.9a, invariant #19): only the t_invalid retraction tombstone may change -- append a superseding row instead';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_evidence_append_only') THEN
        CREATE TRIGGER tg_evidence_append_only
            BEFORE UPDATE OR DELETE ON evidence
            FOR EACH ROW EXECUTE FUNCTION sl_evidence_only_tombstone();
    END IF;
END $$;

-- ============================================================
-- 4. Sequencing note -- the verified-requires-evidence ENGINE trigger
-- is deliberately NOT in this file. Appendix C #3's full form blocks
-- the candidate->verified transition when
-- procedure_evidence_stats returns zero required-type rows; enabling
-- it today would strand the current counter-based promotion path in
-- services/procedures.py (which writes no evidence rows) behind a
-- gate nothing can satisfy -- breaking working flows with no shim to
-- bridge them, which the fresh-start ruling forbids twice over. The
-- contract-level gate ships NOW as
-- app/execution/evidence.py::assert_verified_requires_evidence(),
-- proven offline; the engine trigger lands in the SAME change that
-- wires evidence writes into the lifecycle path (board cross-lane
-- request). Gate and writer arrive together, or not at all.
-- ============================================================
