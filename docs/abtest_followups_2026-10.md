# A-vs-C product test: results so far and what to improve next (2026-10-10)

A = plain Claude Code on Opus 5.5. C = the same Claude Code with StealthLab installed the product way
(`kell install --with-exec`, mirrored per workspace by `experiments/abtest/setup_c.mjs`). Correctness is
graded on each task's full hidden tests; the agent and dispatch only ever see the visible ones.

## Results

| Run | Tasks | A solved / cost | C solved / cost | C saving |
|---|---|---|---|---|
| batch 1 | 30 (DS-1000, BigCodeBench) | 30 / $2.87 | 26 / $1.59 | -45% |
| batch 2 | same 30 | 29 / $2.65 | 24 / $1.48 | -44% |
| batch 3 | 45 (+ MBPP+) | 44 / $3.80 | 41 / $2.69 | -29% |
| batch 4 | 45 new tasks | 42 / $4.01 | 40 / $2.31 | -43% |

Batch 4 (Sonnet writes the test-first tests, Sonnet no longer a routing candidate): cheap models delivered 33/45;
of C's 5 misses, 3 were missed by A as well, 1 was Opus's own fallback answer, 1 was a cheap model's.

## What C does now (Claude Code)

The prompt hook asks the server for a plan, has a cheaper Claude model write a few acceptance tests from the task
(one tool-less call, ~$0.01-0.02), then runs the cheap open models one after another in isolated worktrees against
the project's check plus those tests. The first that passes is applied and the prompt is stopped; if none does, the
session's model does the task with a one-line note. Capture hooks report outcomes and grow `.kell/`.

## Improvements to come back to

1. **Opus fallbacks are the main cost left.** In batch 4 they were $1.30 of C's $2.31 (12 of 45 tasks, ~$0.137 each
   vs ~$0.09 for A). Each pays for the tests, three failed cheap tries and the session. Levers: a higher cheap success
   rate (better task text to the executors, a retry with the failing test output), stopping after the first
   cheap failure when the router is confident, and a lean session (done: hooks only).
2. **Dispatch for Cursor and other clients.** Today the cheap-first saving is Claude Code only: Cursor's
   `beforeSubmitPrompt` (lib/cursor_hooks.mjs) answers at once and only adds knowledge after the first tool call. A
   Cursor path needs either a blocking prompt hook (if Cursor allows the wait) or an MCP tool the agent calls that
   runs dispatch.
3. **Library knowledge to the cheap models and to the fallback.** Dispatch sends the executors only the task text,
   and the fallback note drops the library matches. Pass the matching library entries (no plan text) to both. Not
   measurable on these benchmarks (empty `.kell`, unrelated tasks): needs the repo-level test below.
4. **Residual misses.** Some come from details the task never states (a seeded random draw, a sign rule, a
   misleading benchmark text); no test written from the task catches those. The test writer's brief is general
   (lib/dispatch.mjs SPEC_SYSTEM); more prompt tuning gave noisy, sample-dependent results in a small trial.
5. **Routing.** Every benchmark task matches the same Goal, so the router learns one pool. A finer Goal match would
   let it learn per kind of task. With a cold database one early failure can bias the next similar tasks.
6. **Realism.** The current suites are function-level tasks with visible tests. Still to do:
   - repo-level tasks (SWE-rebench, time-ordered) on Modal, no Docker locally;
   - a "no visible tests" arm (test-first is the only check there);
   - a knowledge-layer test (a sequence of related issues in one repo, surveyed `.kell`);
   - a customer pilot to measure the share of real work with a usable check.
   Report results as "on checkable tasks, X% saved at Y% quality", never "saves X% on your work".
7. **Latency** (p50/p95/p99 per arm) is measured from the full run on (runner `seconds`, wall time of the session
   including the hooks).

## Queued product fixes (agreed 2026-10-11, not started)

In order of value. Each needs the user's go-ahead before building.

1. **Find the check automatically.** Today the A/B runner hands dispatch the check (STEALTHLAB_DISPATCH_CHECK); a
   real user configures nothing, so dispatch mostly never fires. Detect it: pytest for Python, `npm test` from
   package.json, `dbt test` for dbt projects, and so on; fall back to the matched library entry's check.
