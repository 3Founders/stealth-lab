# LLM judges vs execution checks, and how to claim "equivalent quality at lower cost"

Status: literature review, 2026-10-09. Prepared for `docs/testingplan.md`.

How this was done. Each claim below was checked against the paper's arXiv / ACL / venue page (abstract level; I did not
read full PDFs unless a table is named). Where a number is quoted, the version or table is given if I could see it.
"Abstract-level" means I read the abstract page, not the body. "Secondary" means I only saw a search-engine summary or a
third-party page; those claims are marked. Anything I could not confirm says *not verified*. The fetch tool summarised
pages rather than returning raw text, so quotes are paraphrased unless in quotation marks.

---

## 1. Summary and recommendation

**Question.** Is "no model judges correctness; correctness is executable" (testingplan.md section 3) justified?

**Recommendation: keep the rule for Core, with three changes.**

1. **Keep it for every Core task type** (SQL, dbt, PySpark, data analysis with exact answers, tests). Execution-based checks
   have a stable, auditable definition of correct, and the judge literature shows judges are noisy, biased and
   weakest exactly on correctness (finding 1-3 below).
2. **But "executable" does not mean "correct".** Execution checkers have large, documented false-positive and
   false-negative rates (findings 4-6). So the rule should be "executable checker, audited by humans on a sample",
   not "executable checker, trusted". This is the larger risk in the current plan.
3. **Relax it only in a separate, labelled section** for outputs that have no executable oracle (explanations,
   analysis narratives, code-review comments, SQL that is correct but the checker is suspect). There a judge is
   acceptable only if: it is from a different model family than every arm, it is calibrated against a human-labelled
   sample with chance-corrected agreement (kappa), pair order is swapped, repeated runs are averaged, and
   the results are reported as a separate secondary metric, never merged into CES.
   A judge may also be used as a *triage* tool (flag likely checker errors for human review), as ELT-Bench-Verified
   did, but the human decides.

**Strongest findings behind this (detail in sections 2 and 3):**

| # | Finding | Source |
|---|---|---|
| 1 | Even strong judges do barely better than chance on objectively-labelled hard pairs (knowledge, reasoning, math, coding); "many strong models, such as GPT-4o, perform just slightly better than random guessing". | JudgeBench [R4] |
| 2 | Coding judges swing with presentation: response order changes accuracy substantially, and (ACL version) variable naming and misleading comments matter; "significant randomness" across 26 judges. | CodeJudgeBench [R10] |
| 3 | Judges are not repeatable: "low intra-rater reliability" across runs, "at worst close to arbitrary". | Rating Roulette [R9] |
| 4 | Execution checks lie too: 52.8% of BIRD Mini-Dev and 62.8% of Spider 2.0-Snow audited items had annotation errors; fixing a BIRD Dev subset moved agents by -7% to +31% relative and ranks by -9 to +9. | Pervasive Annotation Errors [R14] |
| 5 | In ELT-Bench, of 81 failed transformation tasks, 67 (82.7%) had at least one benchmark-attributable error; a third of 660 unmatched columns were benchmark faults, 156 of them evaluation false positives. Correcting raised SRDT from 22.66% to 32.51% (Sonnet 4.5, SWE-Agent). | ELT-Bench-Verified [R15] |
| 6 | Weak tests inflate agent scores: 7.8% of SWE-bench Verified plausible patches pass the benchmark tests but fail the developer tests; resolution rates inflated by 6.2 points absolute (PatchDiff). HumanEval+ (about 80x more tests) lowers pass@k by up to 19.3-28.9% and reorders models. | [R16], [R18] |
| 7 | The one SQL-specific judge result that argues *for* judges: FLEX raises Cohen's kappa with experts from 62 (EX) to 87.04 (v4 abstract). This supports a calibrated judge as a *checker auditor*, not a replacement, and it comes from the authors' own validation. | FLEX [R13] |
| 8 | On the statistics side, a 150-task suite is small: paired-test resolution is often insufficient and the common unpaired power shortcut underestimates required N by about 2x in close comparisons. | Resolution Diagnostics [R22] |

