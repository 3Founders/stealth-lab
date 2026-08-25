-- Migration 27 (Lane CORE-A, Band 2.4): the failure-routing layer spec
-- §36 mandates -- every classified failure drives exactly one update.
--
-- Next free number: 26 was highest. Never edit an applied migration --
-- scripts/migrate.py checksums them and a mismatch is a hard error by
-- this repo's own design (same header discipline as 20/24/26).
--
-- Idempotent: safe to re-run. Fresh-start compliant: a new [H] table
-- only, no backfills -- failures recorded before this migration simply
-- have no routing rows and surface in
-- app/execution/failures.py::fetch_unrouted_failures() until classified,
-- which is honest, not silent.
--
-- ============================================================
-- THE §36 CONTRACT this table executes (spec v4 §36, verbatim mapping):
--
--   procedure_wrong      -> procedure_version_candidate   (new procedure
--                                                           version)
--   implementation_wrong -> capability_demotion            (on that
--                                                           implementation)
--   environment_changed  -> dependency_queue              (dependent claims
--                                                           re-checked)
--   input_abnormal       -> applicability_narrowing       (scope/exclusion
--                                                           update)
--   verification_wrong   -> plan_revision                 (verification-plan
--                                                           revision)
--   external_failure     -> no_op                         (no knowledge
--                                                           update)
--   false_reuse          -> applicability_narrowing       (the reuse gate
--                                                           let through
--                                                           something that
--                                                           failed -- the
--                                                           match was the
--                                                           bug)
--   (failure_class NULL) -> requires_review               ("nobody
--                                                           classified
--                                                           this" is
--                                                           itself a
--                                                           finding --
--                                                           db/24's own
--                                                           header note)
--
-- WHY A DURABLE LOG, not side effects at classification time: the
-- mandated updates belong to different owners (procedure revision ->
-- extraction, demotion -> capability stream computation, narrowing ->
-- applicability, plan revision -> execution plans). What CORE-A owes the
-- system is the QUEUE those owners consume from, with each decision
-- auditable against the evidence row that caused it -- the same reason
-- ingestion_jobs exists for trace work. Side-effect-at-classify would be
-- unauditable and unretryable; a routed log is both.
-- ============================================================

CREATE TABLE IF NOT EXISTS failure_routes (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The classified failure. One evidence row -> at most one route per
    -- routing generation; UNIQUE below makes re-running the classifier
    -- idempotent instead of duplicate-spawning.
    evidence_id    UUID NOT NULL REFERENCES evidence(id),

    -- Copied from the evidence row for queue-scannability (consumers
    -- filter by class without joining evidence). The boundary copies it;
    -- the NULL-pairing CHECK below keeps the copy honest at the engine.
    failure_class  TEXT,

    -- The §36 mandate, snake_case. Seven values, one per row above.
    route          TEXT NOT NULL,

    -- Typed per-route contents for the consuming owner (failed
    -- context_key for narrowing, independence group for demotion
    -- capping, etc). '{}' where the mandate needs nothing beyond the
    -- columns already on this row.
    payload        JSONB NOT NULL DEFAULT '{}',

    -- Birth discipline: which router generation made this decision --
    -- same "name@version" vocabulary as extracted_by (20) /
    -- extractor_version (23/24). Routing logic will change; WHEN it did
    -- must stay answerable forever.
    routed_by      TEXT NOT NULL,

    created_by     TEXT,
    visibility     visibility_level NOT NULL DEFAULT 'public',
    owner_id       TEXT,

    -- Bi-temporal trio, identical discipline to evidence (db/24):
    -- t_invalid = retraction tombstone, the ONE legal mutation (trigger
    -- below); everything else is append-only.
    t_valid        TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid      TIMESTAMPTZ,
    t_created      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (evidence_id, route)
);

