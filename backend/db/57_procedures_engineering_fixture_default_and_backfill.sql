-- Migration 57: fixes a confirmed, real production bug -- the LIVE
-- `procedures.is_engineering_fixture` column default was `true`, while
-- the committed migration (db/45_procedure_engineering_fixture_flag.sql)
-- declares `DEFAULT FALSE`. Confirmed live via
-- `information_schema.columns.column_default`. Someone/something set
-- the live default to `true` outside the migration system at some
-- point; `ADD COLUMN IF NOT EXISTS` in migration 45 was a no-op against
-- the already-existing column and never corrected it.
--
-- Impact confirmed live: `capture_procedure()` never sets this column
-- explicitly (grep across app/services/procedures.py: zero references),
-- so EVERY procedure captured in this environment silently inherited
-- `true` -- excluded from `applicability.py::_CANDIDATE_BASE_WHERE`,
-- i.e. real production retrieval (`find_applicable_procedures`). 2772 of
-- 2775 live procedures carry the flag as of this migration -- almost
-- certainly why applicability.py's own comment says "2475 of 2478...
-- not just test junk": that comment is not describing a deliberate
-- design outcome, it is describing this exact drift.
--
-- FIX 1 -- the default, going forward: corrected to FALSE, matching the
-- committed migration's own stated intent. Every future capture (real
-- product usage AND test fixtures alike) now defaults to visible/real
-- unless a caller explicitly marks it a fixture -- "fresh-start rule; a
-- row is a fixture only if a fixture-writing test path marked it"
-- (migration 45's own words, now actually true).
--
-- FIX 2 -- backfill the CONFIRMED-real backlog: `created_by` in
-- ('structured_skill_ingestion_worker', 'structured_skill_ingestion_wave1')
-- is the real bulk skill-ingestion pipeline (1285+104+93+14 = 1496 rows
-- at audit time) -- genuine, reusable procedural content, not test
-- fixtures, confirmed by sampling real procedure names
-- (`python-azure-iot-edge-modules`, `azure-servicebus-dotnet`,
-- `gtm-positioning-strategy`, etc. -- ordinary skill-library content,
-- not test-shaped names). These are corrected to FALSE now.
--
-- DELIBERATELY NOT backfilled: every OTHER existing row (`procedure_
-- capture`'s 227+15+... ad-hoc-captured rows, every e2e-suite `created_by`
-- like `pm_e2e`/`gold_durable`/`perf_probe`/etc.) keeps its CURRENT
-- stored value. Some of those rows may be legitimate real ad-hoc
-- captures wrongly hidden too, but `created_by='procedure_capture'`
-- alone cannot distinguish a real ad-hoc capture from this session's own
-- test debris with confidence -- the safe, conservative direction to be
-- wrong in (per this same migration's own precedent in the coordination
-- gate) is to leave an ambiguous row excluded rather than risk exposing
-- real test/eval junk as if it were real product content. FIX 1 means
-- this backlog does not grow -- only this specific, already-confirmed
-- backlog needed correcting.

DO $$ BEGIN
    ALTER TABLE procedures ALTER COLUMN is_engineering_fixture SET DEFAULT FALSE;
END $$;

UPDATE procedures
SET is_engineering_fixture = FALSE
WHERE is_engineering_fixture = TRUE
  AND created_by IN ('structured_skill_ingestion_worker', 'structured_skill_ingestion_wave1');