---

## 2. Section A: judges vs execution

### A1. Agreement with humans looks good on open-ended chat, not on correctness

- MT-Bench / Chatbot Arena: strong LLM judges reach "over 80% agreement, the same level of agreement between humans"
  (abstract). The same abstract names "position, verbosity, and self-enhancement biases, as well as limited reasoning
  ability". [R1] Note this is about preference on open-ended chat, not about code correctness.
- JudgeBench builds response pairs labelled by *objective correctness* (knowledge, reasoning, math, coding). Agreement-with-preference
  benchmarks "can be a poor signal of factual and logical correctness", and many strong judges are only slightly above chance. [R4]
  I did not see per-model numbers (abstract only): *per-model accuracies not verified*.
- Judging the Judges (13 judges, 9 exam-takers): only the best and largest judges reach reasonable alignment with humans, still
  "well short of inter-human agreement"; scores can differ from human scores by up to 5 points; judges are lenient and
  sensitive to prompt complexity and length. They argue percent agreement is misleading (high agreement can hide very different
  scores) and recommend chance-corrected metrics. [R5] The abstract does not give kappa values.

### A2. Known biases

- **Position:** judge ranking "can be easily hacked" by reordering; in one example Vicuna-13B beat ChatGPT on 66 of 80 queries when ChatGPT was the evaluator. Mitigations: balanced position calibration (aggregate over orderings), multiple evidence, human-in-the-loop flagging. [R6]
  Position bias is "not due to random chance", varies by judge and task, and is strongly affected by the quality gap between
  solutions (15 judges, MTBench and DevBench, 150k+ instances). [R3] Practical implication: bias is largest when the two
  systems are close, which is exactly the "equivalence" regime we care about.
- **Verbosity / length:** AlpacaEval is known to favour longer outputs; regression-based length control raised Spearman correlation with
  Chatbot Arena from 0.94 to 0.98. [R7]
- **Self-preference:** GPT-4 and Llama 2 can recognise their own text with better-than-chance accuracy, and fine-tuning for
  self-recognition increases self-preference. [R2] A second paper attributes self-preference to perplexity: LLMs rate low-perplexity
  (familiar) text higher, regardless of who wrote it. [R8] Consequence for us: do not use a judge from a family that is also an arm
  (a Claude judge for a Claude baseline, or a Qwen judge for a Qwen arm).
- **Style / surface features in code:** CodeJudgeBench ACL version reports sensitivity to variable naming and misleading comments. [R10]

### A3. Judge variance (prompts, versions, runs)

- Rating Roulette (EMNLP Findings 2025): repeated runs of the same judge give "low intra-rater reliability"; ratings are "inconsistent, and at worst
  close to arbitrary". The abstract has no coefficients (*numbers not verified*). [R9]
- CodeJudgeBench: "all models still exhibit significant randomness in their judgment of coding tasks"; reasoning ("thinking") models do better
  than non-reasoning, and a small reasoning model (Qwen3-8B) can beat specially trained judges up to 70B. [R10]
  That reasoning-model effect also means **judge results are tied to a judge model version**; a re-run a year later is not the same instrument.
- Criteria drift: graders' criteria change as they see outputs, so human calibration labels need a written rubric and a second annotator. [R12]

### A4. When a judge is acceptable and how to calibrate it

What the literature supports (as engineering practice, not as one agreed standard):

1. Use a judge only where no executable oracle exists, or to *audit* the oracle.
2. Different model family from every arm; fixed prompt and version pinned; both pair orderings; temperature low, several repeats. [R3], [R6], [R9]
3. Human-label a sample (rubric written first, two annotators, report Fleiss/Cohen kappa; ELT-Bench-Verified reports Fleiss' kappa 0.85
   for its annotation, abstract page). Report chance-corrected agreement, not only percent agreement. [R5], [R15]
