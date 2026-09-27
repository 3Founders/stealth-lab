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

1. **2026-09-27, API key switched during AG5 (infrastructure only).**
   - AG5 ran at 3–12× round 4's per-episode latency. The cause: the default General Compute key had used its daily token quota (10M; 1,596 left), so most calls hit 429 and waited in the agent's silent retry (25 s × up to 4).
   - The limit is per key. After 103 AG5 attempts (1 call error) the runner was stopped and restarted with the second key from `backend/.env` (`general_compute_api_key1`, fresh quota), passed as `GENERAL_COMPUTE_API_KEY` in the process environment.
   - No code, prompt or parameter changed. Episodes in flight when it stopped were never graded or recorded, so they run again as not-yet-done.
   - Outputs are deterministic at temperature 0: 83 of the first 89 AG5 episodes reproduced round-4 AG byte for byte, code and token counts. The key cannot affect what is measured.
2. **2026-09-27, second key switch during KP5 (infrastructure only).**
   - At 17:12 UTC `general_compute_api_key1` had 144k of its 10M daily tokens left: KP5 costs 26–110k tokens per episode. The runner was stopped and restarted with a third key the user supplied (passed only in the process environment, never written to disk).
   - The stop was untidy. The Python child of the stopped shell survived and kept running KP5 on key1 until it was killed; then the orphaned launcher started KH on key1 and was killed too.
   - One KH episode (gemma) was graded on key1 in that window. It is kept as a graded attempt; nothing was duplicated (checked: no (task, model, arm) graded twice).
   - State at the switch: AG5 89/89 per model; KP5 47/47/48; KH 1.
   - No code, prompt or parameter changed. As in deviation 1, the key cannot affect outputs.
3. **2026-09-27, third key switch during KH (infrastructure only).**
   - At 18:31 UTC the second supplied key had 241k daily tokens left. The runner was stopped: launcher shells first, then the Python child, verified none left.
   - It was restarted with a fourth key the user supplied (process environment only).
   - State at the switch: AG5 and KP5 89/89 per model; KH 46/46/44 (gemma/gpt-oss/deepseek). No duplicates, no call errors in KP5 or KH.

## Results (2026-09-28)

`python analyze_r5.py` → `runs5/report.json`. Design hash `runs5/design.sha256` = `5ab8e265…` (sha256 of `runs5/design.json` + this file as preregistered + `run_r5.py` + `../kel_product_arm.py`; all four byte-identical to commit `2363b26`, which precedes every scored episode). All arms 89/89 per model; one call error in total (AG5, gpt-oss, re-run); no attempt graded twice.

### Primary: KH − AG5, transfer, 3 models pooled (246 pairs) — **CONFIRMED**

**+8.5 points** (52.0% → 60.6%), family-clustered 95% CI **[+2.7, +14.4]**, 35 gained / 14 lost, exact McNemar **p = 0.004**.

### Secondary

| Comparison (transfer) | Δ | 95% CI | gained / lost | p |
|---|---|---|---|---|
| KP5 − AG5 | +4.1 | [−2.3, +10.5] | 35 / 25 | 0.25 |
| KH − KP5 | +4.5 | [−0.8, +9.6] | 33 / 22 | 0.18 |

Per model (Holm over the 3 models within each comparison):

| Model | AG5 | KP5 | KH | KH − AG5 (Holm p) | KP5 − AG5 (Holm p) | KH − KP5 (Holm p) |
|---|---|---|---|---|---|---|
| gemma | 37.8% | 56.1% | 48.8% | +11.0 (0.15) | **+18.3 (0.012)** | −7.3 (0.48) |
| gpt-oss | 61.0% | 61.0% | 65.9% | +4.9 (0.42) | 0.0 (1.0) | +4.9 (0.48) |
| deepseek | 57.3% | 51.2% | 67.1% | +9.8 (0.19) | −6.1 (0.81) | **+15.9 (0.032)** |

