# Implementation handoff: procedure extraction real end-to-end, hooks verified firing for real, eval plan written

> Wholesale rewrite, not another stacked update box — the last version
> accumulated two "Update" sections in a row, which gets confusing for a
> fresh reader. Everything is folded in here. Old versions are git
> history (`git log -p -- backend/IMPLEMENTATION_HANDOFF.md`).

This is the CODE-level handoff. For the PLANNING-level one, see
`.scratch/memory-substrate/map.md` and the 18 resolved tickets in
`.scratch/memory-substrate/issues/`. For the SWE-bench Pro evaluation
plan specifically, see `experiments/swebench_pro/PROCEDURE_MEMORY_EVAL_PLAN.md`
— that's a plan, not a result; don't read it as one.

## What changed since the last version (5 real commits, 2 authors)

1. **`assemble_structural_context()` was blocking the entire MCP server's
   event loop** — found during integration review after `solve_task` was
   wired to call it for real. Measured, not assumed: `get_call_graph_ranked_names`
   alone took 4.2 real seconds; with `--workers 1`, that's the whole
   server frozen for every concurrent user, once per `solve_task` call.
   Fixed with `run_in_executor()`, and — real finding, not the obvious
   fix — SEQUENTIAL awaits, not `asyncio.gather()`'d concurrently: gathering
   made it worse (1 heartbeat vs 19), since several CPU-bound tree-sitter
   threads contending for the GIL at once starves the main thread harder
   than one at a time does. Confirmed at all three points (0 → 1 → 19
   heartbeats) before picking sequential.

2. **Hooks now fire for real, and the observations pipeline has a real
   consumer.** `process_collector_file()`/`persist_observation()` had zero
   non-test callers before this — meaning `observations`, and therefore
   `get_current_working_set()`/`StructuralContext.open_files`, was
   permanently empty regardless of anything upstream. Fixed with a real
   `ingestion_jobs.py` (SKIP LOCKED consumer) and `scripts/run_ingestion.py`
   (the runnable entry point). **Confirmed working against a real Claude
   Code session** — registered locally, made real tool calls, 33 real
   events landed in `.claude/traces/`, ran clean through ingestion (40/40
   inserted, 40/40 jobs done). This closes the one item every previous
   version of this doc flagged as the most important unverified piece.

3. **Procedure extraction is real.** `capture_procedure()`'s own docstring
   named the gap: preconditions were meant to be "derived from the source
   episode's `state_before` projection... not hand-authored tags," but
   nothing did that derivation. Closed: `environment_probe.py` (deterministic,
   asserts real environment facts), `slot_binders.py`, and a full
   `procedure_extraction/` package (evidence/schema/derive/strategies/
   validators/registry), migration 20 (`approval_status` as a third axis,
   separate from `verification_state` — statistical verification and human
   approval are different claims). Real, verified finding during testing
   (took two wrong hypotheses to pin down against a live DB, documented in
   `derive.py`): `project_state()`'s supersession makes a predicate
   **disappear entirely** for any `as_of` before the new claim's own
   `t_valid` — not stale, not wrong, absent. A capstone e2e test proves the
   real chain: session → observations → derived preconditions → a real
   `procedures` row → correctly blocked by `applicability.py`'s
   **untouched** approval gate.

4. **Procedure retrieval has its first real caller**, and wiring it
   surfaced a real gap that's now closed too. `solve_task` retrieves an
   applicable procedure (environment-derived scope) and renders it into
   `memory_block` for the flat `Agent` — `require_verified` stays at its
   real default (`True`), not weakened to make something show up
   immediately. Wiring a real caller exposed that `approval_status`
   (added in migration 20) was never actually checked anywhere — a
   procedure could reach `verified` via pure statistics with no human
   ever approving it, and automatic retrieval would have silently
   returned it. Fixed in `applicability.py`, gated on the same
   `require_verified` flag. `approve_procedure()`/`reject_procedure()`
   added as the missing counterpart (previously only raw SQL could do this).