4. If the judge score will be used for a headline number, correct for judge error with the human sample. Prediction-powered inference
   gives confidence intervals that stay valid "regardless of how the predictions were produced" while using a small labelled set. [R20]
   For discrete LLM-judge labels there are Bayesian PPI [R21 secondary-search-only] and calibration-set interval methods (arXiv 2511.21140,
   seen only as a search summary, not read: *not verified*).
5. Re-calibrate whenever the judge model or prompt changes.

Evidence that a calibrated judge can beat naive execution on a narrow task: FLEX (SQL) reports Cohen's kappa with human experts of 87.04 against
62 for plain execution accuracy (v4 abstract; earlier versions reported other figures, "61 to 78.17" per a search summary, so cite the version).
It also says EX underestimates models mainly through annotation errors and *overestimates* on challenging questions. [R13] Caveat: these are the
authors' numbers on their own expert labels; I did not see an independent replication (*not verified*).

### A5. Execution-based checks are fallible too

**SQL result comparison.**
- Test-suite accuracy (Zhong et al., EMNLP 2020): the then-current Spider metric had a 2.5% false-negative rate on average and 8.1% in the worst case; distilled test suites
  are a tighter bound; 100 manually checked examples were always judged correctly. [R11]
- Comparison semantics differ by benchmark. A 2025+ paper (ModularSQL, arXiv 2609.29573, found via search, *secondary: not read*) reports that the BIRD evaluators compare
  `set(predicted) == set(gold)` (ignores row order and collapses duplicate rows) while Spider's test-suite evaluator uses multiset comparison. Another paper says BIRD ignores column order and column affiliation.
  I could not confirm Spider 2.0's own comparison rule from its repo: *not verified*, check `evaluation_suite` code before using it.
  Practical point for us: unordered/set comparison gives false positives for queries that must be ordered or must keep duplicates; ordered comparison gives false negatives for queries without ORDER BY.
- "Fundamental Challenges in Evaluating Text2SQL Solutions" (arXiv 2501.18197, abstract only): data-quality issues, NL ambiguity, and "biased match functions" that approximate SQL equivalence. [R17]
- Annotation errors: [R14] audited BIRD Mini-Dev (52.8%) and Spider 2.0-Snow (62.8% on the updated question set; a CIDR 2026 version of the same group's work reports 66.1% for Spider 2.0-Snow on the original questions, per search summary, *secondary*).
  Spider 2.0-Snow gold queries were public for only 121 of 547 examples, so the audit covered those 121. Spearman between corrected-subset ranking and full-Dev ranking was 0.32 (p=0.23) vs 0.85 for the uncorrected subset.
  The error rates come from an AI-agent-plus-expert-review procedure; I did not read how they define "error" in the body.

**Agent benchmarks.**
- ELT-Bench-Verified [R15]: "most failed transformation tasks contain benchmark-attributable errors" (rigid evaluation scripts, ambiguous specs, incorrect ground truth).
  Numbers (arXiv HTML, Tables 1-3): SRDT for SWE-Agent + Claude Sonnet 4.5 went 22.66% (46/203) -> 30.05% with evaluation-script refinement only -> 32.51% (66/203) with both fixes; ReAct went 20.20% -> 32.51%.
  Original ELT-Bench with Sonnet 3.5 had SRDEL 37% / SRDT 1% (Introduction). The paper's own framing: the original benchmark *understated* agents, and it calls for systematic quality auditing as standard.
  Directly relevant to testingplan's "Optional" ELT-Bench entry.
- SWE-bench Verified: OpenAI's release (Aug 2024) found original tasks that may be hard or impossible to solve and released 500 human-verified samples (search-result summary of the OpenAI page; the page itself returned HTTP 403 to my fetch, so I
  could not verify its annotation percentages: *not verified*). [R19 secondary]
