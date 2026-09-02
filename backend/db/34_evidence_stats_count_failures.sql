-- Migration 34: procedure_evidence_stats must count recorded failures.
--
-- Next free number: 34 (33_implementation_registry.sql is highest).
-- Never edit an applied migration -- scripts/migrate.py checksums them
-- and a mismatch is a hard error by this repo's own design. This file
-- redefines one view via CREATE OR REPLACE VIEW; nothing else.
--
-- Idempotent: safe to re-run (CREATE OR REPLACE), same idiom as
-- 24_evidence.sql's own note about PG having no CREATE VIEW IF NOT EXISTS.
--
-- ============================================================
-- The bug (real, present-day, independent of scale):
--
-- 24_evidence.sql's procedure_evidence_stats has `failures` and
-- `unclassified_failures` columns:
--     count(*) FILTER (WHERE e.outcome_status = 'failure') AS failures
-- ...but ALSO a tail predicate `AND e.direction = 'supports'`. Every
-- caller that records a failure goes through
-- app/execution/evidence.py::outcome_to_evidence(), whose honest default
-- assigns a FAILURE outcome `direction = 'contradicts'` (and a success
-- `direction = 'supports'`). So a real recorded failure never passes the
-- tail predicate, never reaches the view, and the `failures` /
-- `unclassified_failures` columns are structurally pinned at 0 -- a
-- procedure could fail every time it was tried and `attempts` /
-- `failures` would not move.
--
-- The same latent filter lived in the Python mirrors of this view
-- (app/services/procedure_graph_api.py::_capability_estimate,
-- app/services/capabilities.py::_capability_estimate_from_evidence) and
-- in the raw evidence-stream queries in applicability.py /
-- procedure_extraction/failure_handlers.py / task_api.py -- all corrected
-- in the same change as this migration.
--
-- The fix:
--
-- Drop the tail `AND e.direction = 'supports'`. A live row of an
-- outcome-bearing type (execution_result / reproduction) with a terminal
-- outcome_status IS an attempt, whichever way its supports/contradicts
-- arrow points; the failure count is exactly what pulls a success rate
-- (or a Wilson lower bound) down.
--
-- What deliberately does NOT change: `independent_supporting_required`
-- keeps its OWN inner `FILTER (WHERE e.direction = 'supports' AND
-- e.evidence_type IN (...))`. "How much INDEPENDENT SUPPORTING evidence
-- exists" is a genuinely different question from "how many attempts /
-- failures were there", and it is the supporting-evidence count that
-- app/execution/evidence.py::assert_verified_requires_evidence reads for
-- invariant #3 (the candidate->verified gate, migration 30). That gate's
-- behaviour is byte-identical before and after this migration.
-- ============================================================

CREATE OR REPLACE VIEW procedure_evidence_stats AS
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
  AND e.evidence_type IN ('execution_result', 'reproduction')
GROUP BY p.id, p.procedure_id, p.version;
