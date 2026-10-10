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
