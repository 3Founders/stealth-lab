# Implementation handoff: step 5/6 done, ticket 15 fully restructured, local_retrieval.py complete

> **Update (Aug 20, commits `44ba4e3` through `68d905b`).** Everything the
> previous version of this doc called "remaining" is done: `local_retrieval.py`
> now has all 7 of ticket 14's structural/temporal/rank signals wired to real
> producers (not just 1), ticket 15's deferred scheduler-strategy restructuring
> is complete, and two of `ResearchHTNAgent`'s three remaining stub methods
> are real implementations. This doc replaces the previous version wholesale.
> Real remaining work is now genuinely small and precisely scoped — see
> "What's actually remaining" below; read that section first if you're
> starting fresh.

This is the CODE-level handoff. For the PLANNING-level one, see
`.scratch/memory-substrate/map.md` and the 18 resolved tickets in
`.scratch/memory-substrate/issues/` (both on `main`). This doc tells you
what's built, tested, and true right now — it doesn't repeat that reasoning.

## Environment setup (unchanged from last time, repeated because it trips people up)

- **Real database**: Supabase Postgres, connection string via `$DATABASE_URL`
  or `--dsn` to `scripts/migrate.py`. Never hardcode it in a committed file.
- **Windows**: use `python`/`py`, not `python3` (App Execution Alias
  intercepts it). `git am --abort` clears a stuck `.git/rebase-apply` safely
  if a patch application gets interrupted or re-run.
- **Local dev DB**: Postgres 16 + pgvector.
  ```
  createdb stealthlab_local
  psql -d stealthlab_local -c "CREATE EXTENSION vector;"
  python backend/scripts/migrate.py --dsn postgresql://<user>:<pass>@localhost:5432/stealthlab_local
  ```
  e2e tests skip cleanly (not fail) without `DATABASE_URL` set.
- **pip**: `pip install -r requirements.txt --break-system-packages
  --ignore-installed PyJWT` (Debian-installed PyJWT conflicts otherwise).

## What's actually remaining — read this first

Four real items, none blocking each other, ranked by how contained they are:

