# DS-1000 round 7: does the knowledge hook help a frontier model? (preregistered)

Written before any scored round-7 episode, and hashed with the runner and analysis (`runs7/design.sha256`). The hook lookups (`runs7/sonnet/hooks.json`) were computed before this file was written; they involve no model answers.

## Why

Round 5 ([PREREGISTRATION_5.md](PREREGISTRATION_5.md)) confirmed the Claude Code knowledge hook for open models: +8.5 points, p = 0.004. The only frontier evidence is round 4 ([PREREGISTRATION_4.md](PREREGISTRATION_4.md), deviation 1). There, Sonnet in Claude Code used Kel as a tool it could call, and gained +3.7 [−7.8, +15.5], p = 0.65. The hook, the delivery that worked, has never been tested on a frontier model. The product runs in Claude Code, so this is the claim that matters most.

## Design

- **Model and host:** `claude-sonnet-5` through Claude Code subagents, as round 4's Sonnet arms (`sonnet_r4.py`). Same 89 tasks (82 transfer, 7 control). Batches of 6 tasks from 6 different families; the same 17 batches for both arms.
- **Rules (instructions, not enforced, as in round 4):** no code execution, at most 20 tool calls per task, the answer in `solution.py`.
- **Knowledge:** `kel_ds1000_r4`, unchanged. Flags: verified examples, related examples, suggested candidate and the governor, all on (round 5's settings). Claims: Sonnet's own round-4 `survey_repo` output.
- **Runner:** `sonnet_r7.py` (`prepare`, `grade`). Kel's tools come through `kel_cli.py` with `KEL_CLI_ROUND=7`.

| Arm | What Sonnet gets |
|---|---|
| **AG7** | round 4's AG prompt, unchanged: Sonnet alone |
| **KH7** | the installed product with the hook: the MCP server instructions; Kel's tools as a command; `.stealth/claims.md`; and for each task, the hook's own text (`find_ways` run on the task before the agent starts, formatted by `packaging/npm/lib/hook.mjs`). No `plan_and_run`. The user's message is the task |

- **Time-matched:** Claude through subagents is not deterministic, so round 4's AG cannot stand in as the baseline. AG7 and KH7 run side by side: each wave launches the same batch numbers for both arms.
- **Hook content** (computed before this file): of 89 lookups, 19 resolved, 33 ambiguous, 37 no_match; 23 added no text; mean 758 characters.
- **Batches:** a batch whose subagent stops early (for example on a Claude usage limit) is infrastructure. Its tasks without a `solution.py` are reset and re-run in a `_retry` batch, as in round 4. A finished batch's missing `solution.py` is a failed answer.
- **Tokens:** subagents report one total per batch. It is split evenly over the batch's tasks (approximate, flagged).

## Analysis (`analyze_r7.py`)

- **Primary:** transfer tasks (82 pairs), **KH7 − AG7**.
  - Family-clustered bootstrap 95% CI (10,000 resamples) and exact McNemar.
  - **Confirmed if the CI excludes 0 and p < 0.05.**
- **Power, stated in advance:** round 4's Sonnet comparison on the same 82 tasks had a 95% CI about ±11.7 points wide. Only a gain of roughly **+12 points or more** can be confirmed here. A smaller true gain, such as the open models' +8.5, would most likely read "not confirmed". That result is not evidence of no effect; the effect size and CI are reported either way.
- **Secondary:**
  - controls (7 tasks) and the tasks AG7 solved that KH7 lost;
  - KH7 − AG7 where the hook delivered knowledge vs where it delivered nothing (descriptive);
  - hook content; Sonnet's own Kel calls;
  - approximate tokens per solved task;
  - cross-round, descriptive: AG7 vs round-4 Sonnet AG (noise), and KH7 vs round-4 Sonnet KP (hook vs tool).
- **Prediction:**
  - KH7 − AG7 > 0, smaller than the open models' +8.5 because Sonnet starts near 79%;
  - the gain concentrates where the hook delivered knowledge;
  - KH7 beats round-4 KP descriptively.

## Rules

- No code, prompt or parameter changes after the first scored episode.
- Infrastructure failures (batches cut off) are re-run; graded answers never are.
- Anything unexpected goes under Deviations, with the date.

## Deviations

(none yet)
