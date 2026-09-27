# DS-1000 round 5: do the round-4 fixes recover the knowledge the product lost? (preregistered)

Written before any scored round-5 episode. Smoke episodes (2 tasks, `runs5_smoke/`) are never analysed.

## Why

Round 4 ([PREREGISTRATION_4.md](PREREGISTRATION_4.md)) showed the knowledge works when it reaches the agent: the pasted round-3 note (KN) scored +8.5 over the agent alone, p = 0.003. The product's workflow (KP) lost most of that. The diagnosis, from per-task traces:
1. **Adoption:** gpt-oss never called `find_ways`; Sonnet skipped it on 22 of 89 tasks.
2. **Nothing usable returned:** a variant of a known task is correctly a different Goal, so `find_ways` answered `ambiguous` or `no_match` and returned nothing on about half the requests where Kel held the neighbouring verified solution. KN carried related examples in 54 of 82 notes.
3. **Ambiguous answers misused:** weaker agents chose badly between candidates.
4. **Ceremony:** `procedures.md` and `run.md` for a 5-line task (DeepSeek: 16 steps vs 10, 4.5× tokens).

## The fixes under test (on `main`)

- `KNOWLEDGE_RELATED_EXAMPLES`: up to 3 verified solutions of judged-similar Goals on **every** outcome, labelled "not verified to apply".
- `KNOWLEDGE_SUGGESTED_CANDIDATE`: an ambiguous answer names one candidate, with its verified solution.
- `plan_and_run`:
  - use `suggested` and `related_examples`;
  - don't re-ask;
  - small-task fast path, skipping `procedures.md` and `run.md` when the way has 1–2 steps.
- `find_ways` description: call once, before writing code; when not to call.
- Governor, keyed on the MCP session:
  - identical requests are served from cache;
  - the same request more than 3 times in 10 minutes is refused;
  - 30 calls per 10 minutes;
  - requests under 3 words are refused.
- **Claude Code knowledge hook** (`stealthlab-mcp hook-prompt`, installed by `stealthlab-mcp install`): each task-like prompt is looked up with `find_ways` **before** the agent starts, and the knowledge is added to its context. The model no longer decides whether knowledge arrives.

## Design

- **Tasks, models, agent, budget, workspace, grading:** identical to round 4 (89 tasks: 82 transfer + 7 control; gemma, gpt-oss, deepseek; 20 tool calls; temperature 0).
- **Knowledge:** `kel_ds1000_r4`, unchanged.
- **Flags:**
  - `KNOWLEDGE_VERIFIED_EXAMPLES`, `KNOWLEDGE_RELATED_EXAMPLES` and `KNOWLEDGE_SUGGESTED_CANDIDATE` on;
  - governor on, with each episode its own MCP session and the hook's lookup a separate session.
- **Runner:** `run_r5.py`.

| Arm | What it is |
|---|---|
| **AG5** | the agent alone: a fresh, time-matched baseline |
| **KP5** | as round-4 KP (MCP instructions in the system prompt, `plan_and_run` as the user message, `find_ways` as a tool), with the fixes |
| **KH** | the installed product with the hook. `find_ways` is run on the task prompt before the agent starts, and the hook's own text (`hook_format.mjs` = `packaging/npm/lib/hook.mjs`) is appended to the prompt. The MCP instructions are in the system prompt and `find_ways` stays callable. The user message is just the task (no `plan_and_run`) |

## Analysis

- **Primary:** transfer tasks, 3 models pooled (246 pairs): **KH − AG5**.
  - Family-clustered bootstrap 95% CI (10,000 resamples) and exact McNemar.
  - **Confirmed if the CI excludes 0 and p < 0.05.**
- **Secondary:**
  - KP5 − AG5; KH − KP5; per model with Holm correction; controls (KH − AG5, and tasks lost);
  - usage: `find_ways` calls, outcomes, related examples and suggestions received, hook context size, governor refusals and cache hits;
  - tokens per solved task;
  - cross-round, descriptive only: KH vs round-4 KN, KP5 vs round-4 KP, AG5 vs round-4 AG (noise).
- **Prediction:**
  - KH recovers at least KN's gain (about +8 over AG);
  - KP5 improves on round-4 KP;
  - if not, the diagnosis is incomplete.

## Rules

- No code, prompt or parameter changes after the first scored episode.
- Call errors (infrastructure) are re-run; graded attempts never are.
- Anything unexpected goes under Deviations, with the date.

## Deviations

(none yet)
