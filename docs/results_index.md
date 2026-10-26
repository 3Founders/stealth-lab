# Results index: everything measured so far (2026-10-24)

Every result we have, grouped by area, with superseded numbers replaced by their corrected versions and a link to the
source. Written by reading each source and recomputing the series costs from the raw result files.

**One finding changes the picture, and it was not in any summary doc before this one:** on 25 real repo issues, every
acceptance rule we tried let through more wrong fixes than our bar allowed, including the product's current default
(section C, row C5).

---

## A. Benchmarks (single tasks, not real repos)

Sources: [findings.md](findings.md), [experiments/ds1000/PREREGISTRATION_5.md](../experiments/ds1000/PREREGISTRATION_5.md).

| # | Result | Numbers | Status |
|---|---|---|---|
| A1 | **Knowledge, delivered by the hook, helps open-model agents** | 52.0% → 60.6%, **+8.5 points** (CI +2.7 to +14.4), p = 0.004, 246 task–model pairs | Confirmed, preregistered |
| A2 | The same, replicated | **+8.9** (CI +3.9 to +14.0), p = 0.003 | Confirmed |
| A3 | The gain comes from the knowledge itself | +11.4 where knowledge arrived (p = 0.0005); 0.0 where none did | Supporting |
| A4 | Gain by model | gemma +11.0, deepseek +9.8, gpt-oss +4.9 | Measured |
| A5 | The hook's token cost | 0.7–1.5× the agent's own tokens per solved task | Measured |
| A6 | **Knowledge doesn't help Sonnet** (public-library tasks) | +1.2 (CI −5.0 to +7.7); 81.7% → 82.9% | Not shown |
| A7 | Letting the model decide when to ask Kel | +3.3 (p = 0.37), +4.1 (p = 0.25); one model never asked | Not confirmed |
| A8 | Extra knowledge flags | +2.8 on top of the hook (p = 0.19) | Not confirmed |
| A9 | The plan_and_run workflow on small tasks | 2.5–4.4× tokens; deepseek −6.1 points | Hurts |
| A10 | Confident snippets can mislead strong models | The hook fixed 6 of Sonnet's failures and broke 5 of its successes | Measured |
| A11 | **Routing to open models is cheaper** (single-shot) | 36–60% cheaper than Sonnet at equal or better accuracy (DS-1000 rounds 1–2; BigCodeBench ~37%) | Replicated |
| A12 | Routing with a perfect check (simulation) | 89–93% solved vs Sonnet's 82%, 45–55% cheaper | Simulation only |
| A13 | Routing with **no** check | 66% vs Sonnet's 82% | Simulation |
| A14 | Run-to-run noise | Sonnet: 12 of 82 outcomes flipped between identical runs | Measured |

## B. The first product A/B: Claude Code ± StealthLab on 205 benchmark tasks

Source: [HANDOFF_abtest_2026-10-11.md](HANDOFF_abtest_2026-10-11.md), section 12 (final, 2026-10-14). Arm A = Opus
alone; arm C = StealthLab on main as of `11b7e67`.

| # | Result | Numbers |
|---|---|---|
| B1 | **Code tasks (150)** | A solved 140 for $17.20; **C solved 137 for $8.82: −49% cost, −3 solved** |
| B2 | All 205 (including web pages) | A 175 for $32.37; C 176 for $24.13: −25% cost |
| B3 | By suite (cost saved) | BoolQ −86%, IFEval −68%, MultiPL-E JS −68%, BIRD −59%, DS-1000 −51%, OpenAPI −14%; pages about equal, because they go to Opus |
| B4 | **Where quality was lost** | The 3-task code gap = wrong cheap answers that passed every check (DS-1000 ×3, BIRD ×2), partly offset by OpenAPI +2 |
| B5 | Latency, code tasks | p50/p95: A 30/88 s, C 61/179 s (**about 2× at the median**) |
| B6 | Test-first (a cheaper model writes acceptance tests) | Wrong cheap answers cut from 4–6 per 30 tasks to about 1–2 |
| B7 | Sonnet as test writer | Same verdicts as Opus, about 69% cheaper |
| B8 | Sonnet as a cheap model | About as expensive as Opus on small tasks, so it was removed |
| B9 | The ambiguity flag | Fixed 1 of 7 misses; wrongly flagged 1 of 9 controls |