-- ---- named, greppable CHECKs (db/24's defense-in-depth idiom: the
-- service layer owns error messaging, the engine refuses garbage from
-- direct-SQL paths that bypass it) ----

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failure_route_values_chk') THEN
        ALTER TABLE failure_routes ADD CONSTRAINT failure_route_values_chk
            CHECK (route IN (
                'procedure_version_candidate', 'capability_demotion',
                'dependency_queue', 'applicability_narrowing',
                'plan_revision', 'no_op', 'requires_review'));
    END IF;

    -- The §36 pairing rule as engine teeth: an UNCLASSIFIED failure
    -- (failure_class copied as NULL) may ONLY route to requires_review,
    -- and a CLASSIFIED failure may never route there -- "requires
    -- review" exists precisely because nobody named a cause. Same
    -- honest-distinction db/24's header drew between "classified
    -- external" and "nobody classified this".
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failure_route_class_pairing_chk') THEN
        ALTER TABLE failure_routes ADD CONSTRAINT failure_route_class_pairing_chk
            CHECK ((failure_class IS NULL) = (route = 'requires_review'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failure_route_class_values_chk') THEN
        ALTER TABLE failure_routes ADD CONSTRAINT failure_route_class_values_chk
            CHECK (failure_class IS NULL OR failure_class IN (
                'procedure_wrong', 'implementation_wrong', 'environment_changed',
                'input_abnormal', 'verification_wrong', 'external_failure',
                'false_reuse'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failure_route_routed_by_chk') THEN
        ALTER TABLE failure_routes ADD CONSTRAINT failure_route_routed_by_chk
            CHECK (length(trim(routed_by)) > 0);
    END IF;
END $$;

-- Queue readers scan by route over live rows; evidence-side reverse
-- traversal ("what happened to this failure?") is covered by the UNIQUE
-- index's leading column.
CREATE INDEX IF NOT EXISTS idx_failure_routes_route
    ON failure_routes(route) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_failure_routes_class
    ON failure_routes(failure_class) WHERE t_invalid IS NULL;

-- ============================================================
-- The freeze, same single-exception shape as evidence's (db/24): this
-- log is [H] -- append-only forever, corrections are new rows from a
-- newer routed_by generation, DELETE refused outright, and the only
-- legal mutation is the t_invalid retraction tombstone (a mis-route
-- retracted, never edited into a different story).
-- ============================================================
CREATE OR REPLACE FUNCTION sl_failure_routes_only_tombstone() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '%.% is append-only (Band 2.4, §36 routing log): DELETE rejected -- retract via the t_invalid tombstone or append a superseding row',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    IF OLD.t_invalid IS NOT NULL THEN
        RAISE EXCEPTION 'failure_route % is already retracted -- retraction is final, append a new row instead', OLD.id;
    END IF;
    -- Only t_invalid may change; enumerated explicitly (not a whole-row
    -- compare) so adding a column later fails THIS function loudly
    -- instead of silently widening what tombstones may edit.
    IF ROW(
            NEW.id, NEW.evidence_id, NEW.failure_class, NEW.route,
            NEW.payload, NEW.routed_by, NEW.created_by, NEW.visibility,
            NEW.owner_id, NEW.t_valid, NEW.t_created
        ) IS DISTINCT FROM
       ROW(
            OLD.id, OLD.evidence_id, OLD.failure_class, OLD.route,
            OLD.payload, OLD.routed_by, OLD.created_by, OLD.visibility,
            OLD.owner_id, OLD.t_valid, OLD.t_created
       ) THEN
        RAISE EXCEPTION 'failure_routes is append-only (Band 2.4, §36 routing log): only the t_invalid retraction tombstone may change -- append a superseding row instead';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_failure_routes_append_only') THEN
        CREATE TRIGGER tg_failure_routes_append_only
            BEFORE UPDATE OR DELETE ON failure_routes
            FOR EACH ROW EXECUTE FUNCTION sl_failure_routes_only_tombstone();
    END IF;
END $$;