- "Are Solved Issues in SWE-bench Really Solved Correctly?" (PatchDiff): 7.8% of plausible patches pass SWE-bench's tests but fail the developer-written tests; 29.6% of plausible patches behave differently from ground truth;
  28.6% of those divergent patches certainly incorrect on manual inspection; inflation of 6.2 absolute points (abstract page). [R16]
- SWE-Bench+ (arXiv 2410.06992): for SWE-Agent + GPT-4, 32.67% of successful patches had the solution leaked in the issue or comments, 31.08% passed on weak tests; after filtering, resolution rate fell from 12.47% to 3.97%. [R23]
- UTBoost: added tests found 36 instances with insufficient tests and 345 erroneous patches wrongly marked passing; this affected 40.9% of SWE-bench Lite and 24.4% of SWE-bench Verified leaderboard entries and changed 18 / 11 rankings. [R24]
- HumanEval: EvalPlus adds about 80x more tests; across 26 LLMs, pass@k drops by up to 19.3-28.9%, and the ranking of some models flips (WizardCoder-CodeLlama and Phind-CodeLlama pass ChatGPT on HumanEval+ but not on HumanEval). [R18]
- Flaky tests: I did not find a primary paper quantifying flakiness on SWE-bench or Spider-family suites. *Not verified.* The cheap mitigation is to run each checker twice on the gold solution and on a no-op solution (see section 4).

### A6. What this means for analytics-engineering tasks specifically

- DABstep uses factoid-style answers with automatic correctness checks (best agent 14.55% on the hardest tasks, abstract). Exact-answer matching on a single value is the
  lowest-risk executable check; its failure mode is formatting/rounding, not semantic error. [R25]
- InsightBench-style open-ended analysis has no oracle; testingplan already excludes it. If you add open-ended analysis, that is where a calibrated judge belongs.
- For SQL tasks, the checker itself needs its own test: run it on (a) the gold SQL, (b) a known-wrong near-miss, (c) a permuted-row/duplicated-row variant, and count how often it passes each.

---

## 3. Section B: claiming equivalent quality at lower cost

### B1. Paired testing, intervals, power

- Your design is paired (same tasks, same tools). For binary pass/fail, the natural test is McNemar on the discordant pairs; for graded scores a paired t-test or paired bootstrap. The recent paper "Resolution Diagnostics for Paired LLM Evaluation"
  states this and reports that 11 of 40 Open LLM Leaderboard v1 pairwise comparisons and 4 of 9 MMLU-Pro adjacent top-10 pairs are unresolved at alpha=0.05, power 0.8; the unpaired Cohen-h-plus-(1-rho) shortcut "deviates from the correct N* by approximately a factor of two"
  in close comparisons (abstract page, ICML 2026 workshop paper). [R22] I read the abstract only.
- "Adding Error Bars to Evals": evaluations are experiments; questions are draws from a super-population; the paper gives formulas for analysing, comparing two models, and planning evaluation experiments (abstract page; I did not see the specific recommendations such as clustered errors or paired differences in the text I got, though the title and abstract point to them: *details not verified*). [R26]
- Classical reference for McNemar and 5x2 tests in ML comparison is Dietterich (1998, Neural Computation). *Not retrieved; not verified here.*

### B2. Non-inferiority (the claim we actually want)

- The plan's "CES lower bound >= 0.85" is already a non-inferiority-style rule, but it is expressed on a ratio capped at 1 per domain (`min(1, pass_C / pass_A)`). The cap discards evidence where C beats A in one domain and hides it in another, and a ratio of small pass rates (Spider 2.0 around 20%) is unstable. Statistically, the standard
  formulation is a one-sided test or one-sided CI on the *paired difference* in pass rate against a pre-registered margin delta ("C is no worse than A by more than delta").
