# DS-1000 pilot: does Kel's knowledge help, beyond routing? Preregistration

This file is written, and hashed together with the sample (`runs/design.sha256`), **before any model is run**. Changes made after that are listed under *Deviations* at the end, each with its reason.

## Question

When a problem is related to one Kel has already seen solved, does the knowledge Kel retrieves make models better at it? Is that effect different from:
- giving the model the right knowledge directly (oracle);
- giving it any longer prompt (placebo);
- plain retrieval of a similar past solution (RAG)?

Separately: how much of full Kel's cost saving comes from knowledge, and how much from routing?

## Data and sample

- **Benchmark:** DS-1000 (`xlangai/DS-1000`, `test.jsonl`), limited to pandas, NumPy, SciPy and scikit-learn.
  - Matplotlib is excluded: its grading depends on shared plot state and unseeded random data, and it has no Surface or Difficult-Rewrite variants.
  - PyTorch and TensorFlow are excluded because they are too heavy for the local sandbox.
- **Eligibility** (`check_references.py`): DS-1000's own reference solution passes both the gold grade and the check in the pinned environment (Python 3.11; pandas 1.5.3, NumPy 1.26.4, SciPy 1.12.0, scikit-learn 1.4.0, as DS-1000's `environment.yml` pins them). Result: 715 of 732.
- **Families:** an original problem plus its variants (same `perturbation_origin_id`).
- **Sample** (`design.py`, seed `ds1000-kel-v1`; every choice is a sha256 order, stratified by library):
  - **Fit (imported into Kel):**
    - 40 originals of families that have at least one variant (*transfer origins*);
    - 20 originals of other families (*distractors*, so most stored knowledge is irrelevant to any given new task, as in real use).
  - **Test (never imported):**
    - up to 2 variants of each transfer family (*transfer*);
    - 20 originals of families not otherwise used (*control*: no related knowledge exists).
  - Every other member of a used family is excluded.

## Grading (`evaluate.py`)

- **Gold:** DS-1000's own `test_execution` over all test cases, plus `test_string` when the problem has one. This is unchanged from the official grading.
- **Check:** what an agent could run before delivering, used only by routing.
  - Problems with 2 or more test cases: test case 1 only.
  - Problems with 1 test case: a smoke run that passes if the solution executes and assigns a non-None `result`. The expected answer is never consulted.
- **The same normalisation for every model and arm:**
  - extract the last python code block and cut it at DS-1000's end markers;
  - indent it to the insertion point (dedent, then indent). This also applies to the references.
- **Safety:** generated code is screened (banned modules and calls) and runs in a separate process of the pinned evaluation interpreter, in a temporary directory, with no secrets in its environment and a 60 s timeout. Screened-out code counts as a failure in every arm.

## Models and prompts (`models.py`)

- **Models:**
  - open models on General Compute: `gemma-4-31B-it`, `gpt-oss-120b`, `deepseek-v3.2`; temperature 0, one sample, max 4096 output tokens;
  - Claude Sonnet as fresh, context-free Claude Code subagents. They may not run code or use tools other than reading their input file and writing their output file. A batch never holds two problems from the same family, and never mixes arms.
- **Prompts:**
  - The system prompt is identical for all arms and models.
  - The user prompt is `PROBLEM:\n<DS-1000 prompt>`.
  - In arms with notes, a neutral block goes first: "Notes from similar past work… may or may not apply… use them only if they help". The block is the same wording in every arm with notes, so no arm is told its notes are relevant.
  - When an arm's prompt is byte-identical to arm A's (no notes available), arm A's graded attempt is reused, not re-sampled.

## How Kel learns (fit set only, through its own pipelines, into the local `kel_ds1000_demo` database)

1. **Goals:** each fit problem becomes a Goal. Its name is one generic imperative sentence written by `gemma-4-31B-it` from the problem text, standing in for ingestion; its description is the problem. Each Goal gets an accepted `SPECIALIZES` edge to a library domain Goal. A frozen benchmark per Goal holds the grading protocol.
2. **Attempts:** all four models attempt every fit problem, with no notes.
3. **Procedures:** the cheapest model's verified solution (gold pass) goes through Kel's `extract_procedure`, which routes it to `CodeSolutionExtractor`. The Procedure holds steps, APIs used and pitfalls, and never the code.
4. **Validation:** each open model re-runs its fit problems with that problem's own Procedure. The outcomes become the Procedure's verified execution evidence, which is in-sample.
5. **Recommender:** every fit attempt becomes a routing observation (check = accepted, gold = correct), then there's one joint refit (NUTS).
6. **Freeze:** after this, no product code, prompts or parameters change.

## Arms on the test set

