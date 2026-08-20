# Implementation handoff: memory-substrate build, step 5/6 done

> **Update (Aug 19-20, commits `c6d1d04` through `e603854`).** Everything
> the last handoff called "remaining" is now done: production gaps 1-3, the
> A2-A7 code-review fixes, migration 17, a real fix for A1 (the collector
> was previously O(n) per event AND could not import on Windows at all),
> real Claude Code hook wiring, and the full step-5/6 build — tickets 05,
> 13, 12, 15, and 14. This doc replaces the previous version wholesale
> rather than layering another update box on top of it; the old one is
> git history (`git log -p -- backend/IMPLEMENTATION_HANDOFF.md`), not
> repeated here.

This is the CODE-level handoff. For the PLANNING-level one (why every
decision was made), see `.scratch/memory-substrate/map.md` and the 18
resolved tickets in `.scratch/memory-substrate/issues/` — both now on
`main`, not a separate branch. This doc doesn't repeat that reasoning; it
tells you what's actually built, tested, and true right now.

## Read this first: environment setup, since it trips people up

- **Real database**: a Supabase Postgres instance is in use for this
  project. The connection string lives in **`$DATABASE_URL`** (or pass
  `--dsn` to `scripts/migrate.py` / test runs) — never hardcode it in a
  file that gets committed. If you're picking this up fresh, ask Anuj for
  the current connection string; the one used earlier in this session had
  its password rotated after being accidentally pasted into a chat, so
  don't reuse anything you find in old logs.