**Superseded:** the earlier phase 1 figure (128 tasks: 114 vs 120 solved, −38%) is replaced by B1/B2.

## C. Real repo issues: the cheap-model and acceptance survey

SWE-rebench, 25 valid issues, after the grader fix. Sources: `experiments/abtest/results/chaitanya-2026-10-16/survey/`
(stage 3 and stage 4 files, `rules-r1-2026-10-17.txt`), [acceptance_rules_preregistered_2026-10-16.md](acceptance_rules_preregistered_2026-10-16.md).

| # | Result | Numbers | Status |
|---|---|---|---|
| C1 | **Cheap model driven by lean Claude Code** (glm-5.3-flash) | Solved **14/25** without the context pack; **12/25** with it; about $0.09–0.11 per issue | Measured |
| C2 | Our own agent loop driving the same model | 8/25, against lean Claude Code's 14/24 | Measured, after the grader fix. It reverses an earlier claim |
| C3 | Locating the right code | Top-3 correct 96%; 39 of 44 wrong answers had touched the right file | Measured |
| C4 | Tests written before the fix | Valid 41% → 50% (70% for single-file fixes) | Near its ceiling |
| C5 | **Acceptance: no rule met the bar** (≤2 wrong of 25) | Best: 4 wrong of 25 (rules 7 and 15, ~44% saved). **The product's current default, `E Rs`** (existing tests + Sonnet reviewer; `packaging/npm/lib/dispatch.mjs`), is closest to rule 11: **8 wrong of 25 (32%), ~76% saved** | Measured. **This is our weakest point** |
| C6 | Reviewers judging diffs | Sonnet and gpt-oss said "fixes" on 15/28 and 18/28 wrong answers; even an Opus review approved 13/28 wrong | Measured |
| C7 | Grader bugs found and fixed | 42/107 answers had been mis-scored; 6/32 issues were invalid | Fixed; the numbers above are corrected |

Notes:
- **"Saved" in C5 is an estimate:** Opus is assumed to cost $1.13 per task and always to be right; the test writer's
  and reviewer's own costs are not included.
- **Rule letters:**
  - E: the project's existing tests still pass;
  - S: the answer changes source code;
  - F/X: focused, no exception-swallowing;
  - A: two answers agree;
  - C: passes the other answer's tests;
  - R / Rs / Ro: the reviewer on gpt-oss / Sonnet / Opus says "fixes".

## D. Real repo issues: the long-horizon series v4–v7

1 issue each from jsonargparse and sqlglot; arms A (plain Claude Code), C (StealthLab, `.kell` carried over), C0
(StealthLab, `.kell` wiped). Source: `experiments/abtest/results/chaitanya-2026-10-16/series/`.

**Costs are corrected:** Claude isn't charged for a turn a cheap model answered (the harness fix in `4ace441`). The
stored `*-report.txt` files for **v7 sqlglot** (C $2.40, C0 $3.55) and **v6 jsonargparse C0** ($0.46) still show the
old, inflated figures. The corrected figures below are recomputed from each results row's `turn_log`. Results files
also contain rows from the other repo; only rows for the named issue are used.

| Run | jsonargparse: A / C / C0 | sqlglot: A / C / C0 | Notes |
|---|---|---|---|
| v4 | $0.76 / $0.89 / $0.83, all solved | $1.08 / $1.56 ✗ / $1.39 ✗ | C and C0 failed sqlglot |
| v5 | $0.75 / $1.12 / $0.74, all solved | $1.62 / $3.11 / $1.10, all solved | Right answers rejected by our own written test |
| v6 | $0.43 / $0.55 / **$0.26**, all solved | $1.24 / $1.52 ✗ / $1.38 | 5 right answers wrongly rejected (check bug) |
| **v7** | **$0.45 / $0.31 / $0.17**, all solved | **$1.00 / $1.87 / $2.36**, all solved | First run with cheap fixes accepted |

(✗ = not solved.)