2. **Hooks-only gaps** (the session no longer gets StealthLab's MCP instructions):
   - a short StealthLab block in CLAUDE.md (as AGENTS.md already gets): record fixes in the library, attach to an
     existing entry instead of duplicating, finish the survey worklist, look up sub-tasks;
   - the agent uses the CLI through Bash for that (`kell library ...`, `survey`, a `find` command) -- no MCP;
   - run the survey automatically when the hook sees a repo without `.kell`;
   - tidy the library (merge duplicates) automatically now and then;
   - upgrade note: `install --with-exec` removes the MCP registration; offer `--keep-mcp`.
3. **Tell the session what a cheap model did.** A dispatched prompt is blocked, so the session never sees the change;
   the next prompt's context should carry a short note ("minimax changed x.py to do Y").
4. **Messy prompts.** Dispatch only when the request is clear enough (the triage judge rates it), and give the test
   writer the conversation, not only the last prompt. Test with messy rewrites of the same tasks, a simulated user
   who answers questions, non-task prompts mixed in, and real prompts from a pilot.
5. **Cursor.** No dispatch today. Either a blocking `beforeSubmitPrompt` (if Cursor allows the wait) or one MCP tool
   ("solve cheaply first") the Cursor rules tell the agent to call. Test with cursor-agent in the same A/C harness.
6. **Long-horizon, repo-level test of `.kell`.** A series of tasks in one repo in real order, workspace and
   `.kell` kept; arms A, C, and C with `.kell` wiped each task. Spider 2.0-DBT locally first; SWE-rebench /
   SWE-EVO (verify) and ADE-bench on Modal with an API key (not the subscription login).

## Product hardening (agreed 2026-10-11: BUILD it, not only test it) -- after fixes 1-3 merge

A. Messy prompts and multi-turn sessions
   1. Task brief: a cheap call turns the last few turns (hook payload transcript_path) + repo context into a short,
      precise brief (resolves "that bug", fills details from earlier turns, splits multi-part requests); the cheap
      models and the test writer get the brief, not the raw prompt.
   2. Request type: code task / question / chat, plus clarity, size and checkability; only clear, checkable,
      bounded tasks are dispatched; the rest go to the session's model with the knowledge block.
   3. Unclear -> no dispatch (the session's model can ask the user).
   4. Continuity: a short per-session log of what StealthLab did; follow-ups carry it (fix 3 is the first step).
   5. Overrides: `!opus` skips StealthLab for one prompt, `!cheap` forces a try.
B. Long, real repositories
   1. Cheap models get the repo knowledge: the relevant .kell facts, library matches, file hints.
   2. Worktrees reuse the project's installed dependencies (node_modules, the virtualenv) so checks can run.
   3. Faster checks: tests related to the changed files first, the full suite before accepting.
   4. Time limit from the project's own test duration.
   5. Size gating: big multi-file changes go to the session's model (or bounded steps of a plan).
C. Real user flow
   1. Safe apply: an undo point before applying, a short diff summary, `kell undo`.
   2. Don't overwrite files the user changed during the run: hand over instead.
   3. A clear message for a cheaply-handled prompt: who, what changed, cost, how to undo.
   4. Optional "ask before applying" mode.
Build as cloud work in parts A, B, C, each with tests and a usage check after it.

## Routing rework (agreed 2026-10-11; build after the OpenAPI re-run)

Why the router did not stop sending OpenAPI tasks to the cheap models (phase 1: 3 cheap passes in 20):
1. Every benchmark task matched ONE Goal, so the router learned a blended pass rate (BoolQ ~100%, OpenAPI ~15%).
2. Dispatch tries all configured cheap models (cheapCandidates fills them in), so the router only decides
   "dispatch or not", never which models.
3. Local learning is per workspace/repo; each OpenAPI task was a fresh workspace, so each re-explored all models.
4. Time-limit cancellations are not counted (right for blame), so a third of the OpenAPI tries gave no signal.
Plan:
- Goals per kind of task (finer Goal matching), so different task types are learned apart.
- The router chooses which cheap models to try and whether trying pays at all (expected saving vs the overhead:
  test writing, tries, added latency); dispatch follows the plan instead of filling in every model.
- Cut-off / provider failures recorded as "dispatch does not pay here" (not as the model's quality).
- What is learned for a kind of task is shared across repos (a new repo starts from it, not from zero).

## State on 2026-10-11 ~08:20 UTC (for whoever continues)

Commits on main (local, NOT pushed since 75e502f): e3ee1ae, 0f94be5 (docs), b1efa59 (OpenAPI fixes: 429 back-off,
2 parallel, 160k context, task-size time allowance, gpt-oss retry, test-writer brief), e63599f (cancelled and
provider-limited attempts are not model failures; dispatch skips a provider with < 300k tokens left today),
fd7d817 (routing per kind of task + 20% re-check of skipped kinds + report 'not dispatched' column).
Package tests: 334+ pass. Backend routing offline tests pass; tests/test_mcp_minimal_surface_offline.py fails
independently of these changes (it expects tools the progressive tool list hides).

Phase 1 result (experiments/abtest/.local/results/phase1.jsonl; `python experiments/abtest/report.py phase1`):
A 120/129 $15.50, C 111/129 $10.14 (-35%). NOT final: General Compute's daily 10M-token limit ran out during arm C
(crossed 8M at 02:43 UTC, 10M at 03:17 UTC), so cheap success fell from 68% to 44% for the last ~40 tasks; also the
router counted cancelled losers and rate-limited runs as failures (fixed in e63599f).

Next steps, in order:
1. After General Compute's daily reset (check `x-ratelimit-remaining-tokens-day` on any GC response): fresh test DB,
   then re-run arm C on all 130 phase-1 tasks with the current main (keep phase1.jsonl's A rows; move the old C rows
   to a backup file first), auto-resume loop as in the session (runner exit code 3 = Claude usage limit; wait, rerun).
   Check after ~10 tasks: per-suite saving, solved, latency, 'not dispatched'.
2. Phase 1b suites: feat/phase1b-suites (validation suite nearly done; reporting data/reference done; node and git
   not started). Phase 2 suites (MultiPL-E JS, Design2Code, WebSight) are built and committed; run A and C.
3. Queued product fixes and product hardening: see the sections above. Branches with partial work:
   feat/product-fixes-1-3 (design notes only), feat/longhorizon-dbt (Spider2-DBT data notes),
   feat/cursor-dispatch (LOCAL only: .claude/worktrees/agent-a6abcdfc7a4cc9b6b, WIP commit 7ba8273, untested).
How to run (Windows, this machine): scratch Postgres on 127.0.0.1:55432 (pg_ctl -D C:\Users\user\pg-scratch\data
start), DB kel_abtest reset with `DROP DATABASE ... WITH (FORCE)`, `CREATE DATABASE`, then
`DATABASE_URL= python backend/scripts/migrate.py --dsn postgresql://kel_owner@127.0.0.1:55432/kel_abtest` (it
sometimes fails once with a duplicate edges constraint -- run it again on a fresh DB); backend:
`python experiments/abtest/run_backend.py` (port 8765); runner:
`python experiments/abtest/runner.py --run phase1 --seed 20261010 --tasks ds1000:30,bird:50,ifeval:20,boolq:10,openapi:20 --arms C --parallel --testfirst-model claude-sonnet-5-5 --exclude-runs batch1,batch2,batch3,batch4,tf-sonnet,smoke-hooksonly,smoke-phase1`.
Spawned "remote" agents ran LOCALLY on the subscription (not cloud credits): do not use them to save usage.

## State on 2026-10-11 15:15 UTC (latest)

- Phase 1 DONE on current main (routing per kind of task): `python experiments/abtest/report.py phase1` --
  A 120/128 $15.38, C 114/128 $9.56 (-38%); per suite BIRD -50%, BoolQ -89%, DS-1000 -43%, IFEval -62%,
  OpenAPI -1% (router skipped cheap models on 11/20). Latency p50/p95: A 33/92 s, C 56/286 s.
  bird-1505 arm C still to redo (its row was removed: two runners overlapped for a minute; A also fails it):
  same phase-1 command with `--only bird-1505`.
- Phase 2: arm A DONE (75 tasks, 39 solved, $16.87; page tasks are hard under the fixed bar). Arm C PAUSED after 7
  tasks: both General Compute keys (GENERAL_COMPUTE_API_KEY, general_compute_api_key1) are out of tokens for
  2026-10-11; the daily limit appears to reset at 00:00 UTC. Resume with:
  `python experiments/abtest/runner.py --run phase2 --seed 20261010 --tasks multiple_js:20,d2c:30,websight:25 --arms C --parallel --testfirst-model claude-sonnet-5-5 --gc-key-name general_compute_api_key1`
  (it pauses itself, exit 4, below 100k General Compute tokens; exit 3 = Claude usage limit, rerun after reset).
  Expect the page suites (d2c, websight) to cost about as much as A: the cheap models are text-only and cannot
  see the screenshot; MultiPL-E JS is where C should save.
- Claude weekly usage at 76% (Pro); the user's cap for this session is 80%, then handoff to Chaitanya.
- The chain script used: experiments/abtest/.local/run_chain.sh (not committed; .local is ignored).
