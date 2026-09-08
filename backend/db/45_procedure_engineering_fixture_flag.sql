-- Next free migration number: 46.
--
-- Ensure `procedures.is_engineering_fixture` exists so the retrieval
-- candidate filter (app/services/applicability.py::_CANDIDATE_BASE_WHERE)
-- can safely reference it on every deployment.
--
-- Context: a large slice of the live corpus is test / eval fixtures
-- (names like `pm-stale-lb-e2e-*`, `Task API E2e Proc *`, `v1-e2e-*`,
-- `Perf Dr *`, `gold-durable-*`). They are fully embedded and were
-- surfacing in product search -- e.g. "isolate and fix a flaky test"
-- returned four `Task API E2e Proc` rows above the relevance gate. Those
-- are practically irrelevant to every real query (plan Part 6, defect
-- #3). Product retrieval now excludes them.
--
-- A prior branch already added + backfilled this column (its migration is
-- not on this line of history); this ADD COLUMN IF NOT EXISTS is a no-op
-- where it is present and a plain default-false boolean where it is not.
-- No backfill here -- fresh-start rule; a row is a fixture only if a
-- fixture-writing test path marked it, which is exactly the semantics
-- product search wants.

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS is_engineering_fixture BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index over the rows product retrieval actually considers, so
-- the added predicate costs nothing at query time.
CREATE INDEX IF NOT EXISTS idx_procedures_non_fixture_live
    ON procedures (t_created)
    WHERE t_invalid IS NULL
      AND is_engineering_fixture = FALSE
      AND staleness <> 'stale'
      AND availability = 'active';
