# Local vs. global knowledge on SWE-rebench-V2: preregistration

This file is written **before any arm is run, any memory block is built, or any task is graded**. `build_notes.py`
refuses to freeze without it and records its sha256 (LF-normalised) in `runs/kel_frozen.json`, next to the sha256
of `experiment.json`. Everything below is fixed from that moment. Any later change goes into `DEVIATIONS.md`,
dated, with its reason, and is reported with the results.

Design: `docs/plan_2026-10_priors_library_survey.md`, workstream D. Settings: `experiment.json` (frozen).

## 1. Question

Does a repository's **own local `.stealth` context** -- its facts (`claims.md`) and its own past fixes with their
diffs (`library.md`, mined from git history) -- make a coding agent fix new issues in that repository more often
and for the right reason? And, on top of that, do **enterprise** knowledge (Kel Goals from the same GitHub
organisation) and **global** knowledge (Kel Goals from every other repository) add anything?

Earlier evidence (internal, 96 tasks): same-repo past fixes with real diffs lifted "right cause" from 27 to 42
(p = 0.023), while steps-only knowledge did nothing. This experiment tests that finding on a larger, unseen,
test-graded sample, with the product's own local scanner.

## 2. Hypotheses

Primary (confirmatory):
- **H1.** L1 (local) resolves more tasks than A0 (nothing): resolve rate L1 > A0.
- **H2.** L1 identifies the right root cause more often than A0: right-cause rate L1 > A0.

Tests are two-sided; a significant difference in the opposite direction is reported as harm.

Secondary:
- **H3.** L2 (local + enterprise) vs L1, and **H4.** L3 (+ global) vs L2, on the same two outcomes: does each
  wider tier add to the one below it?
- **H5.** A0 vs L2 and A0 vs L3: total effect of each tier.
- Cost side: tokens, steps and wall time per task, and tokens per resolved task, for every pair above.

## 3. Task sample (`build_tasks.py`)

- **Dataset:** `nebius/SWE-rebench-V2`, revision `475dd5e8703bb5fb22dd3c60b5d038b019eba1e0`, train split. Every
  task ships a prebuilt image, its own test command and its own log parser, so the real tests grade it.
- **Eligible** when all of these hold:
  - **unseen:** not in the Kel corpus and not used in the earlier experiment (`queries.json`);
  - **not memorised:** created on or after 2024-07-01, the agent model's knowledge cutoff;
  - **has history:** its repository has at least 10 corpus Goals created strictly before the task;
  - **complete:** it has a problem statement, FAIL_TO_PASS tests and an image.
- **Cap:** at most 8 tasks per repository, the earliest ones, so a later task never shapes the choice of an
  earlier one and a few large repositories cannot dominate.
- **Order:** repositories and tasks are ordered by a stable sha256 hash with seed `kel-local-eval-v1`.
  - **Calibration:** 12 tasks, one from each of the last 12 repositories in hash order. They are never scored, and
    their repositories are excluded from the scored set.
  - **Scored set:** the first **322** tasks of the remaining pool in hash order.
- `instances.json` never holds the gold patch or the test patch; only grading reads `grading_source.json`.
- If fewer than 322 tasks are eligible, all eligible tasks are used and the shortfall is reported
  (`design_report.json`).

## 4. Arms and memory blocks (`build_notes.py`, `history.py`)

