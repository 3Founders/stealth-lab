# Retrieval Contamination Review

Investigation of Finding 2 from the 12-trial pilot: every completed
Stealth-arm (`B_default`) trial's retrieval matched a test-fixture
procedure, never real knowledge. Per the coordinator's explicit priority,
this is treated as the highest-priority investigation of this remediation
pass. **No retrieval ranking was changed. No fixture data was deleted.**

## The two matched procedures, in full

| field | `c534835e-3592-4788-a9f2-8f26d0e0cf2f` | `75cc5023-e531-40ef-8dc1-4c6bf20f74f1` |
|---|---|---|
| row id (`procedures.id`) | `01a068db-1646-79a6-8058-231d6806dfac` | `01a06864-32c5-7f3b-bb49-c976b0054d93` |
| name / goal | `proc-test-planonly-root-2b7fb0f9` | `proc-test-planonly-root-5149abb1` |
| `verification_state` | `verified` | `verified` |
| `approval_status` / `approved_by` | `approved` / `tester` | `approved` / `tester` |
| `provenance` | `system_pending_review` | `system_pending_review` |
| `scope_type` / `scope_entity_id` | `global` / `NULL` | `global` / `NULL` |
| `visibility` | `public` | `public` |
| `created_by` | `procedure_capture` | `procedure_capture` |
| `verification_stats.attempts/successes` | 14 / 10 | 12 / 10 |
| `verification_stats.context_keys_seen` | `["ctx-0","ctx-1","ctx-2",...]` | `["ctx-0","ctx-1","ctx-2",...]` |
| task_nodes | none directly FK'd -- procedure `steps` is a self-contained JSON array (`[{"goal":"explore repo","order":0}, {"goal":"run the shared lint pass (...)","order":1,"subprocedure_ref":{...}}]`); this procedure family does not use the `task_nodes` table at all |

**Source, confirmed exactly:** `backend/tests/test_find_best_way_plan_only_e2e.py`, helper
`_make_verified_approved()` (lines 66-78). It calls the real
`capture_procedure(provenance="system_pending_review", scope_type="global")`,
then `record_execution_outcome()` `MIN_SUCCESSES_FOR_VERIFIED` times with
synthetic `context_key=f"ctx-{i % ...}"` values, then
`approve_procedure(approved_by="tester")`. The test's OWN comment (lines
87-96) already documents that this is a known, previously-unaddressed leak:
*"A stale, still-verified leftover root with the SAME goal text would then
rank as the top match instead of this run's fresh one"* -- the author
mitigated only per-run name collisions (a UUID suffix), not exposure to
outside retrieval.

## Why retrieval considered them eligible -- the real predicate, read directly

`app/services/applicability.py`:
- `_CANDIDATE_BASE_WHERE = "t_invalid IS NULL AND staleness != 'stale' AND availability = 'active'"` (line 391-393)
- `require_verified` gate (line 288, 304): `procedure["verification_state"] != "verified"` and `procedure.get("approval_status") != "approved"`
- The full verified-only WHERE actually used server-side (line 745): `"WHERE verification_state = 'verified' AND approval_status = 'approved' ..."`

**None of these reference `provenance`, `created_by`, `scope_type`, or any
other field that could distinguish test/engineering/demo data from real
production knowledge.** `unified_retrieval.py` (what `LocalAgentRunner`
actually calls) layers ranking/specificity logic (`_specificity_rank` by
`scope_type`, broad-to-narrow) on top of whatever `find_applicable_procedures`
already returned -- it does not add a provenance filter either; `scope_type`
only affects ranking ORDER among already-eligible candidates, and both
fixtures use `scope_type="global"`, the SAME value real production knowledge
would use.

## Answers to the coordinator's 7 questions

**1. Should test fixtures share the selectable namespace?**
No. A procedure a test script creates to prove a code path exists is
fundamentally different in kind from a procedure representing real,
externally-validated operational knowledge -- conflating them in one
selectable pool is exactly what produced this finding. They should not be
selectable by `require_verified=True` production retrieval.

**2. If yes, what explicit scope/state should exclude them from normal surfacing? (answering as: since the answer to Q1 is no, what SHOULD gate them)**
Nothing in the current schema does this today (see Q3), but the two axes
that already exist and could carry this distinction cleanly are
`provenance` and `created_by` -- see Q5/Q6 for why neither currently does.

**3. If no, what isolation mechanism is missing?**
Two real gaps, confirmed by reading the actual enums in
`app/services/v0_gate.py`:
- `SCOPE_TYPES = ("global", "organization", "team", "project", "repository",
  "branch", "user", "session", "task", "entity")` -- ten values, broad to
  narrow, but **none named for test/engineering/fixture data**. The
  narrower values that exist (`session`, `task`, `entity`) are never used by
  any e2e test in this codebase; every test that captures a procedure uses
  `scope_type="global"`, identical to what real production knowledge would
  use.
- `PROVENANCE_VALUES = ("company_ingested", "company_debate",
  "public_generated", "prior_library", "system_pending_review")` -- five
  values, **none named for test/engineering-only data either**.
  `system_pending_review` is the closest generic bucket and is used
  interchangeably by real system-generated content pending review AND by
  every test fixture in the codebase -- by design indistinguishable.
  There is no `test_fixture` (or equivalent) provenance value, and nothing
  in `find_applicable_procedures`/`_CANDIDATE_BASE_WHERE`/`unified_retrieval.py`
  would honor one even if it existed -- the isolation mechanism is missing
  at BOTH the vocabulary layer (no value to mark it) and the enforcement
  layer (no filter would read it).