- I searched specifically for a primary paper applying a prespecified-margin non-inferiority or TOST design to LLM benchmark comparison and **found none**. The closest are paired-test papers [R22]. The non-inferiority procedure itself comes from clinical-trial statistics; I have not cited a source for it here. *Source for the clinical-trial method: not retrieved.*
- Power consequence (worked in plan terms, my arithmetic, not from a paper): with 150 tasks and a margin of 10 points, you need the paired discordant rate to be small. If A and C disagree on 20% of tasks, the standard error of the paired difference is roughly sqrt(0.20/150) = about 3.7 points, so a one-sided 95% bound is about 6 points wide on top of the observed difference. A 10-point margin is then feasible only if the true difference is near zero. Per-domain margins at n=15-25 are not feasible, which the plan already says.

### B3. Run-to-run variance, pass@1 vs pass@k vs pass^k

- Non-determinism: five LLMs set to be "deterministic", eight tasks, 10 runs: accuracy varied by up to 15% across runs and the gap between best and worst possible performance reached 70% (abstract). [R27]
  The plan's "rerun a 30-task slice twice" is thin: 30 tasks x 2 runs cannot estimate per-task flip rate well. Proposal below.
- pass@k (Chen et al.): Codex solves 28.8% of HumanEval, and 70.2% with 100 samples per problem. [R28] pass@k with large k measures *capability under oracle selection*, not what a customer gets from a single run, and rewards cost-heavy sampling. For a cost-equivalence claim, pass@1 (and cost per resolved task) is the honest primary metric.
- tau-bench pass^k: the probability that *all* k trials succeed; state-of-the-art function-calling agents (including gpt-4o) succeed on under half the tasks and in retail pass^8 is below 25% (abstract). [R29] The definition of pass^k is not in the abstract, but this is the reliability view. For customer use, report pass@1 averaged over >=3 repeats plus pass^k for k=3 as a reliability note.

### B4. Cost accounting and agent evaluation

- "AI Agents That Matter": accuracy-only agent benchmarks lead to needlessly complex, costly agents; evaluate cost alongside accuracy and jointly optimise; many benchmarks lack adequate holdout sets, which allows overfitting by shortcuts; evaluation practice is not standardised, causing reproducibility problems (abstract). [R30]
  Note: the abstract does not use the word Pareto; the Pareto plot is a common way to present it but I did not verify the paper's own wording.
- Holistic Agent Leaderboard: 21,730 rollouts, 9 models, 9 benchmarks, about $40,000, parallel harness; "higher reasoning effort reducing accuracy in the majority of runs"; LLM-aided log analysis found agents searching for the benchmark on HuggingFace instead of solving the task; logs of about 2.5B tokens released (abstract). [R31]
  Two lessons: (1) cost is a primary output, and spending more tokens does not reliably raise accuracy; (2) read the logs, because agents can find shortcuts around a checker, which is an evaluation-validity problem for any executor with web access. Disable web/benchmark lookups in arms.
- Cost-accounting items specific to your plan (my proposals, no single paper owns them): count retries, failed attempts, tool-call round-trips, and token caching discounts as the provider bills them on the run date; record input/output/cached tokens separately so price changes can be re-applied; record wall-clock and failures-to-complete separately; price the baseline arm at the same date's list price; report cost per *resolved* task as well as per attempted task (a cheap model that fails often costs more per solution).

### B5. Contamination and leakage

- LiveCodeBench: continuously collects new problems from LeetCode, AtCoder and CodeForces; the paper's release has 400 problems from May 2023 to May 2024; allows evaluating on problems published after a model's training cutoff (abstract). [R32] Time-window filtering is the primary mitigation.
- "Benchmarking Benchmark Leakage" (31 LLMs, mathematical reasoning): perplexity and n-gram accuracy detection found signs of training on test sets; recommends a "Benchmark Transparency Card". [R33] Detection is model-side and needs open weights/logits, which suits open models but not the closed baseline.
- SWE-Bench+ leakage: solutions present in issue text (see A5). [R23]
- The plan already treats SWE-bench Verified Mini as a contamination control. Add: report the fraction of tasks from public benchmarks (all of them are), keep the 20 CPG and 15 PySpark tasks authored by you as the primary evidence, and compare public-vs-authored pass rates; a large gap is a contamination signal for that arm.
  Also confirm every authored task is not on a public GitHub repo before the run, and that authored reference solutions are never placed in a retrievable memory store (otherwise arm C can retrieve the answer, which is memory leakage, not skill).