| Arm | Notes block | Run for |
|---|---|---|
| A | none | 3 open models + Sonnet |
| B, Kel | what the real `find_ways` returns for the problem text (first 1500 characters), with the same automated planner policy as the BigCodeBench demo; Procedure rendered as steps, APIs and pitfalls | 3 open + Sonnet |
| C, oracle | the Procedure extracted from this problem's own family origin (none if extraction failed) | 3 open, transfer tasks only |
| D, placebo | a Procedure from another family, from another library when possible, chosen by hash | 3 open |
| E, plain RAG | the top-1 fit problem by BM25 (k1 = 1.5, b = 0.75, lowercase `\w+` tokens) with its verified solution code | 3 open |
| A′, noise | arm A re-run | 3 open |

Full Kel (**F**) and routing without knowledge (**F0**) are computed from recorded attempts:
- F: the real `recommend_models` ladder (models: 3 open + Sonnet; `allow_retries=False`; at most 3 attempts) over arm-B attempts. A step is accepted when its **check** passes; the delivered answer is graded by **gold**.
- F0: the same, over arm-A attempts.
- Reliability targets: 0.5, 0.6, 0.7, 0.8, 0.9.

## Analysis (`analyze.py`)

- **Primary**, transfer tasks, per open model: Δ = solve rate(B) − solve rate(A).
  - Two-sided exact McNemar test (binomial on discordant pairs), Holm-corrected across the 3 models at α = 0.05.
  - 95% confidence interval from a family-clustered bootstrap (10,000 resamples, seed 0).
- **Secondary** (exploratory, no multiplicity correction; confidence intervals as above):
  1. per model, C−A, D−A, E−A, B−E, C−B; also pooled over the 3 open models;
  2. Δ(B−A) by variant type (Surface / Semantic / Difficult-Rewrite);
  3. control tasks: the share that received knowledge, and B−A (negative transfer); also the number of tasks solved in A but lost in B, over all tasks;
  4. Sonnet B−A on transfer tasks;
  5. retrieval coverage and precision (precision: the Procedure offered comes from the task's own family);
  6. F and F0 against Sonnet-A on all test tasks: solve rate, cost, wrong answers delivered, first-step model;
  7. noise: the share of tasks whose gold outcome differs between A and A′, per model;
  8. leakage audit: the share of the reference solution's API identifiers that appear in the notes, per arm.
- **Costs:** open-model prices are placeholders, and Sonnet tokens are estimated. Cost results are indicative only; the knowledge conclusions rest on solve rates.
- **Failed calls:** a model call that errors is retried until it succeeds. If it still fails, that (task, model) is dropped from every arm for that model, so comparisons stay paired. The drop is reported.
- **Power:** about 80 transfer tasks can detect effects of roughly 12–15 points. Smaller true effects may show as non-significant. This is a pilot.

## Deviations

(filled in during and after the run)

1. **Extraction retries (before any test run).** The code-solution extractor runs at temperature 0.2. When a response cites mostly APIs the solution never calls, it is refused and raised as an `ExtractionTransientFailure`. The first pass produced 33 Procedures from 56 verified solutions. As the production ingestion queue does for transient failures (`max_attempts` 5), each failed extraction was retried until it had 5 attempts in total. Result: 44 Procedures. No product code changed.
2. **One Goal for two fit problems.** Problems 831 and 834 (deleting or inserting a scikit-learn Pipeline step) got the same generated Goal name, and Kel's exact-name identity merged them into one Goal (59 Goals for 60 fit problems). This is product behaviour, left as is.
3. **Attempt-log write collision (during test runs).** The Sonnet recorder and the arm-C runner appended to `runs/attempts.jsonl` at the same moment. One arm-C record (open model) was truncated to an unparseable fragment, and arms D and E stopped at start-up. The fragment was removed (a backup is kept as `attempts.jsonl.bak`), appends now use a cross-process lock file, and C, D and E were resumed. The lost (problem, model, C) attempt was re-run with the identical prompt; given the measured determinism (A vs A2: 0 flips in 252), this changes no outcome. Nothing else was touched.

## Addendum 1: "Procedure + verified code" on the SAME sample (post hoc, exploratory)

Added after the results above were seen, following the arm-E (plain RAG) finding. **This is not a confirmation**: the hypothesis came from this sample. Arms:
- **Bc:** arm B's retrieval, unchanged, plus the verified code of the fit solution each Procedure was extracted from (`arms_code.py`). This is what `find_ways` would return if Kel kept that code.
- **Cc:** arm C plus code.

Prompt framing, models and grading are unchanged, and prompts with no notes reuse arm A. Comparisons: Bc−A, Bc−B, Bc−E, Cc−C (paired, analysed as in S1). A confirmation on a fresh sample is preregistered separately.

### Addendum 1 results (same sample, exploratory)
Pooled over the 3 open models, transfer tasks:
- Bc−A: +3.1 [−2.6, +10.1], p = 0.26
- Bc−E: −5.2 [−11.1, 0.0], p = 0.076
- Cc−C: 0.0 [−3.9, +3.8]
- Sonnet Bc−A: +4.7 [−5.8, +16.4]

So appending code to the Procedure gave a small, non-significant gain and did not reach plain RAG. The oracle Procedure plus its code gained nothing.
