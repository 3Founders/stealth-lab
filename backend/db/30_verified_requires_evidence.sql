-- Migration 30 (Lane CORE-A, WAVE-3 adoption sweep): the
-- verified-requires-evidence ENGINE trigger, promised by db/24's
-- sequencing note since Band 1.9a.
--
-- WHY NOW (and not in 24): a gate nothing can satisfy must not ship
-- ahead of its writer. Until this wave, the only writer of evidence
-- rows was direct SQL and the boundary module
-- (app/execution/evidence.py::assert_verified_requires_evidence) --
-- while the lifecycle's promotion path
-- (services/procedures.py::record_execution_outcome) wrote no evidence
-- at all. Enabling this trigger then would have stranded every
-- counter-based promotion behind an unsatisfiable check, breaking
-- working flows with no shim -- exactly what the fresh-start ruling
-- forbids. This wave wires the writer: record_execution_outcome now
-- inserts one execution_result evidence row per recorded outcome,
-- inside the SAME transaction as the counters UPDATE, so by the time
-- the candidate->verified transition can fire, supporting evidence for
-- that version provably exists. Gate and writer arrive together.
--
-- MECHANISM: BEFORE UPDATE on procedures. The check arms ONLY on the
-- transition INTO 'verified' (OLD IS DISTINCT FROM NEW) --
-- already-verified rows keep passing unrelated updates (stats bumps,
-- quarantine flips) without re-proving themselves, and retirement /
-- staleness transitions are untouched. The gate counts INDEPENDENT
-- supporting rows of the required types via db/24's
-- procedure_evidence_stats view
-- (independent_supporting_required >= 1): two rows sharing an
-- independence_group are one piece of evidence wearing two ids, and
-- promotion refuses to be flattered by repetition. Because the view is
-- queried inside the writer's own transaction, the same-statement-
-- transaction evidence INSERT is visible to it -- writer and gate are
-- atomic: either both land or neither does.
--
-- Contract-level twin: app/execution/evidence.py::
-- assert_verified_requires_evidence() (Appendix C #3), unchanged --
-- this trigger is the engine half of the same invariant (#3).
--
-- REQUIRES migrations 23 + 24 (the view aggregates procedures joined
-- with evidence). Not idempotent-safe on databases missing those:
-- like every migration here, it belongs to the ordered chain
-- 01->30, whose canonical application point remains board queue
-- item 2's disposable-DB run.
--
-- Idempotent: safe to re-run. Fresh-start compliant: additive DDL
-- only, no backfills, no data writes.
-- Next free number: 29 was highest before this file.

CREATE OR REPLACE FUNCTION sl_verified_requires_evidence() RETURNS trigger AS $$
BEGIN
    IF NEW.verification_state = 'verified'
       AND OLD.verification_state IS DISTINCT FROM 'verified' THEN
        IF NOT EXISTS (
            SELECT 1 FROM procedure_evidence_stats s
            WHERE s.procedure_row_id = NEW.id
              AND s.independent_supporting_required >= 1
        ) THEN
            RAISE EXCEPTION 'cannot verify procedure % (version %): zero independent supporting rows of required types in procedure_evidence_stats -- every verified procedure has evidence (invariant #3)',
                NEW.id, NEW.version;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'tg_procedures_verified_requires_evidence'
    ) THEN
        CREATE TRIGGER tg_procedures_verified_requires_evidence
            BEFORE UPDATE ON procedures
            FOR EACH ROW EXECUTE FUNCTION sl_verified_requires_evidence();
    END IF;
END $$;