- Whether open models contaminate more than frontier models on these specific benchmarks: *not verified*, no primary source found.

---

## 4. What this means for `docs/testingplan.md` (proposals only, nothing applied)

1. **Reword the no-judge rule** (section 3 intro): "Core correctness is executable and every checker is itself audited; no model judges Core correctness." Add one sentence naming the exception: an optional open-ended section scored by a calibrated judge.
2. **Add a checker audit step before the run**: for each source, run the checker on (a) gold solution, (b) no-op or empty answer, (c) a hand-built near-miss; hand-review 20 randomly chosen "pass" and 20 "fail" outcomes per source after the pilot. Target: report checker false-positive and false-negative counts per source. Justification: [R14], [R15], [R16], [R18].
3. **SQL comparison policy**: state per task whether result comparison is ordered, multiset, or set; ordered when ORDER BY is in the intent; multiset otherwise; never plain set. Check Spider 2.0's script before trusting its numbers. Justification: A5.
4. **Optional open-ended section** (e.g. InsightBench-type narrative analysis, code review comments): scored by a judge from a family not used in any arm, pinned version, both orderings, 3 repeats, with a human-labelled sample of at least 100 items (two annotators, rubric first), kappa reported; headline never merged into CES. Justification: [R3]-[R6], [R9], [R12], [R20]. The 100 figure is my suggestion, not from a paper.
5. **Replace or supplement CES with a paired non-inferiority test on the pooled pass-rate difference**: pre-register delta (for example 8 or 10 points), report the one-sided 95% lower bound from a paired bootstrap over tasks (resample tasks, keep arms paired), plus exact McNemar discordant counts. Keep CES as a descriptive per-domain view. Justification: B1-B2; no LLM-specific non-inferiority paper found, so say this is borrowed from standard clinical-trial practice.
6. **Do a power calculation using the observed discordance**: after the 30-task pilot, estimate the discordant fraction between A and C and compute the N for the chosen delta; do not assume 150 is enough. Justification: [R22].
7. **Repeats**: run every task in every arm at least 3 times (not just a 30-task slice twice), resample over tasks and runs, and report the per-task flip rate. Cost permitting, one repeat of all 150 is the minimum. Justification: [R27], [R29].
8. **Metrics**: primary = pass@1 averaged over repeats; secondary = pass^3 reliability; avoid pass@k as a headline. Justification: [R28], [R29].
9. **Cost**: log input, output and cached tokens per call and per arm; report cost per *resolved* task and cost per attempted task; pin prices to the run date; include planner/worker/retry tokens (the plan already does). Justification: [R30], [R31].
10. **Contamination**: add public-vs-authored pass-rate comparison; drop benchmark lookup tools; keep authored tasks and answers out of any retrievable store; keep SWE-bench Verified Mini as a control only. Justification: [R23], [R32], [R33].
11. **ELT-Bench entry**: if ever used, use ELT-Bench-Verified, not the original. Justification: [R15].
12. **Log review**: read a sample of agent logs for shortcut behaviours (benchmark lookup, test editing, hard-coding expected answers). Justification: [R31].

---

## 5. Reference list

"Read" = abstract page of the paper itself (arXiv/ACL), not full text, unless noted. "Search only" = secondary, snippet from search result.

