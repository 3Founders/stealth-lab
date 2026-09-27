# DS-1000 round 2: does "Procedure + verified code" help? (confirmation, preregistered)

Written, and hashed with the round-2 sample (`runs2/design.sha256`), **before any round-2 model run and before the round-1 Addendum 1 results were seen.** Everything not stated here is as in [PREREGISTRATION.md](PREREGISTRATION.md) (grading, prompts, models, safety, isolation, analysis methods).

## Hypothesis

Round 1 found no benefit from Kel's Procedures, but a benefit from plain retrieval of verified code (arm E, exploratory). Hypothesis: **if Kel returns the verified code together with the Procedure, models solve related tasks more often than without notes.**

## Sample (`design.py --round 2 --transfer 60`, seed `ds1000-kel-v2`)

- Only families never used in round 1 (fit, test or control).
- Fit (imported into the SAME Kel database, next to round 1's 60 fit problems, which act as distractors): 60 originals of families with at least one variant. No new distractors.
- Test (never imported): up to 2 variants of each of those families (transfer), plus 20 originals of otherwise-unused families (control).
- Kel learns exactly as in round 1:
  - Goal naming, import, fit attempts by all 4 models;
  - extraction with up to 5 attempts;
  - validation runs, evidence, observations;
  - one joint refit over rounds 1 and 2.
  After this, nothing changes.

## Arms (test)

| Arm | Notes |
|---|---|
| A | none |
| B | Kel's `find_ways` result (Procedure only), as in round 1 |
| **Bc** | B's retrieval unchanged, plus the verified code of the fit solution that Procedure was extracted from |
| Cc | oracle: the task's own family-origin Procedure plus its verified code (transfer only) |
| E | plain RAG: BM25 top-1 over ALL fit problems with a verified solution (rounds 1 and 2), with its code |

- **Models:** the 3 open models run every arm; Sonnet runs A and Bc.
- **Reuse:** a prompt identical to arm A's reuses arm A's result.

## Analysis

- **Primary:** transfer tasks, per open model, Δ = solve rate(Bc) − solve rate(A).
  - Exact McNemar, Holm across the 3 models, α = 0.05.
  - Family-clustered bootstrap confidence interval (10,000 resamples).
  - Confirmed if at least one model is significant after Holm **and** the pooled Δ is greater than 0 with its 95% confidence interval excluding 0.
- **Secondary** (confidence intervals as above, no correction):
  1. pooled: Bc−A, Bc−B, Bc−E, B−A, E−A, Cc−A, Cc−Bc;
  2. Sonnet Bc−A;
  3. control tasks: Bc−A, and the number of tasks solved in A but lost in Bc;
  4. by variant type;
  5. routing with Bc attempts (F) vs routing with A attempts (F0) vs Sonnet-A, at targets 0.5–0.9.
- **Power:** about 96 transfer tasks × 3 models. Per model, this detects roughly a 10-point effect; pooled, roughly 6 points.

## Deviations

(filled in during and after the run)
1. **Added arm Bw (post hoc, exploratory; added before any round-2 test run, after the round-1 Addendum 1 results).** Round 1 showed that the oracle Procedure plus code gave nothing (Cc−C = 0.0), while plain RAG (a past problem plus its code) gave +8.3. That points to the *format*, not the code. **Bw** uses Kel's retrieval unchanged (the fit problem whose Procedure `find_ways` returned), rendered **exactly like arm E**: the past problem's text (first 1,200 characters) plus its verified solution, with no Procedure text.
   - Bw−E compares retrieval (Kel vs BM25) with the format held constant.
   - Bw−Bc compares the format (worked example vs Procedure plus code) with retrieval held constant.
   - The primary endpoint (Bc−A) is unchanged.
2. **Shared Goal across rounds.** Round-2 fit problem 135 got the same generated Goal name as round-1 fit problem 177 ("Retrieve rows containing the maximum value within each group using pandas"), so Kel's exact-name identity reused the round-1 Goal (59 new edges for 60 problems). This is product behaviour, left as is.
3. **Recommender refit re-run with longer chains.** The joint refit after round 2's learning had max r-hat 1.11 (0 divergences), above the runbook's 1.05 check. It was re-run once with 4 chains, 1,500 warm-up and 1,000 samples (`KEL_REFIT_LONG=1`) before any routing evaluation. Only routing (secondary 5) uses these parameters. Result: max r-hat 1.022, 1 divergence in 4,000 draws (122 Goals, 84 Procedures, 732 observations).
4. **Sonnet usage limit.** All 16 round-2 arm-A Sonnet batches hit the account's session limit mid-run (3 had already written their output). They were relaunched after the reset with identical input files. Only complete answers are recorded; no batch was re-sampled after it had been graded.

## Result against the preregistered rule

- **Per model, Bc − A:**

  | Model | Change | Holm p |
  |---|---|---|
  | gemma | +3.8 | 0.44 |
  | gpt-oss | +1.0 | 1.0 |
  | deepseek | +8.7 | 0.0675 |

  **No model is significant after Holm.**
- **Pooled Bc − A:** +4.5 [+1.2, +8.3], p = 0.0066. The pooled condition is met.
- **Verdict under the preregistered rule, which needed both: NOT CONFIRMED.** The pooled effect is positive, and its confidence interval excludes 0.