- **Windows note**: on the repo owner's machine, `python3` is not on PATH
  (Windows' App Execution Alias stub intercepts it) — use `python` instead,
  or `py`. `git am` can leave a stuck `.git/rebase-apply` directory if a
  patch application is interrupted or re-run; `git am --abort` clears it
  safely before retrying (confirmed this session — no work was lost when
  this happened).
- **Local dev database** (for running tests without touching the real
  Supabase instance): Postgres 16 + pgvector, matching what every session
  this project has used:
  ```
  createdb stealthlab_local
  psql -d stealthlab_local -c "CREATE EXTENSION vector;"
  python backend/scripts/migrate.py --dsn postgresql://<user>:<pass>@localhost:5432/stealthlab_local
  ```
  Then run tests with `DATABASE_URL` pointed at that local DB (see
  "Testing what's built" below) — e2e tests skip cleanly (not fail) if
  `DATABASE_URL` isn't set at all, so a sessionless environment can still
  run the rest of the suite.
- **pip installs**: use `--break-system-packages`. One real conflict hit
  this session: installing `requirements.txt` fresh failed on `PyJWT`
  (Debian-installed, not pip-managed) — fixed with
  `pip install -r requirements.txt --break-system-packages --ignore-installed PyJWT`.

## Real implementation order (fully revised)

1. ~~Migration ledger + trace ingestion pipeline (tickets 06, 16, 17)~~ — **done**
2. ~~Episode assembly prototype (ticket 11)~~ — **done**, `experiments/episode_assembly/`
3. ~~Observations + claims + state model (tickets 03, 04, 10)~~ — **done**
4. ~~Production gaps 1-3 (access control, race condition, load testing)~~ — **done**
5. ~~A2-A7 code-review fixes (Windows redaction, field-name ambiguity, owner_id on trace tables, malformed-line handling, worker perf, worker/collector trim coordination)~~ — **done**
6. ~~Migration 17 (project_id + episode columns) + real A1 fix (O(1) append, cross-platform locking)~~ — **done**
7. ~~Real Claude Code hook wiring~~ — **done, but see "The one thing genuinely unverified" below**
8. ~~Ticket 05 — procedure representation~~ — **done**
9. ~~Ticket 13 — procedure lifecycle (promotion, circuit breaker, quarantine, utility retirement)~~ — **done**
10. ~~Ticket 12 — applicability function~~ — **done**
11. ~~Ticket 15 — HTN relocation~~ — **done, scheduler-strategy restructuring explicitly deferred, see below**
12. ~~Ticket 14 — local-first retrieval hierarchy~~ — **done**

**Nothing from the memory-substrate map's step 5/6 remains undone**, except
the one deliberately-deferred piece inside ticket 15 (below) and gap 4
(also below).

## What's actually built — file map

Real, working code from this session, by area:

- **Access control** (gap 1): `app/services/state.py`, `app/services/claims.py`,
  `app/services/observations.py` — `project_state()`/`capture_claim()`/
  `persist_observation()`/`promote_observation_to_claim()` all now take a
  real `scope: AccessScope` and enforce it via `visibility_predicate()`,
  where before `owner_id`/`visibility` were written but never checked on
  read (or, for observations, never written at all).
- **Collector/worker** (gap 2, A1-A7): `app/services/trace_collector.py`,
  `app/services/trace_worker.py`, `app/services/trace_redaction.py` — real
  O(1) append with cross-platform (`fcntl`/`msvcrt`) locking, a
  `worker_seen_count` high-water mark so compaction never discards data
  the worker hasn't confirmed reading, quarantine for malformed lines,
  Windows path redaction, dual `tool_output`/`tool_response` field
  handling.
- **Hook wiring**: `backend/scripts/hook_wrapper.py`,
  `backend/scripts/example_hook_settings.json` — the real script Claude
  Code would invoke via `.claude/settings.json`. Fail-safe by
  construction (always exits 0). **Not yet registered or run against a
  real Claude Code process** — see below.
- **Migrations**: `backend/db/17_episode_project_columns.sql` (project_id,
  episode hierarchy columns, `agent_traces.collector_drop_count`),
  `backend/db/18_procedures.sql` (the `procedures` table itself),
  `backend/db/19_procedures_embedding.sql` (embedding column, added
  separately since 18 was already applied to the real DB by the time it
  was needed).
- **Procedures** (tickets 05, 13): `app/services/procedures.py` — capture,
  and the full lifecycle (`record_execution_outcome`,
  `check_quarantine_and_disable`, `compute_utility`,
  `retire_negative_utility_procedures`), with ticket 13's exact numbers
  (≥10 successes/0 failures/≥3 contexts for `verified`, 5-failure circuit
  breaker, 14-day forced quarantine disable).
- **Applicability** (ticket 12): `app/services/applicability.py` — the
  non-compensatory hard-constraint filter cascade, fail-closed
  preconditions via `project_state()`, the cold-start gate
  (`should_disable_procedure_retrieval`).
- **Execution engine** (ticket 15): `backend/app/execution/htn_agent.py`
  is now the **real** implementation (relocated from
  `experiments/swebench_pro/htn_agent.py`, which is now a 29-line
  re-export shim — don't edit that file, it just re-exports everything).
  `RunContext` (in that same file) carries all per-run state; `HTNConfig`/
  `StructuralLimits`/`DistributionalBudgets` are the new hyperparameter
  config objects, additive only.
- **Retrieval** (ticket 14): `app/services/retrieval.py` (existing
  `HybridRetriever`, now with `fuse_rrf()` extracted as a pure, tested
  function) and the new `app/services/local_retrieval.py`
  (`StructuralContext`, `get_current_working_set()`,
  `get_recent_commit_files()`, `assemble_context()`,
  `retrieve_local_first()`).

## Real, honest gaps — not resolved, don't let them get lost

1. **The one thing genuinely unverified: hook wiring against a real Claude
   Code process.** `scripts/hook_wrapper.py` and
   `scripts/example_hook_settings.json` are built directly against the
   real, cited hook schema doc
   (`.scratch/memory-substrate/research/claude-code-hook-schema.md`), and
   every field the doc didn't confirm (`tool_call_id`'s real name,
   whether `PostToolUse` has a `success` field, `tool_output` vs
   `tool_response`) is handled defensively, not guessed. But this has
   **never actually fired from a real Claude Code hook invocation** — no
   such process was available anywhere this session ran. Real, concrete
   next step: copy the relevant block from `example_hook_settings.json`
   into a real `.claude/settings.local.json`, do a few real tool calls in
   a live Claude Code session, and check whether
   `.claude/traces/<session_id>.jsonl` actually appears with sane content.
2. **Gap 4 — the model-based observation extractor
   (`extract_model_observation` in `app/services/observations.py`) has
   never been run against a real LLM.** It's coded against General
   Compute's OpenAI-compatible interface (`GENERAL_COMPUTE_API_KEY` in
   `.env`); Groq (already your primary provider) would also work via the
   same OpenAI-compatible client shape. Blocked purely on credentials +
   network access from whatever sandbox runs the test, not on anything
   architectural.
3. **Ticket 15's scheduler-strategy restructuring, deferred by explicit
   agreement, not an oversight.** The HTNAgent → AugmentedHTNAgent →
   ResearchHTNAgent inheritance chain — ticket 15's own text calls this
   "the largest structural flaw" — is still three classes, not one engine
   with a pluggable scheduler strategy. Left alone because `_schedule`
   (the concurrent scheduler) has several real, hard-won regression fixes
   documented in its own comments (starvation bugs found and fixed on
   real SWE-bench runs), and restructuring it deserves its own dedicated,
   carefully-tested pass rather than being blended into the relocation
   diff. What WAS done in `htn_agent.py` (relocation, `RunContext`
   extraction, hyperparameter config) is real, tested, and independent of
   this deferred piece — it doesn't need to happen first, just hasn't
   happened yet.