5. **Independent verification pass** (this session, not Chaitanya's):
   read all 4 of the above commits in full, re-synced, applied migration
   20, ran the full suite fresh rather than trusting the reported counts.
   Found and fixed one real **doc**-staleness bug (item 4 above landed in
   code before this doc was updated to say so) and one real **code** bug
   his session correctly diagnosed and deliberately left for the original
   author: 2 Windows-only test failures (`PermissionError: [WinError 5]`
   in `trace_collector.py`'s `os.replace()`, almost certainly Windows
   Defender racing a rename). Fixed with a bounded retry
   (`_replace_with_retry`, 5 attempts/50ms) — a no-op cost on POSIX, only
   ever fires on Windows in practice. **Not independently confirmed on a
   real Windows machine** — this sandbox is Linux; someone should confirm
   the expected 910/1/0 count for real.

6. **A written, honest SWE-bench Pro evaluation plan** —
   `experiments/swebench_pro/PROCEDURE_MEMORY_EVAL_PLAN.md`. Central
   finding it's built around, confirmed by reading `extract_procedure()`
   directly: every extraction creates a brand-new `candidate` procedure
   (no merge into an existing similar one), and ticket 13's verification
   bar needs real, repeated successful reuse — which SWE-bench Pro's
   non-repeating issues structurally can't provide at this corpus size.
   Splits testing into two separate questions (does unverified retrieval
   help; does the full verified pathway help) rather than one conflated,
   uninterpretable number. Nothing in this plan has been executed yet.

## Environment setup (unchanged, repeated because it trips people up)

- **Real database**: Supabase Postgres via `$DATABASE_URL` / `--dsn`.
  Never hardcode it in a committed file.
- **Windows**: use `python`/`py`, not `python3`, including in hook
  `command` fields. `git am --abort` clears a stuck `.git/rebase-apply`
  safely.
- **Local dev DB**: Postgres 16+ (Chaitanya's own machine runs PG17 —
  see his notes on building pgvector from source on Windows if you need
  that path) + pgvector.
  ```
  createdb stealthlab_local
  psql -d stealthlab_local -c "CREATE EXTENSION vector;"
  python backend/scripts/migrate.py --dsn postgresql://<user>:<pass>@localhost:5432/stealthlab_local
  ```
  All 20 migrations apply cleanly in order on a fresh DB. e2e tests skip
  cleanly (not fail) without `DATABASE_URL` set.
- **pip**: `pip install -r requirements.txt --break-system-packages
  --ignore-installed PyJWT`.

## What's actually remaining

1. **`ResearchHTNAgent._mcts_pick`** — the only stub left in `app/`. Needs
   a real LLM call to generate 2-3 decomposition candidates.

2. **Nothing has ever run against a real LLM.** Two, real, separate
   instances of the same root cause: `extract_model_observation`
   (`observations.py`) and `GroundedHybridExtractor`
   (`procedure_extraction/strategies.py`) are both built and tested only
   against scripted `FakeClient`s. Not credential-blocked in principle
   (General Compute creds are meant to live in `.env`) — just never
   actually invoked against a real endpoint from any session so far.

3. **Collapsing the HTN class hierarchy's other 7 behavioral overrides**
   into one class — the scheduler half of ticket 15's "pluggable
   strategy" ask is done; this, the larger half, is still deliberately
   separate subclass overrides. Real behavior-change risk (several
   existing tests assert the base class's simpler defaults on purpose),
   needs an explicit decision to start, not just time.

4. **The SWE-bench Pro procedure-memory pipeline itself doesn't exist
   yet** — see `PROCEDURE_MEMORY_EVAL_PLAN.md` section 4. The old
   `graph_ingest.py` (static preload) cannot answer either of that plan's
   two research questions; it never creates `procedures` rows. Building
   the real pipeline (agent runs → episodes → extraction → reuse →
   eval-set comparison) is genuinely new work, blocked in practice on
   item 2 above (a real LLM call) for the "agent runs" step.

5. **No `.claude/settings.local.json` exists in THIS repo checkout** —
   `example_hook_settings.json` is real and confirmed working (see item 2
   above), but it's still documentation someone has to deliberately copy
   in per machine, not auto-installed.

## Testing what's built

```
export DATABASE_URL=postgresql://...
python -m pytest tests/ -q
```

**899 passed, 2 skipped, zero failures** — verified fresh at this exact
commit (`c23c4cd`), on Linux, immediately before writing this doc. Windows
count should be 910/1/0 per the fix in item 5 above, not independently
confirmed.

New/updated test files worth reading if you're touching this area next:
`test_local_retrieval_e2e.py`'s event-loop heartbeat test (a real,
measured concurrency test, not a mock), `test_environment_probe*.py`,
the six `test_procedure_extraction*.py` files, `test_applicability_e2e.py`'s
approval-gate additions, `test_trace_collector.py`'s
`test_replace_with_retry_*` tests (simulate the Windows race directly
since this sandbox can't reproduce the real one).

## Working style that's kept producing real results across both sessions

Verify against real code before asserting anything — every fix above was
confirmed broken first, then confirmed fixed, not written and assumed.
Real measurement over reasoning from theory (the event-loop fix went
through 3 measured iterations, not the first plausible-sounding one).
Real database, not mocks. Honest scope notes in every module's own
docstring. Independent verification of another session's work before
building on top of it — this doc itself is the product of that twice
now, and found a real bug each time.

## Suggested skills

Still backend Python/SQL/pytest work — no specialized skill applies.
