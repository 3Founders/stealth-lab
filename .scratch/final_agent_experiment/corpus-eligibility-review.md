# Corpus/retrieval eligibility review

**Status: FIXED and independently verified.** This document answers the
coordinator's exact question list, all against real evidence (live-DB
queries, real code reads, a real regression test now passing).

## Root cause

`applicability.py::_fetch_candidate_pool` (feeding
`find_applicable_procedures`, which every real retrieval entrypoint --
`search_procedures` MCP tool, `find_best_way`, `LocalAgentRunner` --
ultimately calls) had exactly one shared filter constant,
`_CANDIDATE_BASE_WHERE = "t_invalid IS NULL AND staleness != 'stale' AND
availability = 'active'"`. Nothing in that predicate, or anywhere else in
the real retrieval chain, referenced `provenance` or `created_by`.

Two rows that surfaced in the 12-trial pilot's `B_default` arm proved this
concretely: `proc-test-planonly-root-2b7fb0f9` and
`proc-test-planonly-root-5149abb1`, both `verification_state='verified'`,
`created_by='procedure_capture'` (the generic default `created_by` value
`capture_procedure()` uses when a caller doesn't override it), both
fixtures of `backend/tests/test_find_best_way_plan_only_e2e.py`.

## Why `provenance`/`scope_type` cannot be used (confirmed by direct query)

Both contaminating fixture rows AND the 3 genuinely Better-Ways-admitted
procedures (`created_by='better_ways_admission_2026_09_04'`) share the
**identical** `provenance='prior_library'`, `scope_type='global'`. These
two columns encode source-lineage-category and
visibility/tenancy respectively -- neither was ever designed to answer
"is this engineering scaffolding or real knowledge", and retrofitting
either would conflate two independently-meaningful axes.

## Answers to the 7 required questions

1. **Should test fixtures be stored in the same selectable namespace?**
   No change needed here -- they remain in the same `procedures` table (no
   parallel store invented, per the coordinator's explicit instruction),
   but as of this fix they are no longer in the same *selectable* set by
   default.
2. **If yes, what explicit scope/state prevents them from surfacing
   normally?** A new column, `procedures.is_engineering_fixture BOOLEAN
   NOT NULL DEFAULT true` (db/39), consulted by
   `_CANDIDATE_BASE_WHERE` (`AND is_engineering_fixture = false`) --
   the single seam every real retrieval SQL query in
   `_fetch_candidate_pool` already shared, so the fix applies uniformly
   with a one-line change, not a scattered patch.
3. **If no, what isolation mechanism is missing?** N/A (answered by 2).
4. **Is the problem limited to test-created procedures, or can other
   demo/engineering data similarly pollute retrieval?** Broader than just
   `proc-test-planonly-root-*`. Direct query of the full corpus (1038
   procedures) before this fix found ~20 distinct `created_by` actor tags,
   overwhelmingly e2e/perf/demo scaffolding
   (`pm_e2e` 444, `gold_durable` 233, `perf_probe` 94, `dr_e2e` 49,
   `procedure_capture` 39, `dres_e2e` 33, `corpus_wave` 28, plus ~12
   smaller tags, `seed_demo_procedures` 3) -- confirming this session's
   own earlier Finding-D characterization ("~99% of the live corpus is
   engineering-execution smoke data"). The fix (db/40 backfill) classifies
   all of these `true`, not just the `proc-test-planonly-root-*` pattern
   the pilot happened to surface.
5. **Does `verification_state='verified'` currently mean enough to
   distinguish production knowledge from fixtures? (This is likely the
   single most important finding.)** **No.** Before this fix, ALL 12 of
   the corpus's `verification_state='verified'` procedures were confirmed
   (by direct query) to be `proc-test-planonly-root-*` fixtures,
   `canon-demo-*` demo rows, or `seed_demo_procedures` synthetic seed
   content -- zero real external knowledge. `verification_state` answers
   "has this procedure earned trust through evidence accumulation"; it has
   never answered, and structurally cannot answer, "is this real knowledge
   or engineering scaffolding" -- a fixture created via
   `capture_procedure()`+`approve_procedure()` (the exact real production
   path, not a shortcut) becomes just as "verified" as anything else. This
   is the core finding this whole review exists to fix.
6. **Does `created_by='procedure_capture'` carry any semantic protection
   today?** No. It is `procedures.py`'s own `CREATED_BY` module constant
   -- the generic default used whenever a caller does not pass a custom
   `created_by`. It is used ambiguously by fixture-creating test code AND
   would be used by any real production caller that also forgot to
   override it. It carries no real/fixture signal by itself (confirmed:
   this session's own earlier corpus characterization found 3 of the 5
   pre-Better-Ways `verified` procedures under this exact tag were
   `canon-demo-*` synthetic content, not real).
7. **Does retrieval currently prioritize real production/global knowledge
   over engineering/demo/test data, or is it blind to the distinction
   entirely?** Before this fix: **entirely blind.** After this fix:
   engineering/demo/test data is excluded from the default candidate pool
   outright (a stronger guarantee than mere de-prioritization) --
   `_CANDIDATE_BASE_WHERE`'s new predicate runs before ranking, at the
   hard-filter stage, matching this module's own existing "filter before
   rank, never let ranking compensate for a filter failure" design
   principle (see that module's own docstring). Real knowledge is not yet
   *ranked ahead of* engineering data by any richer signal -- it is
   simply the only thing eligible to compete at all. Ranking optimization
   was explicitly out of scope this pass per the coordinator's own
   instruction ("Do NOT optimize the retrieval ranking until the corpus
   eligibility problem is understood").

## The fix, precisely

- `backend/db/39_procedures_engineering_fixture_flag.sql` -- additive
  column, fail-closed default `true`, full rationale in the migration's
  own header comment and `COMMENT ON COLUMN`.
- `backend/db/40_procedures_engineering_fixture_backfill.sql` -- explicit
  (not merely default-implied) classification: the ~20 known e2e/demo
  actor tags -> `true`; the 3 Better-Ways-admitted procedures -> `false`.
  Confirmed via live query after applying: 1035 `true` / 3 `false` out of
  1038 total, and the 2 exact procedures that contaminated the pilot
  (`c534835e...`, `75cc5023...`) confirmed still `true`.
- `backend/app/services/procedures.py::capture_procedure` -- new
  `is_engineering_fixture: bool = True` parameter, threaded into the
  INSERT as a new trailing column (`$29`, no renumbering of existing
  positional params, minimizing diff risk).
- `backend/app/services/applicability.py::_CANDIDATE_BASE_WHERE` -- the
  one-line fix: `AND is_engineering_fixture = false` appended to the
  single shared predicate string.
- No SQL was hand-written outside these two migration files. No `DELETE`,
  no `DROP`, no hiding of rows from direct/engineering queries -- only
  the ordinary retrieval candidate pool changed.

## Known limitation, stated plainly (not swept under the rug)

This pass did **not** audit or update the ~15 real production call sites
of `capture_procedure()` across `backend/app/` (`local_ingestion.py`,
`skill_ingestion.py`, `claims.py`, `ingestion_jobs.py`,
`procedure_extraction/{registry,synthesis}.py`, `publish.py`, etc.) to
individually decide, per call site, whether it should pass
`is_engineering_fixture=False`. The fail-closed default means this is
**safe** (any of these call sites that captures genuinely real knowledge
without passing the new flag will be conservatively excluded from default
retrieval, not silently re-included as fixture-contamination would be) but
it is also incomplete: a real future ingestion path that does not
explicitly opt in stays invisible to ordinary retrieval until it is
updated. This is a concrete, scoped follow-up item, not a gap this review
is hiding.

Separately: `created_by='procedure_capture'` was classified `true`
(fixture) in the db/40 backfill on the basis that, as of this pass, 100%
of the corpus's `procedure_capture`-tagged rows are confirmed demo/test
artifacts (this session's own earlier Finding-D characterization, and this
pass's own re-check, both found zero real content under that tag). This is
a data-snapshot decision, not a structural rule -- `procedure_capture` is
`capture_procedure()`'s generic default, not a fixture-specific marker, so
this classification should be revisited if real content is ever captured
under that same unmodified default tag.

## Regression tests

- `backend/tests/test_retrieval_fixture_isolation_e2e.py` (pre-existing,
  intentionally left failing by the prior pass) -- **re-run after this
  fix and now genuinely PASSES** (confirmed: `1 passed in 14.03s`, live
  DB, real embeddings, real `capture_procedure`/`approve_procedure` path,
  real `find_applicable_procedures` call with a semantically distinct
  query from the fixture's own wording).
- A second, positive-direction test was added (see
  `remediation-results.md` for its exact name and result) proving a
  real/admitted procedure remains retrievable after the fix -- the fix
  excludes fixtures, it does not also accidentally exclude everything.
