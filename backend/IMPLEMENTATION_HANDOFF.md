# Implementation handoff: procedure extraction is real, end to end, and gated behind approval

> **Update (Aug 20, commits after `d0edd21`).** The previous version of this
> doc closed the ingestion pipeline and wired retrieval into a caller. This
> update adds the piece `capture_procedure()`'s own docstring named as
> missing: nothing turned an episode of real work into a `procedures` row.
> That gap is closed — a full `procedure_extraction` package, migration 20,
> and a capstone e2e test proving the whole chain: real session →
> real observations → real derived preconditions → a real `procedures` row →
> correctly blocked by `applicability.py`'s **untouched** approval gate. Read
> "What's actually remaining" below; the τ³ item and the two LLM stubs are
> unchanged from last time.
>
> **Second update, same day, independent verification pass (not
> Chaitanya's own session):** re-read all 4 of his real commits, applied
> migration 20 and ran the full suite fresh (899 passed, 2 skipped, zero
> failures on Linux) rather than trusting the commit messages alone.
> Found and fixed one real doc-staleness bug (`02244bc` wired
> `solve_task`'s procedure retrieval but didn't update this doc to say
> so — item 3 below was still describing it as unwired) and fixed the
> one real, open item his session correctly diagnosed but deliberately
> left for the original author: a bounded retry-on-`PermissionError`
> around `trace_collector.py`'s two `os.replace()` calls, for the
> confirmed Windows Defender race. Expected new Windows count: 910
> passed, 1 skipped, 0 failures — not independently confirmed on Windows
> (this sandbox is Linux).

This is the CODE-level handoff. For the PLANNING-level one, see
`.scratch/memory-substrate/map.md` and the 18 resolved tickets in
`.scratch/memory-substrate/issues/` (both on `main`). This doc tells you
what's built, tested, and true right now — it doesn't repeat that reasoning.

## Environment setup

- **Real database**: Supabase Postgres, connection string via `$DATABASE_URL`
  or `--dsn` to `scripts/migrate.py`. Never hardcode it in a committed file.
- **Windows**: use `python`/`py`, not `python3` (App Execution Alias
  intercepts it) — including in hook `command` fields. `git am --abort`
  clears a stuck `.git/rebase-apply` safely if a patch application gets
  interrupted or re-run.
- **Local dev DB**: Postgres 17 + pgvector 0.8.0, confirmed working on
  Windows. No prebuilt Windows binary exists for PG17 — built from source:
  `nmake /f Makefile.win` under a VS Build Tools 2022 `vcvars64` shell,
  pointed at `PGROOT`, then manually copied `vector.dll`/`vector.control`/
  `vector--*.sql` into the PG `lib`/`share\extension` dirs (the Makefile's
  own `install` target needs admin rights Program Files requires; copy from
  an elevated prompt).
  ```
  createdb stealthlab_local
  psql -d stealthlab_local -c "CREATE EXTENSION vector;"
  python backend/scripts/migrate.py --dsn postgresql://<user>:<pass>@localhost:5432/stealthlab_local
  ```
  All 20 migrations apply cleanly in order on a fresh DB.
  e2e tests skip cleanly (not fail) without `DATABASE_URL` set.
- **pip**: `pip install -r requirements.txt --break-system-packages
  --ignore-installed PyJWT` (Debian-installed PyJWT conflicts otherwise).

## What's actually remaining — read this first

1. **`ResearchHTNAgent._mcts_pick`** — still a `NotImplementedError` stub,
   the only one left in `app/`. Needs a real LLM call. Not credential-blocked
   (General Compute creds are in `.env`), just not built.

2. **`extract_model_observation` has never touched a real LLM.** Same root
   cause — built, tested for shape, never actually called.

3. **`solve_task`'s memory_block rendering is now real (as of `02244bc`) —
   the HTN-side adapter is not.** `find_applicable_procedures()` has a real
   caller now: `server.py`'s `solve_task` retrieves a matched procedure
   (environment-derived scope, `require_verified=True` honored, not
   weakened) and renders its steps into `memory_block` for the **flat**
   `Agent` (`agent.run(...)`, not `HTNAgent`) — confirmed by reading the
   call site directly, not assumed from the commit message. What's still
   genuinely unwired: an equivalent adapter on the HTN side
   (`_seed_plan`/`_verify_precondition` consuming a matched procedure the
   way `ResearchHTNAgent._synthesize_method` already consumes a method-
   library match) doesn't exist yet. **Real, found while verifying this
   handoff, not by Chaitanya's own session**: this exact paragraph was
   stale in the version his own commit shipped — `02244bc` wired the
   solve_task half but didn't update this doc to say so. Fixed here;
   worth double-checking any handoff doc's "remaining" list against the
   actual latest commit's diff before trusting it, not just the doc.

4. **Collapsing the HTN class hierarchy's other 7 behavioral overrides** —
   unchanged from last handoff, deliberately deferred, needs an explicit
   call before starting (see prior version's reasoning, still accurate).

5. **τ³-bench micro-test — started, not finished.** Unchanged from last
   time: 698 `banking_knowledge` documents ingested with real
   `properties.source_doc_id`; procedure-seeding script, tau2 retriever
   adapter, and the actual task run are still unwritten.

## What's built and verified this session (not just written — run against a real database)

**A full `backend/app/services/procedure_extraction/` package**, plus two
new top-level modules it depends on. The core design decision, stated once
because it shapes everything: sorting `ExtractedProcedure`'s fields by
whether they genuinely require a model shows only TWO do
(`capability_statement`, step phrasing) — preconditions, scope, the step
skeleton, slots, and failure_conditions are all real derivations against
real state. Migration 18's own comment on `procedures.preconditions` says
exactly this: *"structured predicates derived from the source episode's
state_before projection... NOT hand-authored tags."* The **compounding
benefit**: a precondition derived from `project_state()` is, by
construction, already in the environment probe's vocabulary, so it cannot
fail groundedness — a pure-LLM extractor needs a validator to catch an
invented precondition; this design cannot produce one in the first place.

**`app/services/environment_probe.py`** (new, hard prerequisite, not a
later nicety) — deterministic, no LLM. Reads a real checkout
(`package.json`/lockfiles/`requirements.txt`/`pyproject.toml`) and asserts
real environment claims (`has_framework`, `has_test_runner`,
`package_manager`, `language`) via a claim-shaped write path that
deliberately does **not** go through `claims.py`'s `capture_claim()` — that
function drops any claim whose `task_ids` don't resolve to a live
`task_node`, and an environment fact is about a project, not a task.
Idempotent (re-probing an unchanged repo writes nothing new) and correctly
supersedes (via `claims.relate_claims()`) when the environment genuinely
changes, preserving history rather than overwriting. Owns
`PROBE_PREDICATE_VOCABULARY`, the single exported constant the extraction
validator checks imported/LLM-supplied preconditions against.

**`app/services/slot_binders.py`** (new, top-level because instantiation
will need it too) — a registry wrapping producers that already exist and
are already tested (`call_graph_reachable`, `import_deps`, `related_tests`,
`relevant_symbols`, `literal`). `best_binder_for()` picks whichever
producer's real output best covers the files an episode actually touched —
this is the part of a procedure that's genuinely learned from experience,
not asserted by a model.

**`procedure_extraction/evidence.py`** — `SessionEvidenceSource` (real
`observations`/`trace_events` reads, the exact join
`get_current_working_set()` already uses) and `AgentRunEvidenceSource`
(in-process, zero DB round trips). `goal_text`/`outcome` are deliberately
caller-supplied, not derived — `agent_traces.intent` exists in the schema
but has zero real writers, and this module doesn't invent a read against a
column nothing populates.

**`procedure_extraction/derive.py`** — the file that matters most, and the
one with no model in it. `derive_preconditions()`/`derive_scope()` call
`project_state(as_of=episode_start)` directly. **Real, verified limitation
found and documented, not assumed** (it took two wrong hypotheses to pin
down against a live database): `project_state()` requires BOTH
`t_valid <= as_of` AND `truth_state='IN'` to hold together. Once a claim is
superseded, the OLD value fails `truth_state` for *every* `as_of` including
one from before the supersession, but the NEW value fails `t_valid<=as_of`
for that same timestamp — so the predicate **disappears from the
projection entirely** once superseded, not the wrong value, an absent one.
Documented in `derive_preconditions()`'s own docstring and covered by a
dedicated regression test (`test_derive_preconditions_drops_a_predicate_
entirely_once_superseded`) so this doesn't need rediscovering.

**`procedure_extraction/schema.py`** — Pydantic contract mirroring
`procedures.py`'s real columns. `ProcedureStep` has **no `deps`/`requires`
field at all** — not a runtime check, a structural guarantee (ticket 05:
steps are planner-neutral).

**`procedure_extraction/strategies.py`** — `DeterministicExtractor` (the
honest, always-available baseline; migration 20 seeds it enabled) and
`GroundedHybridExtractor` (one small, bounded LLM call over a compressed,
run-length-encoded tool-call summary — never the raw episode — asking for
exactly `capability_statement` and step phrasing). Degradation is explicit:
no client, an API failure, a malformed response, or a step-count mismatch
between the LLM's `STEPS` list and the real derived skeleton all fall back
to the deterministic output, verified by dedicated tests, not asserted.

**`procedure_extraction/validators.py`** — five rules, all run, all
failures collected (unlike `applicability.py`'s deliberate short-circuit).
**V1 (precondition groundedness) is the highest-value rule in the
module** — `check_hard_constraints()` treats "no claim found" and
"precondition unsatisfied" as the same answer under CWA, so an ungrounded
precondition makes a procedure permanently, silently unmatchable. V4
(capability abstraction) mechanically rejects a `capability_statement`
containing any concrete file path/command drawn from the episode's own
evidence — this is what makes cross-domain retrieval possible at all.

**`procedure_extraction/registry.py`** — extractors as first-class,
versioned, reviewable objects, modeled directly on `07_agents.sql`'s
`agents` table (already this repo's pattern for config-driven, bi-temporal,
reviewable artifacts, including the deliberate `approved` ≠ `enabled`
split). Versions supersede, never edit in place. `extractor_stats()`
reports three signals in increasing order of value and decreasing order of
availability: validator pass rate (cheap, gameable) → human approval rate →
downstream success rate rolled up from `record_execution_outcome` (the one
that matters, necessarily delayed).

**`procedure_extraction/__init__.py`** — `extract_procedure()` (the public
API: collect evidence → select an extractor via the registry → run it →
validate → persist via `capture_procedure()` **unchanged**, plus one
follow-up `UPDATE` for the three migration-20 columns that function doesn't
yet know about, rather than widening its signature and touching its
existing callers) and `evaluate_extractor()` (a golden-set dry run,
diffing a candidate extractor's output against validators without
persisting — what a human reads before flipping `enabled`; without it,
"improvable over time" just means driftable).

**Migration 20**: `procedures.approval_status` (a THIRD orthogonal axis,
deliberately separate from `verification_state` — human approval is not
the same claim as ≥10 statistically-verified successes, and conflating them
would destroy ticket 13's semantics), `capability_statement`, `extracted_by`,
and the `procedure_extractors` table with a seeded, enabled
`deterministic_v1` baseline row.

### The capstone test — the actual end-to-end claim, proven

`tests/test_procedure_extraction_init_e2e.py`: a real session's real
`file_touched` observation → `extract_procedure()` → a real `procedures`
row with `extracted_by='deterministic_v1@1'`, `approval_status='proposed'`,
a real derived `capability_statement` → **`find_applicable_procedures()`,
imported and called exactly as-is, does not return it**, even with
`require_verified=False` (explicit-invocation mode). The approval gate,
proven against the actual consumer, not asserted.

## What's built — file map

- **Access control, collector/worker, hook wiring, migrations 17-19,
  applicability (ticket 12), execution engine (ticket 15), retrieval
  producers + orchestrator (ticket 14), ingestion_jobs, τ³ ingestion** —
  unchanged from prior handoffs; see git log for that detail.
- **New this session**: `app/services/environment_probe.py`,
  `app/services/slot_binders.py`, `app/services/procedure_extraction/`
  (evidence, schema, derive, strategies, validators, registry, `__init__`),
  `db/20_procedure_extraction.sql`.

## Testing what's built

```
export DATABASE_URL=postgresql://...
python -m pytest tests/ -q
```

**899 passed, 2 skipped, zero failures** on this update's own verification
(Linux, local Postgres 16 + pgvector) — the Windows-specific failures
below never manifest on Linux to begin with, and this update adds 2 more
real tests (for the retry fix itself). Chaitanya's own last count on
Windows was **908 passed, 1 skipped, 2 failures** (up from 883 before his
session's work — the delta is entirely new, real tests: 12 for
`environment_probe`, 26 unit + 4+5+7+3 e2e for `procedure_extraction`
across six new test files, zero of them mocked at the DB layer). With
this update's fix, the expected Windows count is **910 passed, 1 skipped,
0 failures** — expected, not independently confirmed on Windows (this
sandbox is Linux); worth a real Windows run to close the loop.

**The 2 failures Chaitanya's session found and confirmed unrelated, now
fixed** (verified this update, not left as a known gap): `test_trace_
collector.py::test_a1_append_cost_does_not_scale_with_existing_file_size`
and `test_trace_ingestion_e2e.py::test_a3_header_is_ensured_once_per_
distinct_trace_not_per_event` were failing on Windows with
`PermissionError: [WinError 5] Access is denied` inside
`trace_collector.py`'s `_write_meta()` at `os.replace(tmp_path,
meta_path)` — almost certainly Windows Defender's real-time scan racing
the rename of a freshly-written temp file. Fixed with a bounded
retry-on-`PermissionError` wrapper (`_replace_with_retry`, 5 attempts,
50ms gap) around both `os.replace()` call sites in that file — a
correct no-op cost on POSIX (a single successful call, same as before;
this retry path only ever fires on Windows in practice) and a real fix
for the confirmed Windows race, not just a wider `try/except` around
the symptom. 2 new tests confirm the retry logic itself: recovers from
a simulated transient failure, still raises after the bounded attempt
count (same "fail loudly rather than hang forever" discipline this
module already applies to its lock acquisition). Not verified against
a REAL Windows Defender race (this sandbox is Linux) — worth a
confirming run on the machine that originally hit it.

New test files worth reading if you're touching this area next:
`tests/test_environment_probe.py` / `_e2e.py`,
`tests/test_procedure_extraction.py` (DB-free: derive/validators/strategies
pure logic), `tests/test_procedure_extraction_e2e.py` (the
`project_state()` grounding + the superseded-predicate limitation),
`tests/test_procedure_extraction_strategies_e2e.py`,
`tests/test_procedure_extraction_registry_e2e.py`,
`tests/test_procedure_extraction_init_e2e.py` (the capstone).

## Real bugs / real findings this session

- `project_state()`'s supersession behavior drops a predicate entirely for
  any `as_of` before the newer claim's own `t_valid` — see derive.py above.
  Not a bug (it's `project_state()`'s own documented epistemic design), but
  a real, non-obvious consequence for extraction, now documented rather
  than silently discovered again by whoever builds instantiation next.
- `capture_claim()` cannot be reused for environment facts — it silently
  drops any claim without a resolving `task_id`, and an environment fact
  isn't about a task. `environment_probe.py` writes the same claim shape
  through a dedicated path instead of forcing a fake task_node.

## Working style that kept producing real results — still true

Verify against real code before asserting anything — including your own
test's assumptions: the `project_state()` supersession behavior above took
two wrong hypotheses and two real test failures to pin down correctly, and
both wrong guesses are worth knowing were wrong, not just the right answer.
Real database, not mocks, for anything touching Postgres. Honest scope
notes in every module's own docstring, not buried elsewhere.

## Suggested skills

Still backend Python/SQL/pytest work — no specialized skill applies.