| # | Result |
|---|---|
| D1 | **v7 jsonargparse: C 32% cheaper, C0 63% cheaper than A,** the same result in all three |
| D2 | **v7 sqlglot: C 88% more expensive, C0 137% more.** A correct cheap fix was thrown away at the time limit (since fixed), and minimax spiralled for about $0.90 per failed try (two causes, both fixed) |
| D3 | v7 cheap answers graded on hidden tests: 2 accepted, **both correct; 0 wrong applied.** gpt-oss's wrong patch was caught. 4 of 4 cheap explanations correct |
| D4 | v5 sqlglot: a wrong cheap answer passed the project's own tests, so tests alone aren't enough |
| D5 | **C vs C0 (memory) shows no benefit yet:** C0 was cheaper on jsonargparse in v6 and v7. Only 1 issue per repo |
| D6 | The fixes after v7 (hand-in at the limit, the worktree file, the test instruction) are **not yet re-measured** |

## E. Repo memory (`.kell`)

Sources: [survey_eval_2026-10.md](survey_eval_2026-10.md), [stealth_status_results.md](stealth_status_results.md).

| # | Result | Numbers |
|---|---|---|
| E1 | **Cited facts are accurate** | 97.8% on the development set; 94.4% → 98.6% and 96.9% on held-out repos (hand-checked, one annotator) |
| E2 | Detecting the units of a repo | 0.90–1.00 precision |
| E3 | Memory stays small in big repos | A 500-package monorepo needs about the same context as a single package (0.97×) |
| E4 | Computed status (verified / open / failed / contested / expired) | Works on a toy repo and on our own collab plan. A real edit made 2 nodes expire. Tests only, not use at scale |

## F. Latency

Sources: [latency_routing_results.md](latency_routing_results.md), [HANDOFF_abtest_2026-10-11.md](HANDOFF_abtest_2026-10-11.md).

| # | Result | Numbers |
|---|---|---|
| F1 | Routing time, warm, dispatched prompts | p50 **6.3 s**, p95 18.7 s (localhost) |
| F2 | What routing time goes to | The brief 41%, locate 44%, triage + lookup + gate about 16% |
| F3 | The cold start | 13–33 s; the first triage after a server start timed out |
| F4 | Routing's share of the extra time | About 27% of C's added median (~23 s) |
| F5 | The whole task (benchmarks) | C median 42–61 s vs A 15–37 s (B5) |

## G. Product checks (functional, not measured at scale)

- **A real session works end to end.** In a scripted run, gpt-oss fixed a bug ("add is broken") in 41 s for
  $0.0001. A follow-up ("make it reject strings") was handled, `undo` worked, and "thanks" wasn't dispatched
  (handoff section 11).
- **The brief's parser** scored 36/36 on its test set.
- **The company onboarding flow:** a local end-to-end check against Postgres 17 with one real provider call passed
  (commit `534a148`). It hasn't run on staging or in a real Cursor.

---

## What this means, in one view

| Area | Evidence today |
|---|---|
| **Reduce** | **Strong on benchmarks** (−49% on 150 code tasks, −3 solved). **Mixed on real repos** (one issue cheaper, one more expensive) |
| **Tests decide** | **Weak on real repos.** The current default let 8 of 25 wrong fixes through in the survey, even though v7 applied 0 wrong out of 2 |
| **Recall** | **Knowledge helps open models on benchmarks** (+8.5, confirmed). On the user's own repo: not shown; C vs C0 shows nothing yet |
| **Reuse** | Not measured |
| **Route learning** | Not measured |
| **Memory accuracy** | Strong: ~97% of facts correct |
| **Latency** | About 2× slower; routing is about a quarter of the extra time |

**For the series10 run** ([series10_prereg.md](series10_prereg.md), [series10_thresholds.md](series10_thresholds.md)):
the acceptance problem (C5) is the biggest risk to "Tests decide" and to quality. The run measures it with the default
rule. If wrong fixes come through at anything like the survey's rate, Reduce will look good while quality drops.

**Still not measured anywhere:**
- real developers' prompts and multi-turn sessions;
- facts going stale as code evolves (series issues each start at their own commit);
- large repos, languages other than Python, months of use;
- Cursor, teams sharing `.kell`, and the company gateway in real use;
- whether developers keep it installed.