The same agent, model, decoding, step budget and system prompt in every arm. The **only** difference between arms
is the memory block appended to the first user message, with the same neutral header in every arm ("Notes from
previous work on similar tasks in this repository (may or may not apply; verify ...)").

| Arm | Memory block |
|---|---|
| A0 | none |
| L1 | local tier: `claims.md` facts + this repository's own past fixes (top 3, 2 with diffs) |
| L2 | L1 + enterprise tier: Kel Goals from the same GitHub organisation (top 3, 2 with diffs) |
| L3 | L2 + global tier: Kel Goals from every other organisation (top 3, 2 with diffs) |

- **Local tier provider: `survey`.** The real scanner from workstream C (`packaging/npm/lib/survey/survey.mjs`,
  merged on main) runs on a detached worktree of the checkout at `base_commit`. The pre-merge `stub` provider is
  not used for any scored note. The choice is recorded in `kel_frozen.json` (`providers`).
- **Leakage rules (tested in `tests/test_local_eval.py`):**
  - only commits that are ancestors of `base_commit` are read, so the task's own fix and anything after it never
    appear;
  - history window: the 730 days before the base commit, at most 5,000 commits (passed to the scanner
    explicitly);
  - nothing is read from the dataset's install configuration or test command;
  - enterprise and global Goals must be created strictly before the task; undated Goals are never shown;
  - a Goal already shown by the local library (same repository, same PR number) is dropped from the wider tiers.
- **Retrieval** for the enterprise and global tiers: bge-small-en-v1.5 cosine over the frozen corpus export
  (`corpus.jsonl` + `corpus_bge_small.npy`, exported once; no database reads during the experiment).
- **Sizes:** each tier at most 4,000 characters, each diff at most 2,400 characters.
- **Empty block:** when an arm's block is empty for a task, that task reuses its A0 attempt, so arms differ only
  where their inputs differ. Reuse is counted per arm and reported.
- All blocks are built once, before any scored run; their sha256 per arm is in `kel_frozen.json`.

## 5. Generation and grading

- **Agent:** `app.execution.coding_agent.Agent` through `experiments/swebench/generate.py` (`run_generate.py`),
  model `gpt-oss-120b`, temperature 0, one attempt per task and arm, on a real checkout at `base_commit`.
- **Step budget:** chosen on the calibration tasks with arm A0, before any scored run (`swebench/calibrate.py`):
  the smallest of 40 and 60 at which at least 80% of calibration episodes finish rather than run out of steps;
  if neither does, 60, logged as a deviation. It is written into `experiment.json` and never changed.
- **Test grading (`grade_tests.py`):** the task's own SWE-rebench-V2 image, test command and log parser, with
  SWE-bench's resolution rule: apply the patch (git apply, then `patch --fuzz`), reset and apply the test patch,
  run the tests; **resolved** = every FAIL_TO_PASS and every PASS_TO_PASS test passes. The log parser is pinned
  by commit.
- **Right-cause grading (`blind.py`):** Claude Sonnet subagents, blind to the arm. Each task shows the issue, the
  real fix, and the four arms' proposals as S1..S4 in a per-task shuffled order; batch files never name an arm or
  show a memory block. Fields: `root_cause_right` (primary), `score` 0-10, `accept`. 10% of batches, chosen by
  hash, are graded twice by independent graders; agreement and Cohen's kappa are reported.

## 6. Validity and errors (`valid_tasks.py`, `grade_tests.py`)

- **Valid task:** the gold patch resolves it and an empty patch does not. Both checks run before any arm is
  graded and use no arm's output, so they cannot favour an arm. A task whose gold or empty run still errors after
  retries is invalid.
- **Scored set for H1 and test outcomes:** the valid tasks. Right-cause does not need the tests, so it is reported
  on the valid set (primary) **and** on every designed task (sensitivity).
- **Infrastructure errors** (image pull, sandbox, timeout of the runner) are retried and never scored as a
  failure. A model call that keeps failing marks that attempt as an environmental failure; that task is dropped
  from every comparison involving that arm, so comparisons stay paired, and the drop is reported.
- **Sensitivity:** the primary comparisons are repeated with remaining errors counted as failures.

## 7. Analysis (`analyze.py`, `stats.py`)

- **Primary family (H1, H2), L1 vs A0:** two-sided exact McNemar test on the discordant pairs, Holm-corrected
  across the two outcomes at α = 0.05.
  - Effect: difference in rates with a 95% CI from a repository-cluster bootstrap (10,000 resamples, seed 0);
    conditional odds ratio with an exact CI; Cohen's h.
  - Robustness: a repository-cluster sign-flip randomisation test (20,000 permutations).
- **Secondary family:** every other pair in {A0-L1, L1-L2, L2-L3, A0-L2, A0-L3} on `resolved`,
  `root_cause_right`, `accept`, and the continuous outcomes `score`, tokens, cost, steps and wall time. Binary:
  exact McNemar; continuous: Wilcoxon signed-rank (plus the cluster sign-flip test). Benjamini-Hochberg across the
  whole secondary family.
- **Exploratory (no correction, labelled so):** L1 vs A0 by language, and by amount of earlier history
  (< 30 vs >= 30 corpus Goals before the task).
- **Reporting:** every comparison listed here is reported whatever its direction or significance, with rates,
  95% Wilson intervals, the CI of the difference, raw and adjusted p-values and effect sizes. Null and negative
  results are reported the same way. No comparison is added or dropped after seeing results; anything not listed
  here is labelled post hoc.
- **Cost:** tokens are the primary efficiency measure. A dollar cost is reported only if `price_per_mtok` is
  filled from a published price before the first scored run (and that is logged); otherwise cost is reported in
  tokens only.

## 8. Sample size and power

- 322 scored tasks, at most 8 per repository (so at least 41 repositories).
- With the exact McNemar test at α = 0.05 (two-sided) and 80% power, the smallest detectable paired difference
  (Connor 1987, `stats.mcnemar_sample_size`) depends on the share of discordant tasks:

  | tasks scored | 15% discordant | 25% discordant | 35% discordant |
  |---|---|---|---|
  | 322 | 6.5 points | 8.0 points | 9.5 points |
  | 280 (if ~13% fail validity) | 6.5 points | 8.5 points | 10.0 points |

- Clustering by repository inflates these by the design effect 1 + (m - 1) * ICC (m <= 8); the repository-cluster
  bootstrap CI accounts for it in the reported intervals.
- The earlier finding (+15.6 points right cause) is well above these thresholds; a true effect under ~7 points may
  come out non-significant, and the report says so rather than claiming "no effect".

## 9. Order of work

1. `build_tasks.py` -> `runs/design.json` (sample fixed).
2. `grade_tests.py --gold`, `--empty`, then `valid_tasks.py` -> `runs/valid_tasks.json` (scored set fixed).
3. Calibration (A0, 40 and 60 steps) -> `calibrate.py` -> `agent.max_steps` written and committed.
4. `build_notes.py --part all` -> `runs/notes_*.json`, `runs/kel_frozen.json` (blocks and this plan frozen).
5. `run_generate.py --part test` for A0, L1, L2, L3.
6. `grade_tests.py` per arm; `blind.py make`, grading, `blind.py collect`.
7. `analyze.py` -> `runs/analysis.json`, `runs/REPORT.md`.
