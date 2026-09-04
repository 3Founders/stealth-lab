-- Explicit, reviewable classification backfill for migration 39's new
-- fail-closed column. Every row in `procedures` already defaulted to
-- is_engineering_fixture=true on ALTER (migration 39). This migration does
-- two things, both idempotent (safe to re-run):
--
-- 1. Makes the "these are engineering/test/demo data" classification
--    EXPLICIT rather than merely implicit-via-default, for the specific
--    created_by actor tags directly confirmed this session (by live-DB
--    query, .scratch/final_agent_experiment/retrieval-contamination-review.md)
--    to be e2e-test/demo/smoke-test scaffolding. This is documentation as
--    much as data repair: a future migration that changes column 39's
--    default would not silently reclassify these rows, because they are
--    now pinned `true` explicitly, not by default alone.
-- 2. Flips the 3 procedures genuinely admitted by the Better-Ways admission
--    phase (better-ways-admission @ f921e2c385dfab511233adc148a5b7a2b8082932,
--    created_by='better_ways_admission_2026_09_04') to `false` -- real,
--    source-derived, externally-admitted knowledge, not engineering
--    scaffolding, and must remain retrievable after migration 39's default
--    would otherwise have hidden them too.
--
-- NOT a deletion, NOT a hide-from-the-database action: every row named
-- here remains fully present, fully queryable directly, fully inspectable
-- by engineering/test code -- only its eligibility for the ordinary
-- user-facing retrieval predicate (applicability.py::_CANDIDATE_BASE_WHERE)
-- changes.
--
-- Known e2e/test/demo actor tags (confirmed via live-DB
-- `SELECT provenance, created_by, COUNT(*) FROM procedures GROUP BY ...`,
-- this session) -- explicit true, matching (and documenting) the default:
UPDATE procedures SET is_engineering_fixture = true
WHERE created_by IN (
    'pm_e2e', 'gold_durable', 'perf_probe', 'dr_e2e', 'dres_e2e',
    'corpus_wave', 'pm_stale_lb_e2e', 'durable-chaos-e2e', 'step-tnid-e2e',
    'dg_e2e', 'plan-impl-bind-e2e', 'v1_browser_e2e', 'plan-pin-e2e',
    'mcp_idres_e2e', 'task-api-e2e-actor', 'seed_demo_procedures',
    'procedure_capture'
) OR created_by LIKE 'gold_ingestion_sources_%'
  OR created_by LIKE '%_e2e' OR created_by LIKE '%-e2e';

-- Real, externally-admitted knowledge -- explicit false, the only rows in
-- the corpus this migration promotes out of the fail-closed default:
UPDATE procedures SET is_engineering_fixture = false
WHERE created_by = 'better_ways_admission_2026_09_04';
