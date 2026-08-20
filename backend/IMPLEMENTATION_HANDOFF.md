# Implementation handoff: ingestion pipeline closed end-to-end, hooks fired for real, retrieval wired into a caller

> **Update (Aug 20, commits after `68d905b`).** The previous version of this
> doc listed four "remaining" items and said two of them were blocked on
> credentials/network. Neither was true on this machine (`.env` carries
> `DATABASE_URL` and `GENERAL_COMPUTE_API_KEY`), and a closer look found the
> real blocker wasn't in that list at all: `retrieve_local_first()` needs
> `StructuralContext.open_files`, which needs `get_current_working_set()`,
> which reads the `observations` table — and **nothing wrote to it**.
> `extract_deterministic_observations()`/`persist_observation()` had zero
> non-test callers; `process_collector_file()` had zero callers outside
> tests; `ingestion_jobs` rows were written and never consumed. That's now
> closed, tested against a real database, and proven against a **real
> Claude Code hook firing during this very session** — not a synthetic
> fixture. Read "What's actually remaining" below; it's shorter now.

This is the CODE-level handoff. For the PLANNING-level one, see
`.scratch/memory-substrate/map.md` and the 18 resolved tickets in
`.scratch/memory-substrate/issues/` (both on `main`). This doc tells you
what's built, tested, and true right now — it doesn't repeat that reasoning.

## Environment setup

- **Real database**: Supabase Postgres, connection string via `$DATABASE_URL`
  or `--dsn` to `scripts/migrate.py`. Never hardcode it in a committed file.
- **Windows**: use `python`/`py`, not `python3` (App Execution Alias
  intercepts it) — including in hook `command` fields; `example_hook_
  settings.json` had `python3` and was fixed this session (see below).
  `git am --abort` clears a stuck `.git/rebase-apply` safely if a patch
  application gets interrupted or re-run.
- **Local dev DB**: Postgres 17 + pgvector 0.8.0, confirmed working on
  Windows this session. No prebuilt Windows binary exists for PG17 at time
  of writing — built from source: `nmake /f Makefile.win` under a VS Build
  Tools 2022 `vcvars64` shell, pointed at `PGROOT`, then manually copied
  `vector.dll`/`vector.control`/`vector--*.sql` into the PG `lib`/`share\
  extension` dirs (the Makefile's own `install` target needs admin rights
  Program Files requires; do the copy from an elevated prompt).
  ```
  createdb stealthlab_local   # or use an existing local Postgres 17 db
  psql -d stealthlab_local -c "CREATE EXTENSION vector;"
  python backend/scripts/migrate.py --dsn postgresql://<user>:<pass>@localhost:5432/stealthlab_local
  ```
  All 19 migrations apply cleanly in order on a fresh DB — confirmed this
  session, not assumed.
  e2e tests skip cleanly (not fail) without `DATABASE_URL` set.
- **pip**: `pip install -r requirements.txt --break-system-packages
  --ignore-installed PyJWT` (Debian-installed PyJWT conflicts otherwise).

## What's actually remaining — read this first