4. **`local_retrieval.py`'s structural/temporal tiers have two real
   producers and five honest gaps.** `open_files`
   (`get_current_working_set`) and `recent_commit_files`
   (`get_recent_commit_files`) are wired to real data (your own
   `observations.py` file_touched/commit_made observations).
   `relevant_symbols`, `import_deps`, `related_tests`,
   `call_graph_ranked_names`, `recent_failure_files` are real, typed
   fields on `StructuralContext` that nothing populates yet —
   `call_graph.py`'s reachability data is filesystem-based (needs a real
   repo checkout) and has zero DB coupling by design, so wiring it into a
   live retrieval call is real, separate work for whichever caller
   actually has that checkout (most naturally the HTN engine, once it's
   actually invoking things — which routes back to gap 3 above).
5. **No `.claude/settings.local.json` exists anywhere in this repo.**
   `example_hook_settings.json` is documentation, deliberately not
   auto-installed — silently modifying live Claude Code config would be a
   bad surprise. Someone has to deliberately copy it in.

## Testing what's built

Every module above has real tests, run against a real database (not
mocked) wherever the module touches Postgres. From `backend/`:

```
# Full suite, e2e tests included (needs DATABASE_URL pointed at a real
# Postgres+pgvector instance -- local or Supabase, either works):
export DATABASE_URL=postgresql://...
python -m pytest tests/ -q

# Without DATABASE_URL, e2e tests skip cleanly (not fail) -- still useful
# for pure-logic modules (fuse_rrf, assemble_context, the HTN engine's
# fake-client tests, etc.):
python -m pytest tests/ -q
```

Current state (verified against the exact commit this doc describes,
`e603854`, before pushing): **774 passed, 2 skipped, zero failures.**

Notable test files from this session, if you want to read one to
understand a specific piece before touching it: `test_procedures_e2e.py`
(ticket 13's exact thresholds, including the circuit breaker probe/reset
logic), `test_applicability_e2e.py` (the non-compensatory guarantee,
deliberately proven both ways — passes correctly, and correctly fails
against a broken version), `test_htn_agent.py`'s `TestRunContextExtraction`
class (the concurrency fix, including a test that reproduces the original
staleness bug against the pre-fix code), `test_retrieval_rrf_properties.py`
(RRF's four invariants, manually property-tested since `hypothesis` isn't
a project dependency), `test_local_retrieval_e2e.py` (the union guarantee,
proven with a direct before/after check).

Migration idempotency: `python backend/scripts/migrate.py --dsn ...` is
always safe to re-run — already-applied migrations are checksummed and
skipped, tampering with an applied file is a hard error (by design, do
not edit an applied migration; add a new one).

## Working style that got real results this session, worth keeping

- **Verify against real code, not prose** — every fix in this session was
  confirmed against the pre-fix code first (to prove the bug was real),
  then against the fix (to prove it worked), not just written and
  assumed. This caught real things: a dropped line causing a silently-
  swallowed `NameError` in the HTN scheduler, a double-JSON-encoding bug
  in `procedures.py`, a circuit-breaker close path that referenced a
  counter never actually incremented, two test-design confounds in the
  retrieval e2e tests (a fake enum value, and a small-test-DB artifact
  that made a "distant" embedding still rank as its own entrypoint).
- **Real database, not mocks**, for anything touching Postgres — every
  e2e test file this session follows the same pattern: real local
  Postgres, real inserts, real cleanup, skip (don't fail) without
  `DATABASE_URL`.
- **Honest scope notes over silent gaps.** Every module built this
  session states explicitly, in its own docstring, what it does NOT do
  and why — not buried in a comment three files away.
- **git discipline**: this session used `git format-patch`/`git am` to
  hand off each real, tested unit of work as a reviewable diff rather
  than one giant unreviewable dump. Worth continuing — small, real,
  independently-tested commits, each verified against `origin/main`
  after landing (not just assumed to have landed).

## Suggested skills

Still backend Python/SQL/pytest work — no specialized skill (docx/pdf/
pptx/xlsx) applies. If a future session needs a real, external-facing
document (an investor one-pager, an architecture diagram), `docx` or
`frontend-design` would be the relevant skill at that point, same as the
last handoff noted — still not needed yet.
