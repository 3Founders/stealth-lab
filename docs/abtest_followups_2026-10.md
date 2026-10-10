# A-vs-C product test: results so far and what to improve next (2026-10-10)

A = plain Claude Code on Opus 5.5. C = the same Claude Code with StealthLab installed the product way
(`stealthlab-mcp install --with-exec`, mirrored per workspace by `experiments/abtest/setup_c.mjs`). Correctness is
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
session's model does the task with a one-line note. Capture hooks report outcomes and grow `.stealth/`.

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
   measurable on these benchmarks (empty `.stealth`, unrelated tasks): needs the repo-level test below.
4. **Residual misses.** Some come from details the task never states (a seeded random draw, a sign rule, a
   misleading benchmark text); no test written from the task catches those. The test writer's brief is general
   (lib/dispatch.mjs SPEC_SYSTEM); more prompt tuning gave noisy, sample-dependent results in a small trial.
5. **Routing.** Every benchmark task matches the same Goal, so the router learns one pool. A finer Goal match would
   let it learn per kind of task. With a cold database one early failure can bias the next similar tasks.
6. **Realism.** The current suites are function-level tasks with visible tests. Still to do:
   - repo-level tasks (SWE-rebench, time-ordered) on Modal, no Docker locally;
   - a "no visible tests" arm (test-first is the only check there);
   - a knowledge-layer test (a sequence of related issues in one repo, surveyed `.stealth`);
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
   - the agent uses the CLI through Bash for that (`stealthlab-mcp library ...`, `survey`, a `find` command) -- no MCP;
   - run the survey automatically when the hook sees a repo without `.stealth`;
   - tidy the library (merge duplicates) automatically now and then;
   - upgrade note: `install --with-exec` removes the MCP registration; offer `--keep-mcp`.
3. **Tell the session what a cheap model did.** A dispatched prompt is blocked, so the session never sees the change;
   the next prompt's context should carry a short note ("minimax changed x.py to do Y").
4. **Messy prompts.** Dispatch only when the request is clear enough (the triage judge rates it), and give the test
   writer the conversation, not only the last prompt. Test with messy rewrites of the same tasks, a simulated user
   who answers questions, non-task prompts mixed in, and real prompts from a pilot.
5. **Cursor.** No dispatch today. Either a blocking `beforeSubmitPrompt` (if Cursor allows the wait) or one MCP tool
   ("solve cheaply first") the Cursor rules tell the agent to call. Test with cursor-agent in the same A/C harness.
6. **Long-horizon, repo-level test of `.stealth`.** A series of tasks in one repo in real order, workspace and
   `.stealth` kept; arms A, C, and C with `.stealth` wiped each task. Spider 2.0-DBT locally first; SWE-rebench /
   SWE-EVO (verify) and ADE-bench on Modal with an API key (not the subscription login).