1. **`ResearchHTNAgent._mcts_pick`** (item 4 of that class's own docstring) —
   still a `NotImplementedError` stub, the only one left in `app/`. Needs a
   real LLM call to generate 2-3 decomposition candidates before the
   UCB1/scoring mechanics around it can be tested meaningfully. `_ast_edit`
   and `_method_score` are done. **Not credential-blocked** — General
   Compute creds are in `.env` — just not built yet.

2. **`extract_model_observation` has never touched a real LLM.** Same
   root cause as above (built, tested for shape, never actually called).

3. **Collapsing the HTN class hierarchy's other 7 behavioral overrides**
   (`_verify_precondition`, `_verify_postcondition`, `_system_prompt_extra`,
   `_replan_evidence`, `_build_context`, `_basename_index`,
   `_tools_for`/`_persona`) into one class. The SCHEDULER half of ticket 15's
   "one engine, pluggable strategy" ask is done; this is the other, larger
   half, deliberately left alone. Real risk, not just size: each override is
   independently justified (a basename-hint fix, multi-language
   postcondition checking, hierarchical context compression — each with its
   own measured-regression comment), and several existing tests specifically
   assert the BASE class's simpler, unaugmented defaults on purpose.
   Collapsing these is a real, deliberate behavior-change decision — needs
   an explicit call before starting, not just time.

4. **τ³-bench micro-test — started, not finished.** The plan: seed
   `banking_knowledge`'s procedural documents into the real `procedures`
   table (ticket 05/12's non-compensatory preconditions map almost exactly
   onto that domain's "## Eligibility Requirements" / "## Opening
   Procedure" documents), wrap `retrieve_local_first` as a tau2
   `@register_retriever`, and run one real task, comparing retrieved ids
   against `task.required_documents`. **Done so far:** all 698
   `banking_knowledge` documents are ingested as real `knowledge_nodes`
   (`provenance='prior_library'`), via the existing
   `scripts/ingest_banking_knowledge.py` pointed at
   `experiments/tau3_bench/_tau2_bench_src` — confirmed with
   `SELECT count(*) FROM knowledge_nodes WHERE node_type='policy_document'`
   → 698. **Not done:** the ingestion script now also stores
   `properties.source_doc_id` (a real gap fixed this session — the tau2
   doc id previously existed only as an in-memory onboarding key and was
   never persisted anywhere retrievable), but the procedure-seeding
   script, the tau2 retriever adapter, and the actual task run are all
   still unwritten. Explicitly note when picking this up: banking_
   knowledge has no repo checkout, so `retrieve_local_first`'s structural
   tier is inert there — this test exercises procedure seeding,
   applicability, and semantic retrieval, NOT the structural hierarchy
   item 5 below already validates against a real repo.

## What's built and verified this session (not just written — run against real infrastructure)

**1. The three-link gap that made `observations` orphaned is closed.**
New `backend/app/services/ingestion_jobs.py`: `claim_jobs()` (real
`SELECT ... FOR UPDATE SKIP LOCKED`), `handle_normalize_trace_event()`
(calls the existing, previously-uncalled `extract_deterministic_
observations()` + `persist_observation()`), `process_pending_jobs()`
(per-job try/except so one bad job — A4's lesson — doesn't stall the
batch), `requeue_stuck_jobs()` (manual recovery for a worker that died
mid-job). New `backend/scripts/run_ingestion.py` — the runnable entry
point neither `process_collector_file()` nor `process_pending_jobs()` had
before this: one `--once` pass or `--interval N` loop over both.

Verified against a real database with `tests/test_ingestion_jobs_e2e.py`
(4 tests, all passing): a real `Edit` trace_event produces a real
`file_touched` observation end to end; an unknown `job_type` is marked
`failed` rather than stuck; a job pointing at a since-deleted trace_event
is a no-op, not a batch-stalling error; `requeue_stuck_jobs` only touches
genuinely old `processing` rows.

**2. `retrieve_local_first()` now has a real caller.** New
`assemble_structural_context()` in `local_retrieval.py` — the
orchestrator the previous handoff named as missing. Takes
`(session_id, repo_root, seed_files)`, calls the 6 already-built
producers, returns a `StructuralContext`. **Cold-start handling, stated
because it's easy to get wrong**: a session's first retrieval has no
`file_touched` observations yet, and `get_call_graph_ranked_names()`
early-returns on empty `open_files` — so without a seed the entire
structural tier goes silently empty. `seed_files` (e.g. `git diff
--name-only HEAD`) feeds ONLY the three filesystem producers
(`relevant_symbols`/`import_deps`/`related_tests`); it never populates
`open_files` itself and never becomes a FILTER candidate — a repo-scoped
seed is lower-precision than a session-scoped one, and letting it into a
FILTER slot would be the criterion-compensation mistake ticket 12 warns
against, in reverse.

Wired into `app/mcp_server/server.py`'s `solve_task` — the natural
caller, already has `repo_path` + the pool. It now builds a structural
context (git-diff-seeded unless a `session_id` is passed), calls
`retrieve_local_first`, and appends the result to `memory_block`
alongside the existing `retrieve_precedent`-based prior-solution lookup
(kept separate and unchanged — different retrieval concern, precedent
trajectories vs. structural file context, conflating them would have
been wrong).

Verified with 3 new tests in `test_local_retrieval_e2e.py` against a real
DB and this repo's own real files (not synthetic fixtures): cold start
with no session/repo/seed is honestly all-empty; a real session's
observations produce real `code_index.py`/tree-sitter output for a real
file in this repo; `seed_files` feeds the filesystem producers but never
leaks into `open_files`.

**3. Hooks fired for real — genuinely, during this session's own work, not
simulated.** Fixed `scripts/example_hook_settings.json`'s `python3` →
`python` (would have failed immediately on Windows, contradicting this
doc's own environment section). Registered it in this repo's
`.claude/settings.local.json`, then made real tool calls — and
`.claude/traces/<this-session's-own-id>.jsonl` appeared with 33 real
events, including genuine `tool_input`/`tool_output` from this
conversation's own Bash commands. Ran `run_ingestion.py` against it: **40
records seen, 40 inserted, 40 jobs done, 0 failed** — real
`command_executed`/`file_touched` observations landed in the database
from a live hook firing, not a hand-built payload.

One real, unrelated gap found and fixed in the course of this: **`.claude/
traces/` and `.claude/settings.local.json` were untracked but NOT
gitignored** — `git status` showed both as `??`, meaning a `git add -A`
would have committed real (redacted, but still real) session transcript
content and local permission config. Neither was in `.gitignore` at all
before this session. Fixed.

## What's built — file map (carried forward, unchanged since last handoff unless noted)

- **Access control, collector/worker, hook wiring, migrations 17-19,
  procedures (ticket 05+13), applicability (ticket 12), execution engine
  (ticket 15, scheduler strategy), retrieval producers (ticket 14, all 7)**
  — unchanged from the last handoff version; see git log
  (`c6d1d04` through `68d905b`) for that detail, not repeated here.
- **New this session**: `app/services/ingestion_jobs.py`,
  `scripts/run_ingestion.py`, `assemble_structural_context()` in
  `local_retrieval.py`, `solve_task`'s structural-context wiring in
  `app/mcp_server/server.py`, `properties.source_doc_id` in
  `scripts/ingest_banking_knowledge.py`.

## Testing what's built

```
export DATABASE_URL=postgresql://...
python -m pytest tests/ -q
```

**852 passed, 1 skipped, zero failures** — verified fresh this session on
a freshly-migrated local Postgres 17 + pgvector DB, not copied from an
earlier run. (Without `DATABASE_URL`: DB-gated tests skip rather than
fail — a green run without a database is not coverage of anything
touching Postgres; check the skip count.)

New test files/classes worth reading if you're touching this area next:
`tests/test_ingestion_jobs_e2e.py` (the job-consumer link),
`test_local_retrieval_e2e.py`'s `assemble_structural_context` tests (the
orchestrator + cold-start behavior).

## Real bugs found and fixed this session

- The back-compat shim `experiments/swebench_pro/htn_agent.py` claimed
  "every name this module used to define is re-exported here unchanged"
  but used `from app.execution.htn_agent import *`, which skips every
  `_underscore` name (no `__all__` defined). `_node_row` was missing —
  pytest reported `Interrupted: 1 error during collection` and ran **zero**
  tests. Fixed with an explicit second import; docstring corrected.
- `ingestion_jobs`/`observations` link never existed — see above.
- `.gitignore` didn't cover `.claude/traces/` or `.claude/settings.local.json`
  — see above.
- `ingest_banking_knowledge.py`'s tau2 document id was never persisted
  anywhere retrievable (only lived as an in-memory `KnowledgeSpec.key`
  during onboarding) — fixed by storing it in `properties.source_doc_id`.

## Working style that kept producing real results — still true

Verify against real code before asserting anything. Real database, not
mocks. Real hook firing against this session's own tool calls, not a
synthetic payload, for the hook-wiring claim specifically — a hand-built
JSON payload piped to `hook_wrapper.py` would have proven the wrapper's
parsing logic but not that Claude Code's actual hook mechanism invokes it
correctly with real fields. Honest scope notes in every module's own
docstring, not buried elsewhere.

## Suggested skills

Still backend Python/SQL/pytest work — no specialized skill applies.