- KH is positive for all three models; no single model is significant after Holm (the pooled test is the preregistered one).
- KP5 is model-dependent: large gain for gemma, nothing for gpt-oss (which never calls `find_ways`), a loss for deepseek.
- **Controls** (7 tasks × 3): KH − AG5 +9.5 [−19, +29], p 0.69; KP5 − AG5 −9.5, p 0.63. No harm detected; too few to say more.
- **Tasks AG5 solved and the arm lost** (all 89 tasks): KH 16, KP5 28.
- **Where the hook delivered anything** (a procedure, a suggestion or related examples; 184 of 246 transfer pairs, descriptive): KH − AG5 +11.4 [+5.1, +18.4], p 0.0005. Where it delivered nothing (62 pairs): 0.0. The gain sits where the knowledge arrived.

### Usage

- **Hook (KH), per model of 89:**
  - outcomes: 21–23 resolved, 36–37 ambiguous, 30–31 no_match;
  - content: 35–40 carried related examples, 28–29 a suggested candidate, 13–15 procedures;
  - 22–23 added no text;
  - context mean ≈ 830 characters, max 2,881;
  - the same task gave the same outcome across models on 86 of 89 (claims differ per model).
- **KP5 `find_ways`:**
  - gemma called on 89/89 episodes, deepseek on 89/89 (94 calls), gpt-oss on **0/89** (as in round 4: the description change did not fix adoption);
  - of gemma's and deepseek's calls, 86 carried related examples and 58 a suggested candidate;
  - 123 of 178 episodes received some verified solution.
- **KH agents' own `find_ways` calls:** gemma and gpt-oss 0; deepseek 25 calls in 24 episodes.
- **Governor:** no refusals and no cache hits in either arm (at most 3 calls in an episode; the loop breaker needs more than 3 identical requests).
- **Steps (mean):** gemma 3.8 / 6.6 / 3.3; gpt-oss 5.4 / 8.7 / 5.7; deepseek 10.2 / 14.8 / 11.0 (AG5 / KP5 / KH). The `plan_and_run` ceremony is still what costs KP5 its steps; KH adds almost none.

### Tokens and cost per solved task (all 89 tasks; open-model placeholder prices; Kel's own server-side work is not counted in any arm)

| Model | AG5 | KP5 | KH |
|---|---|---|---|
| gemma | 30.7k ($0.0036) | 76.2k ($0.0082) | **21.8k ($0.0025)** |
| gpt-oss | 13.3k ($0.0030) | 53.8k ($0.0097) | 19.6k ($0.0040) |
| deepseek | 43.2k ($0.0125) | 189.8k ($0.0540) | 59.3k ($0.0170) |

KH costs 0.7–1.5× the agent alone per solved task. KP5 costs 2.5–4.4×.

### Cross-round (descriptive only; same tasks, models, agent)

- **AG5 vs round-4 AG:** 52.0% vs 52.0%, 1 gained / 1 lost. The providers are effectively deterministic at temperature 0, so the "time-matched baseline" is a near-exact replay; the primary comparison carries almost no sampling noise from the agent side.
- **KH vs round-4 KN:** 60.6% vs 60.6% (19/19 swapped). The hook delivers what pasting the note did.
- **KP5 vs round-4 KP:** 56.1% vs 55.3%. The fixes did not improve the product's way overall.

### Against the prediction

- **"KH recovers at least KN's gain":** yes, +8.5 vs +8.5.
- **"KP5 improves on round-4 KP":** no (+0.8).
  - The diagnosis holds where it can be tested: deterministic delivery (the hook) fixes adoption, and small context fixes ceremony.
  - Within the agent-driven path, the fixes help a model that calls the tool and uses short answers (gemma, +18) and hurt one that turns the extra material into longer runs (deepseek, −6, 4.4× the tokens).
  - They do nothing for a model that never calls the tool (gpt-oss).

### Limits

- **Scope:** one benchmark (DS-1000 transfer variants) and three open models; Sonnet was not run in round 5.
- **Flags not separated:** all three knowledge flags were on in every Kel arm, so their separate contributions are not measured. The confirmed claim is the hook together with the flags.
- **Cost undercounted:** Kel's server-side LLM work for `find_ways` is not in the cost figures.
