# DS-1000 round 4: does Kel help when it is used the way the product is used? (preregistered)

Written, and hashed with the design (`runs4/design.sha256`), **before any scored round-4 episode**. Smoke episodes (2 tasks, `runs4_smoke/`) were run only to check the pipeline and are never analysed.

## Why this round

Rounds 1–3 tested Kel's knowledge as a note that a script pasted into a single-shot prompt: the script called `find_ways` once and chose what to show. Production agents instead:
- get the MCP server's instructions in their system prompt;
- run the `plan_and_run` workflow themselves;
- call `find_ways` as a tool (as often as they like, with the repo facts from `.stealth/claims.md`);
- write their own `.stealth/procedures.md` and `.stealth/run.md`.

Round 4 tests that. It is also a dry run of SWE-bench arm KP, using the same shared code (`experiments/kel_product_arm.py`).

## Hypothesis

With Kel used the product's way (KP), models solve held-out transfer tasks more often than the same agent without Kel (AG).

## Sample and knowledge (fixed; reused from round 3)

- **Tasks:** round 3's test set, unchanged: 89 tasks, 82 transfer and 7 control (`runs4/design.json` = `runs3/design.json`). Round 3's analysis of these tasks is already known; round 4 asks a different question, about a different arm.
- **Knowledge:** `kel_ds1000_r4`, a clone of round 3's frozen `kel_ds1000_r3`, so the Goals, Procedures and evidence are identical. Two changes:
  - migration 124 applied;
  - the 100 verified examples moved onto the production provenance model with `verified_solutions.preserve` (`backfill_r4.py`), so `find_ways` returns them as `verified_solution`.

  `KNOWLEDGE_VERIFIED_EXAMPLES=true`. `main`'s `find_ways` has no `related_examples` channel (it stayed on a branch), so KP gets less than round 3's K note did.
- **Models:** `gemma-4-31B-it`, `gpt-oss-120b`, `deepseek-v3.2` (General Compute), temperature 0.

## Agent (identical for every arm; `run_r4.py`)

- **Loop:** `app.execution.coding_agent.Agent`, at most 20 tool calls, 2,000 output tokens per call.
- **Tools:** the standard coding tools (list, search, read, symbols, edit, create, delete, finish). There is no code execution.
- **Workspace:** a fresh temp directory holding `README.md` and `requirements.txt` (the grading environment's versions).
- **System prompt:** the DS-1000 instruction, rewritten for a tool-using agent: write only the solution-point code into `solution.py`.
- **Grading:** the unchanged DS-1000 harness grades `solution.py`. If the model never writes it, the last ```python block of its final message is graded instead; the same rule applies in every arm.

## Arms

| Arm | What differs |
|---|---|
| **AG** | nothing: the agent alone |
| **KN** | round 3's K note (the exact text, `runs3/notes_K.json`) after the problem, under round 1's "may or may not apply" intro. Where round 3 had no note, KN reuses AG's graded attempt |
| **KP** | Kel used the product's way. Details below |

**KP in detail:**
- the v1 MCP server instructions are appended to the system prompt;
- the user's message is the `plan_and_run` prompt, with the problem as its task, followed by fixed unattended-run adaptations: no user, no subagents, no code execution, read-only knowledge, `repo_claims` may be `@.stealth/claims.md`;
- tools: `find_ways` (real, in-process, judged with the repo facts) and `read_procedure_claims`;
- `.stealth/claims.md` is written once per model by the `survey_repo` workflow on the same workspace; survey tokens are charged to KP;
- `find_ways` replies are cut at 24,000 characters, claims reads at 8,000;
- KP never reuses AG, because the agent decides whether to use Kel.

## Analysis (`analyze_r4.py`)

- **Primary:** transfer tasks, 3 models pooled (about 246 pairs): **KP − AG** gold solve rate.
  - Family-clustered bootstrap 95% CI (10,000 resamples) and exact McNemar.
  - **Confirmed if the CI excludes 0 and p < 0.05.**
- **Secondary** (no correction unless stated):
  1. per model KP − AG, Holm-corrected;
  2. KN − AG, the pasted note in the same agent;
  3. KP − KN, product delivery vs pasted note;
  4. controls: KP − AG, and the tasks KP lost that AG solved (all tasks);
  5. KP usage, per model:
     - share of episodes that called `find_ways`;
     - outcomes (resolved / ambiguous / no_match);
     - share that received a verified solution, directly or on a candidate;
     - mean Kel calls;
  6. KP − AG restricted to episodes where KP called `find_ways`. This is descriptive, not causal: calling is the model's choice;
  7. cost: tokens and dollars per solved task (placeholder open-model prices, as in earlier rounds), survey included for KP;
  8. context: round 3's single-shot A and K rates on the same tasks (different scaffold, not paired in time).
- **Power:** as in round 3, the pooled analysis detects roughly 7–8 points.

## Rules

- No code, prompt or parameter changes after the first scored episode. Anything unexpected goes under **Deviations** below, with the date.
- Only call errors (`call_error`, infrastructure) are re-run; graded attempts never are.

## Deviations

(filled in during and after the run)
