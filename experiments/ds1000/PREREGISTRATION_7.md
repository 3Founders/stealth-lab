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

1. **2026-09-28, KH7's own `find_ways` calls are effectively unavailable (found after wave 1; prompts unchanged).**
   - This Claude Code session has a StealthLab MCP server configured that is not running (ECONNREFUSED); the subagents inherit it. The KH7 batch 00 agent reported that it looked for StealthLab there, found it disconnected, and solved the tasks without calling Kel. It did not use the `kel_cli.py` command its prompt offers, which works: checked by running the exact command.
   - No KH7 batch in wave 1 made a Kel command call.
   - **What it means:** the hook's text reached every KH7 task as designed, so the primary tests the hook. The "`find_ways` stays callable" part is in practice absent. Round 5's hook arms rarely used it anyway (gemma and gpt-oss 0 calls; deepseek 25 in 89 episodes).
   - The number of Kel command calls is reported.
   - Batch delivery: each subagent is told to read its batch prompt file and follow it; the file is byte-identical to the prepared prompt.
   - The configured StealthLab MCP server must stay down during round 7. If it came up, subagents could call it, and a server started with `backend/.env` would point at the hosted database.

## Results (2026-09-28)

`python sonnet_r7.py grade` then `python analyze_r7.py` → `runs7/report.json`. Design hash `6f5b5d51…` (sha256 over the LF-normalized bytes of `runs7/design.json`, `runs7/sonnet/hooks.json`, this file as preregistered, `sonnet_r7.py`, `analyze_r7.py`, `kel_cli.py`, `../kel_product_arm.py`, in that order). All 34 batches finished on the first attempt (no retries); 89/89 answers per arm.

### Primary: Sonnet, KH7 − AG7, transfer (82 pairs) — **NOT CONFIRMED**

- **Rates:** Sonnet alone solved 81.7%; with the hook, 82.9%. That is **+1.2 points**, 95% CI **[−5.0, +7.7]**, 6 gained / 5 lost, exact McNemar **p = 1.0**.
- **What it rules out:** the CI's upper bound (+7.7) is below the open models' round-5 gain (+8.5). So a gain as large as theirs is unlikely for Sonnet on these tasks. The CI is narrower than the ±12 planned, because only 11 pairs were discordant.

### Secondary

| Comparison | Δ | 95% CI | gained / lost | p |
|---|---|---|---|---|
| Controls (7 tasks) | 0.0 | [−42.9, +42.9] | 1 / 1 | 1.0 |
| Where the hook delivered knowledge (61 transfer tasks, descriptive) | +4.9 | [−3.7, +14.5] | 5 / 2 | 0.45 |
| Where the hook delivered nothing (21, descriptive) | −9.5 | [−25.0, 0.0] | 1 / 3 | 0.63 |

- **Tasks AG7 solved and KH7 lost** (all 89): 6 (200, 338, 57, 64, 812, 865).
- **Hook content:** 19 resolved, 33 ambiguous, 37 no_match; 37 carried related examples, 26 a suggested candidate, 12 procedures; 23 added no text; mean 758 characters.
- **Sonnet's own Kel calls: 0** (deviation 1). KH7 is the hook alone.
- **Tokens:** subagent totals, split per batch; no estimates were needed.
  - per solved task: AG7 15.0k, KH7 16.1k (+7%);
  - in all: 1.06M vs 1.16M.

### Cross-round (descriptive)

- **Sonnet's run-to-run noise:** AG7 vs round-4 Sonnet AG, 81.7% vs 79.3% (7 gained / 5 lost). Twelve of 82 outcomes flipped with nothing changed: Claude through subagents is not deterministic, unlike the open models.
- **KH7 vs round-4 Sonnet KP** (Kel as a tool with `plan_and_run`): 82.9% vs 82.9%.

### Against the prediction

- **"KH7 − AG7 > 0, smaller than +8.5":** the point estimate is +1.2, within noise.
- **"Gain concentrates where the hook delivered":** in direction only: +4.9 where knowledge arrived, −9.5 where it did not; neither is significant.
- **"KH7 beats round-4 KP":** no, equal.

### What it means

- On DS-1000, Sonnet already solves about 82% of these transfer tasks alone. Kel's knowledge, delivered by the hook, adds nothing measurable.
- The knowledge gain confirmed in round 5 is, on this benchmark, a gain for weaker (open) models.
- Whether a frontier model benefits on harder, repository-level work is the SWE-bench question and remains open.

### Limits

- One benchmark, near Sonnet's ceiling.
- Rules were instructions, not enforced.
- Kel's own tool was effectively unavailable (deviation 1); the hook's text is what was tested.