1. **`ResearchHTNAgent._mcts_pick`** (item 4 of that class's own docstring) —
   still a `NotImplementedError` stub. Needs a real LLM call to generate 2-3
   decomposition candidates before the UCB1/scoring mechanics around it can
   be tested meaningfully — same category of blocker as gap 4 below (nothing
   in a sandbox environment can make that call). `_ast_edit` (item 2) and
   `_method_score` (item 5) are DONE as of `68d905b` — only this one stub
   remains on that class.

2. **Wiring `retrieve_local_first()` into a real caller.** Every piece
   (`app/services/local_retrieval.py`, all 7 producers) is built and tested
   standalone, but nothing in the actual execution path calls it yet — there
   is no real function today that takes a live session + repo checkout,
   assembles a `StructuralContext` from `get_current_working_set()` /
   `get_relevant_symbols()` / etc., and feeds it through. This is genuinely
   buildable now (all the pieces exist), just not done — the natural next
   step, not blocked on anything external.

3. **Collapsing the HTN class hierarchy's other 7 behavioral overrides**
   (`_verify_precondition`, `_verify_postcondition`, `_system_prompt_extra`,
   `_replan_evidence`, `_build_context`, `_basename_index`,
   `_tools_for`/`_persona`) into one class. The SCHEDULER half of ticket 15's
   "one engine, pluggable strategy" ask is done (`SchedulerStrategy`,
   `SequentialScheduler`, `ConcurrentBatchScheduler` — see below); this is
   the other, larger half, deliberately left alone both times it came up.
   Real risk, not just size: each override is independently justified (a
   basename-hint fix, multi-language postcondition checking, hierarchical
   context compression — each with its own measured-regression comment), and
   several existing tests specifically assert the BASE class's simpler,
   unaugmented defaults on purpose. Collapsing these would be a real,
   deliberate behavior-change decision, not a pure refactor — needs your
   explicit call before starting, not just time.

4. **Hook wiring has never fired against a real Claude Code process.**
   Unchanged since the last handoff. `scripts/hook_wrapper.py` and
   `scripts/example_hook_settings.json` are built and tested as far as
   possible without one. Real next step: copy the example into a real
   `.claude/settings.local.json`, do real tool calls, check whether
   `.claude/traces/<session_id>.jsonl` appears with sane content.

**Separately, still genuinely blocked on credentials/network, not on effort:**
- **Gap 4** — `extract_model_observation` in `observations.py` has never
  touched a real LLM (General Compute or Groq, both OpenAI-compatible).
- **`_mcts_pick`** above, same root cause.

## What's built — full file map (everything from this doc's previous version, plus what's new)

- **Access control, collector/worker, hook wiring, migrations 17-19,
  procedures (ticket 05+13), applicability (ticket 12)** — unchanged from
  the last handoff version; see git log for those commits
  (`c6d1d04` through `a9e7fe1`) if you need the detail, not repeated here.
- **Execution engine** (ticket 15, NOW FULLY DONE):
  `backend/app/execution/htn_agent.py` — real implementation;
  `experiments/swebench_pro/htn_agent.py` is a 29-line re-export shim, don't
  edit it. `RunContext` carries all per-run state.
  `HTNConfig`/`StructuralLimits`/`DistributionalBudgets` are the
  hyperparameter config objects. **New since last handoff:** `SchedulerStrategy`
  (ABC), `SequentialScheduler`, `ConcurrentBatchScheduler` — the two
  scheduling algorithms are real, interchangeable strategy objects now, not
  fixed to one class each. `HTNAgent(..., scheduler=ConcurrentBatchScheduler())`
  and `AugmentedHTNAgent(..., scheduler=SequentialScheduler())` both work.
  `ResearchHTNAgent._ast_edit` (stdlib `ast`, replaces a function/class's
  entire source, rejects non-parsing results, wired into real tool dispatch
  as `ast_replace_function`, gated to `.py`-naming goals) and
  `._method_score` (Beta-Bernoulli posterior mean) are real now;
  `._mcts_pick` remains a stub (see above). `._run_ready_batch` was removed
  entirely — confirmed dead, and superseded by `ConcurrentBatchScheduler`
  under a different name.
- **Retrieval** (ticket 14, `local_retrieval.py` NOW FULLY DONE — all 7
  signals real, not 1):
  - `open_files` → `get_current_working_set()` (observations.py, unchanged)
  - `recent_commit_files` → `get_recent_commit_files()` (unchanged)
  - `related_tests` → `app/services/related_tests.py` (naming-convention,
    filesystem-checked)
  - `relevant_symbols` → `get_relevant_symbols()` (wraps `code_index.outline()`)
  - `import_deps` → `app/services/import_deps.py` (**new tree-sitter
    capability** — this repo had zero import-parsing before; real filesystem
    resolution for Python and relative JS/TS, honest raw-string fallback for
    Go and bare JS/TS specifiers)
  - `call_graph_ranked_names` → `get_call_graph_ranked_names()` (wraps
    `call_graph.py`'s existing reachability)
  - `recent_failure_files` → `get_recent_failure_files()` +
    `failure_capture.py`'s new optional `file_paths` param (no migration
    needed, `properties` is already JSONB)

## Real bugs found and fixed this session (worth knowing about if you touch these files)

- `import_deps.py`: `from . import foo` initially resolved to the useless
  literal `.` — fixed to resolve the real file. `from __future__ import X`
  was silently invisible entirely (it's a distinct tree-sitter grammar node,
  confirmed by direct inspection, not assumed) — fixed.
- `related_tests.py`: an early version missed this repo's own
  `test_X_e2e.py` convention — caught by running it against real files here
  (`procedures.py`, `applicability.py`), not synthetic fixtures alone.
- `htn_agent.py`'s scheduler extraction: `ConcurrentBatchScheduler` initially
  depended on `AugmentedHTNAgent`-only attributes (`_shallow`,
  `MAX_PARALLEL_NODES`), so "pluggable scheduler" was only true in one
  direction until real base-class defaults were added to `HTNAgent` itself.
  Caught by the first test of the actual new capability, not assumed to
  work from the extraction alone.
- `_ast_edit`: an early version dropped decorators (`node.lineno` for a
  decorated function points at the `def` line, not the decorator, confirmed
  by direct AST inspection) — caught by writing the regression test first
  and watching it fail against the naive version.
- Two real transcription bugs during the scheduler extraction itself (a
  literal `\"` escape sequence left in several comments, and the blanket
  fix for that then corrupting two unrelated legitimate lines by
  coincidental pattern match) — both caught by `ast.parse` failing, fixed
  precisely at the byte level, not guessed.

## Testing what's built

```
export DATABASE_URL=postgresql://...
python -m pytest tests/ -q
```

**833 passed, 2 skipped, zero failures** — verified fresh at `68d905b`
immediately before writing this doc, not copied from an earlier run.

New test files/classes worth reading if you're touching this area next:
`tests/test_import_deps.py`, `tests/test_related_tests.py`,
`test_htn_agent.py`'s `TestSchedulerStrategy`/`TestAstEdit`/`TestMethodScore`
classes, `test_local_retrieval_e2e.py`'s producer-specific tests (each one
proves its signal is a genuinely INDEPENDENT retrieval path — findable via
that signal alone, with zero semantic/lexical overlap with the query — not
just a filter/boost on what semantic search already found).

## Working style that kept producing real results — same as last time, still true

Verify against real code before asserting anything (every fix above was
confirmed broken against the pre-fix code, then confirmed fixed — not
written and assumed). Real database, not mocks, for anything touching
Postgres. Real filesystem fixtures, not just synthetic ones, for anything
touching `call_graph.py`/`code_index.py`/tree-sitter — several of the real
bugs above were only caught by testing against this repo's own actual files.
Honest scope notes in every module's own docstring, not buried elsewhere.

## Suggested skills

Still backend Python/SQL/pytest work — no specialized skill applies.