**4. Is this limited to test-created procedures, or could other demo/engineering data pollute retrieval the same way?**
Not limited to test data -- confirmed by a live, exact query. **All 12 of
the 12 procedures currently meeting `verification_state='verified' AND
approval_status='approved'` in this shared corpus (the entire pool
`require_verified=True` retrieval can ever surface) are engineering
smoke/demo/test data. Zero are real externally-ingested knowledge:**

| name pattern | count | `created_by` | `provenance` | actual origin |
|---|---|---|---|---|
| `proc-test-planonly-root-*` | 5 | `procedure_capture` | `system_pending_review` | `test_find_best_way_plan_only_e2e.py` fixtures |
| `canon-demo-*` | 5 | `procedure_capture` | `system_pending_review` | engineering smoke data (this session's own earlier Finding-D characterization) |
| `Cut a patch release` / `Roll a Postgres schema migration safely` | 2 | `seed_demo_procedures` | `prior_library` | synthetic demo seed content (same earlier characterization) |

This is not a narrow, one-test bug -- it is the CURRENT STATE of the entire
verified-and-retrievable corpus. Every category of engineering/demo data
this session has previously characterized as "not real external knowledge"
(Better-Ways admission phase, the pre-ingestion deterministic baseline) is
equally eligible for normal user-facing retrieval today, for the identical
structural reason.

**5. Does `verification_state='verified'` currently mean enough to distinguish production knowledge from fixtures?**
**No -- this is the single most important finding in this review.**
`verification_state='verified'` means only "this row's `verification_stats`
crossed `MIN_SUCCESSES_FOR_VERIFIED` successes across
`MIN_DISTINCT_CONTEXTS_FOR_VERIFIED` distinct `context_key` values, and a
human or system actor subsequently called `approve_procedure`." Nothing
about that bar requires the successes to come from real production usage --
a test script generating synthetic `context_key=f"ctx-{i}"` values and
approving its own fixture via `approved_by="tester"` clears the identical
bar, in the identical way, indistinguishably. As of this review, 100% of
what `verification_state='verified'` currently marks in this corpus is
test/demo data (see Q4's table) -- the label is not currently a reliable
signal of production-worthiness at all.

**6. Does `created_by='procedure_capture'` carry any semantic protection today?**
No. `created_by` is populated with the NAME OF THE FUNCTION that inserted
the row (`capture_procedure`), not an actor/caller identity -- confirmed by
reading its usage sites: every call to `capture_procedure`, from a real
production ingestion path or from an e2e test fixture, writes the identical
literal string. It is uniform across all 12 verified rows in the corpus
(10 of them) plus the vast majority of the corpus's ~1038 total procedures.
It answers "which code function wrote this row," never "was this a test."

**7. Does retrieval currently prioritize real production/global knowledge over engineering/demo/test data, or is it blind to the distinction entirely?**
Blind entirely, confirmed by reading every filtering/ranking predicate in
the real call chain (`_CANDIDATE_BASE_WHERE`, the `require_verified` gate,
`unified_retrieval.py`'s `_specificity_rank`) -- none references
`provenance`, `created_by`, or any equivalent signal. `scope_type` affects
ranking order among already-eligible candidates only, and both fixtures use
`scope_type="global"` -- the same value real knowledge would use, so even
specificity-based ranking cannot distinguish them.

## Regression test added

`backend/tests/test_retrieval_fixture_isolation_e2e.py` --
`test_test_fixture_procedure_does_not_surface_as_normal_knowledge_in_user_facing_retrieval`.
Creates a fresh procedure using the EXACT same fixture-creation pattern as
`test_find_best_way_plan_only_e2e.py` (mechanically earns
`verification_state='verified'`/`approval_status='approved'` the identical
way), then asserts a genuinely unrelated user-facing query's
`find_applicable_procedures(require_verified=True)` call does NOT return
it.

**Result: FAILS, as expected, and is committed failing.** Live run: the
freshly-created fixture row (`01a06a9b-9c43-75f3-9661-72fc91e122e7`) was
returned alongside the two SAME real contaminating procedures the pilot
found (`01a068db-...`, `01a06864-...`) -- an exact, live reproduction of the
pilot's finding, not a synthetic guess. Test fixture cleaned up after the
run (confirmed by direct query: row no longer exists). This test is
intentionally NOT weakened to pass -- it pins the real, current gap.

## What a real fix would need (not implemented this pass -- investigation and a regression test only, per the coordinator's explicit scope)

1. A new `provenance` (or a dedicated boolean/enum field) value meaning
   "test/engineering-only, never eligible for normal retrieval regardless
   of verification_state" -- added to `PROVENANCE_VALUES` in
   `app/services/v0_gate.py`.
2. Every e2e test that currently calls `capture_procedure(provenance=
   "system_pending_review", ...)` purely to exercise a code path (not to
   simulate real ingested knowledge) would need to be updated to use the
   new value instead.
3. `_CANDIDATE_BASE_WHERE` (and any sibling predicate in
   `unified_retrieval.py`) would need one added clause excluding that new
   value, applied identically to every retrieval caller (not per-caller
   opt-in, matching this codebase's own `access.py` "one predicate builder"
   discipline already established for the visibility/tenancy axes).
4. A migration to retroactively tag the 12 currently-contaminating rows
   (or a decision to leave them and only prevent new contamination going
   forward -- a real product/data-governance decision, not an engineering
   one, and explicitly out of scope for this remediation pass).

None of the above was implemented this pass. Per the coordinator's
explicit instruction, this pass is investigation plus a regression test
only -- no retrieval ranking changes, no fixture deletion, no schema
change.
