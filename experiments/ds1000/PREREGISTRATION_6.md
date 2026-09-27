# DS-1000 round 6: which of round 5's knowledge flags does the hook need? (preregistered)

Written before any scored round-6 episode, and hashed with the runner and analysis (`runs6/design.sha256`). Smoke episodes (`runs6_smoke/`: tasks 207 and 208, deepseek, KH0) checked the pipeline and are never analysed.

## Why

Round 5 ([PREREGISTRATION_5.md](PREREGISTRATION_5.md)) confirmed the Claude Code knowledge hook (KH): +8.5 points over the agent alone, [+2.7, +14.4], p = 0.004. Every Kel arm ran with two new flags on, so their separate contributions were not measured:
- `KNOWLEDGE_RELATED_EXAMPLES`: up to 3 verified solutions of judged-similar Goals on every answer;
- `KNOWLEDGE_SUGGESTED_CANDIDATE`: one named candidate, with its verified solution, on an ambiguous answer.

Turning them on in production changes what **every** MCP client receives. The product decision needs to know whether the hook's gain depends on them. In round 5, clients without the hook (KP5) gained nothing overall from the fixes, and deepseek ran worse and used 4.4× the tokens.

## Design

Everything is round-5 KH exactly: tasks (89: 82 transfer + 7 control), models (gemma, gpt-oss, deepseek), agent, 20-call budget, temperature 0, workspace, grading, knowledge (`kel_ds1000_r4`, unchanged), claims, system prompt, the hook (`kel_product_arm.hook_context`, the same code as `run_r5.hook_context`), `KNOWLEDGE_VERIFIED_EXAMPLES` on, governor on. Only the two flags differ. Runner: `run_r6.py` (flags set per process from the arm name before any app module loads).

| Arm | Related examples | Suggested candidate | What it answers |
|---|---|---|---|
| **KH** (round 5, reused) | on | on | the comparator |
| **KH0** | off | off | the hook with round 4's delivery: does the hook need the flags at all? |
| **KHnR** | off | on | what related examples add |
| **KHnS** | on | off | what the suggested candidate adds |
| **KHr** | on | on | round-5 KH re-run: a replication of round 5's primary, and outcome noise from Kel alone |

**Why round-5 KH is reused as the comparator:**
- The agent side is deterministic. Round-5 AG5 matched round-4 AG's outcome on 244 of 246 transfer pairs (1 gained, 1 lost).
- Kel's side is mostly reproducible. Before this file was written, `check_hook_replay.py` re-ran all 267 of round-5 KH's hook lookups with round 5's settings (no agent): 234 of 267 identical on every recorded field (outcome 260, related-example count 238, suggested 261, procedures 264, context length 235). `runs6/hook_replay.jsonl`.
- The differences are Kel's own run-to-run variation (its LLM judge), which a fresh KH arm would have too. It is noise on both sides, not a bias. KHr measures its effect on outcomes directly.

**Order:** KH0, KHnR, KHnS, then KHr, one arm at a time (the attempts file is appended by one process at a time).

## Analysis (`analyze_r6.py`)

- **Primary:** transfer tasks, 3 models pooled (246 pairs): **KH − KH0** (do the flags add to the hook?).
  - Family-clustered bootstrap 95% CI (10,000 resamples) and exact McNemar.
  - **Confirmed if the CI excludes 0 and p < 0.05.**
- **Secondary:**
  - KH − KHnR and KH − KHnS (each flag's contribution with the other on); KHnR − KH0 and KHnS − KH0 (each flag alone);
  - KH0 − AG5 (the hook without the flags vs the agent alone);
  - every comparison per model, with Holm correction;
  - controls: KH − KH0 and KH0 − AG5;
  - **replication:** KHr − AG5 (round 5's primary, re-run);
  - **noise:** KHr − KH and its flip rate. Read every ablation difference against it;
  - descriptive: KH − KH0 where the hook's text differed between the arms vs where it was the same;
  - hook content per arm; tokens and $ per solved task.
- **Prediction:**
  - related examples carry most of the gain: they are the only knowledge the hook adds on `no_match`, and on 37–40 of 89 round-5 lookups;
  - KH − KHnR > 0; KH − KHnS small;
  - KH0 still beats AG5, but by less than round 5's +8.5;
  - KHr − AG5 near +8.5.

## What the result decides

| Result | Production decision |
|---|---|
| **Primary confirmed** | the flags are part of the hook's gain. Turn them on with the hook. Consider restricting them to the hook's lookup if clients without the hook stay flat. |
| **Not confirmed, but KH0 ≈ KH** | the hook is what matters; the flags can stay off for all clients, avoiding KP5's extra cost. |

## Rules

- No code, prompt or parameter changes after the first scored episode.
- Call errors (infrastructure) are re-run; graded attempts never are.
- Anything unexpected goes under Deviations, with the date.

## Deviations

1. **2026-09-28, how to verify the design hash (before any scored episode).** `runs6/design.sha256` = `327e846c…`. It is sha256 over these bytes, concatenated:
   - `runs6/design.json` (identical to `runs5/design.json`);
   - `PREREGISTRATION_6.md`, `run_r6.py` and `analyze_r6.py` exactly as committed in `b31f85d` (LF line endings);
   - `../kel_product_arm.py` as committed in `b31f85d`, but with CRLF line endings (its Windows checkout when hashed).
   A later rebase re-checked-out the files with `core.autocrlf=true`, so the working copies no longer hash to the same value. The content is unchanged; checked against the committed blobs.

2. **2026-09-28, paused for API quota (infrastructure only).** At 20:30 UTC every General Compute key had used its daily token quota. The runner was stopped (launcher, then Python; none left) so that rate-limited episodes would not be graded after a truncated run.
   - State: KH0 89/89 per model; KHnR 24/23/21 (gemma/gpt-oss/deepseek).
   - No duplicates; no graded episode ended on an API error.
   - It resumes with the same command when a quota resets. Nothing else changes.