- R1. Zheng et al., Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena, 2023. https://arxiv.org/abs/2306.05685 . Judge agreement with humans (>80%) and bias list. Read (abstract).
- R2. Panickssery, Bowman, Feng, LLM Evaluators Recognize and Favor Their Own Generations, 2024. https://arxiv.org/abs/2404.13076 . Self-recognition and self-preference. Read (abstract).
- R3. Shi et al., Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge, 2024. https://arxiv.org/abs/2406.07791 . Position bias across 15 judges, 150k+ instances. Read (abstract).
- R4. Tan et al., JudgeBench: A Benchmark for Evaluating LLM-based Judges, 2024. https://arxiv.org/abs/2410.12784 . Judges near chance on objectively-labelled correctness pairs. Read (abstract); I did not verify that this is the exact author list.
- R5. Thakur et al., Judging the Judges: Evaluating Alignment and Vulnerabilities in LLMs-as-Judges, 2024. https://arxiv.org/abs/2406.12624 . 13 judges; percent agreement is misleading; leniency. Read (abstract).
- R6. Wang et al., Large Language Models are not Fair Evaluators, 2023. https://arxiv.org/abs/2305.17926 . Position bias and the three calibration strategies. Read (abstract).
- R7. Dubois et al., Length-Controlled AlpacaEval, 2024. https://arxiv.org/abs/2404.04475 . Verbosity bias, 0.94 -> 0.98 Spearman. Read (abstract).
- R8. Wataoka et al., Self-Preference Bias in LLM-as-a-Judge, 2024. https://arxiv.org/abs/2410.21819 . Perplexity explanation. Read (abstract).
- R9. Haldar and Hockenmaier, Rating Roulette: Self-Inconsistency in LLM-As-A-Judge Frameworks, Findings of EMNLP 2025. https://arxiv.org/abs/2510.27106 . Low intra-rater reliability. Read (abstract); no numbers seen.
- R10. CodeJudgeBench: Benchmarking LLM-as-a-Judge for Coding Tasks, 2025 (ACL 2026). https://arxiv.org/abs/2507.10535 and https://aclanthology.org/2026.acl-long.888/ . 5,352 pairs, 26 judges, order/naming/comment sensitivity. Search-result summary of the abstract, paper itself not opened.
- R11. Zhong, Yu, Klein, Semantic Evaluation for Text-to-SQL with Distilled Test Suites, EMNLP 2020. https://aclanthology.org/2020.emnlp-main.29/ . 2.5% average and 8.1% worst-case false-negative rate of Spider metric. Read (abstract).
- R12. Shankar et al., Who Validates the Validators? Aligning LLM-Assisted Evaluation of LLM Outputs with Human Preferences, 2024. https://arxiv.org/abs/2404.12272 . Criteria drift; human-grade a subset to select evaluators. Read (abstract).
- R13. FLEX: Expert-level False-Less EXecution Metric for Text-to-SQL Benchmark, NAACL 2025. https://arxiv.org/abs/2409.19014 . kappa 62 -> 87.04 (v4 abstract); versions differ. Read (abstract, v4).
- R14. Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards, 2026. https://arxiv.org/abs/2601.08778 . 52.8% BIRD Mini-Dev, 62.8% Spider 2.0-Snow, ranking effects. Read (abstract). CIDR 2026 version ("Text-to-SQL Benchmarks are Broken", https://vldb.org/cidrdb/2026/text-to-sql-benchmarks-are-broken-an-in-depth-analysis-of-annotation-errors.html) cited for 66.1% via search summary only.
- R15. ELT-Bench-Verified, 2026. https://arxiv.org/abs/2603.29399 (numbers from https://arxiv.org/html/2603.29399, Tables 1-4). Benchmark errors in ELT-Bench. Read (HTML, via summariser).
- R16. Are "Solved Issues" in SWE-bench Really Solved Correctly? An Empirical Study (PatchDiff), 2025. https://arxiv.org/abs/2503.15223 . 7.8%, 29.6%, 28.6%, 6.2 points. Read (abstract).
- R17. Fundamental Challenges in Evaluating Text2SQL Solutions and Detecting Their Limitations, 2025. https://arxiv.org/abs/2501.18197 . Biased match functions, data quality. Read (abstract), no numbers.
- R18. Liu et al., Is Your Code Generated by ChatGPT Really Correct? (EvalPlus), 2023. https://arxiv.org/abs/2305.01210 . HumanEval+ 80x tests, pass@k drop 19.3-28.9%. Read (abstract).
- R19. OpenAI, Introducing SWE-bench Verified, 2024-08-13. https://openai.com/index/introducing-swe-bench-verified/ . Page returned 403 to my fetch; description is from search-result snippets and a third-party summary. Secondary; annotation percentages not verified.
- R20. Angelopoulos et al., Prediction-Powered Inference, 2023. https://arxiv.org/abs/2301.09633 . Valid CIs from a small labelled set plus model predictions. Read (abstract; note my first fetch returned an unrelated page, a retry on this ID gave no usable text, so the content comes from the search-result description. Treat as search only.)
- R21. Hofer et al., Bayesian Prediction-Powered Inference, 2024. https://arxiv.org/abs/2405.06034 . Search only.
- R22. Kotawala, Resolution Diagnostics for Paired LLM Evaluation, 2026. https://arxiv.org/abs/2605.30315 . Paired tests; unresolved comparisons; 2x power shortcut error. Read (abstract). Workshop paper, not peer-reviewed at full-conference level as far as I could see.
- R23. Aleithan et al., SWE-Bench+: Enhanced Coding Benchmark for LLMs, 2024. https://arxiv.org/abs/2410.06992 . Solution leakage and weak tests. Read (abstract).
- R24. UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench, 2025. https://arxiv.org/abs/2506.09289 . 345 erroneous patches; leaderboard shifts. Read (abstract).
- R25. DABstep: Data Agent Benchmark for Multi-step Reasoning, 2025. https://arxiv.org/abs/2506.23719 . 450+ tasks, factoid answers, 14.55% best on hardest. Read (abstract).
- R26. Miller, Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations, 2024. https://arxiv.org/abs/2411.00640 . Evals as experiments; formulas for comparing and planning. Read (abstract).
- R27. Atil et al., Non-Determinism of "Deterministic" LLM Settings, 2024. https://arxiv.org/abs/2408.04667 . Up to 15% accuracy variation across runs. Read (abstract).
- R28. Chen et al., Evaluating Large Language Models Trained on Code (Codex, HumanEval), 2021. https://arxiv.org/abs/2107.03374 . 28.8% and 70.2% with 100 samples. Read (abstract).
- R29. Yao et al., tau-bench, 2024. https://arxiv.org/abs/2406.12045 . pass^k; pass^8 under 25% in retail. Read (abstract).
- R30. Kapoor et al., AI Agents That Matter, 2024. https://arxiv.org/abs/2407.01502 . Cost-controlled evaluation, holdouts, reproducibility. Read (abstract).
- R31. Holistic Agent Leaderboard, 2025. https://arxiv.org/abs/2510.11977 . 21,730 rollouts, about $40,000, log-analysis findings. Read (abstract).
- R32. Jain et al., LiveCodeBench, 2024. https://arxiv.org/abs/2403.07974 . Time-windowed problems. Read (abstract).
- R33. Xu et al., Benchmarking Benchmark Leakage in Large Language Models, 2024. https://arxiv.org/abs/2404.18824 . Perplexity / n-gram detection on 31 LLMs. Read (abstract).
- R34. Spider 2.0 (Lei et al.), 2024. https://arxiv.org/abs/2411.07763 . 632 tasks; o1-preview code agent 21.3% vs 91.2% Spider 1.0 and 73.0% BIRD (abstract of the version I saw; testingplan cites different task counts for other versions). Read (abstract).
- R35. ModularSQL (arXiv 2609.29573) and Kotawala-adjacent items seen only in search snippets: used only for the BIRD set-vs-Spider multiset comparison claim. Search only; verify against the BIRD and Spider evaluation code before relying on it.

Not retrieved and not cited as verified: Dietterich 1998 (McNemar / 5x2cv), clinical-trial non-inferiority references, any primary study of test flakiness on SWE-bench or Spider, any primary study comparing contamination between open and closed models on analytics benchmarks.
