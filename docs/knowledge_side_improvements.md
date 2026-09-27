# Knowledge side: what production does today, and what to change

This document proposes changes to Kel's knowledge side (Goals, Procedures and `find_ways`), grounded in controlled experiments. Routing (`recommend_models` / `report_model_run`) is out of scope: it is the measured win and needs no change from this work.

Evidence:
- [experiments/ds1000/README.md](../experiments/ds1000/README.md), with preregistrations `PREREGISTRATION.md` and `PREREGISTRATION_2.md`;
- [experiments/bigcodebench/README.md](../experiments/bigcodebench/README.md).

All experiments ran on isolated local databases, never on production.

## Status (after round 3)

**Round 3** (preregistered; 30 new families; knowledge base built the production way with changes 1–5 ON):
- **Primary:** Kel with the changes vs no notes was **+3.3 points [−3.4, +10.2], p = 0.27. Not confirmed.**
- **Head to head:** it equalled plain nearest-example retrieval (0.0) and today's product (+0.4).
- **Plain retrieval's own gain** fell to +3.3; round 2's +9.9 was partly that sample.
- **Frontier model:** Sonnet with the changes, −3.7 (ns).
- **Oracle ceiling:** handing models the *correct* Procedure and code gave at most +6 across rounds.

Single-shot tasks on well-known public libraries leave little room for knowledge: the models already know the APIs, failures are about exact specifications that a related solution does not settle, and there is no exploration to save.

| Change | Status | Where |
|---|---|---|
| 7. Judge batch fixes + `judge-health` | **Shipped** (a production bug) | `main` |
| 1–2. Store and return verified solutions | Built; **being redone with `source_locator` / `source_artifacts`** instead of a new column | branch `knowledge-examples-experimental` |
| 3–4. `related_examples` | Built; **off**. No confirmed benefit; costs tokens | same branch |
| 5. Strict candidate ways | Built; off | same branch |
| 6, 8 | Not built | — |

**Next test:** where knowledge should matter. Multi-step agent work in repositories, with knowledge the model cannot already have: see the SWE-bench protocol below.

## Summary of the evidence

Measure: gain in solve rate on tasks related to ones Kel had already seen solved. Three open models (gemma-4-31B-it, gpt-oss-120b, deepseek-v3.2), paired design, family-clustered 95% CIs; ns = not significant.

| What the model is given | Round 1 (64 tasks) | Round 2, fresh sample (104) | Round 2, production-like knowledge base |
|---|---|---|---|
| Kel's Procedure (today's product) | +1.6 (ns) | +2.2 (ns) | +1.6 (ns) |
| The *correct* Procedure, handed directly (oracle) | −0.5 (ns) | — | — |
| Kel's Procedure + the verified code | +3.1 (ns) | **+4.5** (pooled significant; missed the per-model preregistered bar) | +2.2 to +3.8 |
| Plain retrieval: nearest past problem + its verified code | **+8.3** | **+9.9** (replicated) | — |

Findings:
- **Retrieval is not the bottleneck for Procedures.** Even the correct Procedure, handed directly, gives nothing: models already know the steps. What transfers is concrete verified code, shown as a worked example.
- **Kel's retrieval answers "is this safe to execute?", not "what would help?"** It returns knowledge only when the judge says it *applies*. Close variants are correctly rejected, and nothing is returned in their place.
  - Traced example, problem 116: the right family was found, then judged "unrelated 0.49" for the Goal and "not_applicable" for the Procedure, so nothing was returned.
  - Plain retrieval showed the same past problem with its code, and the model adapted it and passed.
  - Kel returned knowledge for about half of related tasks; plain retrieval always returns its nearest example.
- **The experiment setup was not the cause.** Rebuilding the knowledge base the production way (embedded Goals, `judge_mode="model"` identity, the real placement worker, agent-written queries) left the gain at +2 to +4. It mainly cut wrong matches on unrelated tasks (10/20 → 3–5/20).
- **Production builds almost no Goal hierarchy for specific-task content.** Placement accepted 1 edge across 118 Goals. The judge correctly calls sibling tasks *distinct*, and nothing creates broader parent Goals.

Caveats:
- Small samples (84 and 124 tasks per round).
- Single-shot coding tasks only; multi-step agent work is untested.
- Public benchmark, so models may have seen it (this affects all arms equally).
- Open-model prices are placeholders.

## What stays as it is

- **The applicability-gated Procedure path:** `find_ways` → Goal choice → Procedure tier (judge: `applies` / `not_applicable`) → evidence-aware selection. It is right for its purpose, telling an agent what it can safely *follow*, and it rarely returns wrong knowledge.
- **Routing.**

## Proposed changes

### 1. Capture verified solutions (prerequisite)
- **Production today:**
  - Procedures are extracted from agent-run evidence (tool sequences, observations, outcome).
  - The code-solution extractor (`CodeSolutionExtractor`, `code_solution_v1@1`) runs only when evidence holds a *verified code solution*, and no production path records one yet.
- **Change:**
  - When an agent reports a finished run, it also sends the final code or diff and the check that passed (tests or verifier).
  - Kel stores this as an **evidence artifact on the Procedure version**. It is not a Procedure "implementation", so the no-implementations design holds.
  - The artifact inherits the run's visibility and privacy scope, is scanned for secrets, and is size-capped.
- **Why:** every measured knowledge gain came from verified code. Without capture, changes 2 and 3 have nothing to return.
- **Decision needed:** Kel would store verified code produced in users' runs, scoped to each run's visibility.

### 2. Return the verified code with each Procedure
- **Production today:** `find_ways` returns the Way, the steps with the APIs they use, and pitfalls.
- **Change:** each Procedure also carries `verified_example`: the code plus a one-line description of what it solved.
- **Evidence:** +4.5 on a fresh sample (+2.2 to +3.8 production-like), against about +2 for the Procedure alone.

### 3. A `related_examples` channel in `find_ways` (largest expected gain)
- **Production today:** if nothing is judged `applies`, the agent gets nothing. This includes every `ambiguous` and `no_match` answer, about half of related tasks.
- **Change:**
  - **Contents:** every `find_ways` answer, whatever its outcome, carries up to 3 **nearest verified solved examples**: the past task's text, its verified code, and its Goal. Each is labelled *"similar solved problem, not verified to apply; adapt"*.
  - **Search:** a new search index of solved examples in the search database (text plus embedding, hybrid search, access-scoped like everything else).
  - **Gate:** a new, looser judgment kind, `task_example` (relevant / unrelated). An example is dropped only when it is clearly unrelated at high confidence. Applicability judging stays for Procedures.
  - **Code locations:** `retrieval_service` (new `search_examples`), the `find_ways` payload, and the `plan_and_run` prompt (one line on how to use examples).
- **Evidence:** plain nearest-example retrieval gave +9.9 (replicated; +8.3 in round 1), about twice Kel's current gain. It helps even when the example comes from a different but similar task.

### 4. Let the judge express "variant"
- **Production today:** `task_goal` answers `matches` / `partial` / `unrelated`. A close variant (row-wise vs column-wise percentages) comes back `unrelated 0.49`, and a low-confidence `unrelated` still eliminates a top-ranked candidate.
- **Change:**
  - Add a `variant` verdict: same kind of task, different specifics. It counts like `partial` when listing candidates and always passes the `related_examples` gate.
  - A low-confidence `unrelated` on a top-ranked candidate no longer eliminates it.
- **Evidence:** the problem-116 trace.

### 5. Tighten "ways on a candidate"
- **Production today:** on an ambiguous answer, a way is listed if any Procedure under the candidate Goal, including its more specific Goals, is judged `applies`. This gave 10/20 wrong matches on unrelated tasks in the original run (3–5/20 production-like).
- **Change:** list a way only if its own source Goal is judged at least `partial` or `variant` for the request.
- **Priority:** low, but cheap.

### 6. Parent-Goal synthesis (defer; decide after round 3)
- **Production today:** `goal_abstraction_placement` only links existing Goals and accepts edges at confidence ≥ 0.9 (`GOAL_ABSTRACTION_MINIMUM_CONFIDENCE`). Specific-task content yields a flat graph, so the hierarchy walk and the "more specific Goals" lookup rarely have anything to use.
- **Change:** when sibling Goals cluster, a model proposes a broader parent Goal as a *proposed* edge for review, or accepted above the threshold.
- **Why defer:** changes 3–4 already recover "similar" knowledge. Build this only if retrieval still misses.

### 7. Fix the batched judge path (production bug, now)
- **Symptom:** identity and placement batches fail in three ways:
  - JEV returns **HTTP 400** on batched identity requests;
  - Gemini returns **malformed JSON** on batch replies;
  - Gemma's key is **rejected (401)**. It is likely a different key from the one working calls use.
- **Impact:** single judgments (`find_ways`) work. Production duplicate detection and hierarchy building are degraded now.
- **Fix:**
  - diagnose the JEV batch payload;
  - add JSON mode or repair for Gemini;
  - fix the Gemma key configuration;
  - add a judge health check to `LAUNCH_RUNBOOK.md`.

### 8. Close the feedback loop (later)
- **Change:** run reports record which returned Procedure or example the agent used and whether the run passed. That becomes evidence for ranking `related_examples`, reusing the existing evidence machinery.

## Order and validation

1. **Change 7:** production-facing, independent of the rest.
2. **Changes 1–4 (+5), behind a feature flag with tests.**
3. **Round 3:** preregistered, on untouched problem families, with a production-like knowledge base (embedded Goals, `judge_mode="model"`, the real worker, agent-written queries).
   - **Primary:** `find_ways` with `related_examples` vs no notes.
   - **Bar to match or beat:** plain nearest-example retrieval.
4. **Then decide on 6, and on enabling 8 in production.**

---

# Next experiment: SWE-bench (repo-level agent work): protocol and operator instructions

**Why this benchmark.** DS-1000 showed knowledge adds only 2–4 points on single-shot tasks over well-known libraries. The value Kel claims is different: saving exploration on multi-step work in a codebase, using what was learned from earlier work in that same codebase. SWE-bench Verified tasks are real issues in 12 repositories, fixed by an agent that explores the repo over many steps. The question:

> Does Kel's knowledge, learned from earlier issues in a repo, help an agent resolve later issues in the same repo, and at what cost?

**Who runs it.** Grading needs Docker, so **Chaitanya runs the whole experiment** on a Docker machine. Every step is a fixed script in `experiments/swebench/`, and the scripts refuse to run if the setup drifts. Follow the steps in order, do not skip a check, and do not change anything that isn't marked "fill in".

Design follows `.scratch/experience_transfer_research.md` (the AutomationBench protocol): a split that prevents leakage, memory frozen before evaluation, token-matched placebo controls, and a decision rule fixed in advance.

## Protocol (frozen; `experiments/swebench/experiment.json` holds every value)

| Item | Fixed value |
|---|---|
| Dataset | `princeton-nlp/SWE-bench_Verified`, split `test`, at the HF revision pinned on the first scored step |
| Scored repos | repos with at least 15 instances: django, sympy, sphinx, matplotlib, scikit-learn, astropy, xarray, pytest (about 480 instances). The smaller repos are used only for calibration |
| Split | **per repo, chronological by `created_at`**: earliest 60% form the **train pool**, later 40% are **held out** (about 290 / 190). No held-out issue is older than anything Kel learned from, as in real use. `design.py` computes it deterministically |
| Agent | `app.execution.coding_agent.Agent`: identical tools (search/read/edit, no code execution), identical budget and temperature 0 for every arm. **The only difference between arms is the memory block** appended to the first user message under one fixed "may not apply" header |
| Model | `model.id` in `experiment.json` (default `gpt-oss-120b`, OpenAI-compatible endpoint). The same model writes Goal names and `find_ways` queries |
| Step budget | chosen by **calibration** before any scored run (rule in `calibrate.py`: smallest of {40, 60} at which at least 80% of episodes finish), then frozen |
| Grading | the **official SWE-bench harness, unmodified** (`swebench.harness.run_evaluation`), FAIL_TO_PASS and PASS_TO_PASS. Harness errors are re-run, never scored |
| Kel | fresh local Postgres `kel_swebench`, built the production way from the **train pool only**: Goals embedded, `judge_mode="model"` identity, the real ingestion Worker for placement; Procedures from **resolved** train attempts through `extract_procedure` (up to 5 attempts); graded success recorded as evidence. **Gold patches are never read.** Frozen before any held-out run |

**Arms on the held-out set** (each held-out instance gets every arm, so every comparison is paired):

| Arm | Memory block |
|---|---|
| **A0** | none |
| **K** | real `find_ways` on the agent's own short request, with the planner policy used in DS-1000. Renders the selected Procedure (Way, steps, pitfalls) plus the verified patch it came from |
| **E** | plain retrieval: BM25 top-1 resolved train issue from the **same repo**, with its verified patch |
| **C1** | a random other train memory item (same repo, hash-chosen), cut to K's exact length. Only where K has notes |
| **C2** | a generic "how to fix issues" placebo, cut to K's exact length. Only where K has notes |

- **Reuse:** an arm with no notes for an instance reuses that instance's A0 attempt.
- **Caps:** memory is capped at 3,200 characters, and patches at 2,400.

**Primary:** held-out resolved rate, **K − A0**, paired per instance. Exact McNemar test and a 95% bootstrap CI stratified by repo.

**Decision rule** (fixed; `analyze.py` prints the verdict). "Knowledge helps" only if all four hold:
1. K − A0 > 0, with the CI excluding 0 and p < 0.05;
2. K − C2 lower bound > −3 points (not a placebo effect);
3. no repo shows a significant regression;
4. tokens per resolved instance under K ≤ 1.2× A0's.

**Secondary:** E − A0, K − E, C1 − A0, C2 − A0, per repo, K − A0 where K had notes, and tokens per resolved instance.

**Rules:**
- No code, prompt or parameter changes after the step budget is frozen.
- Anything unexpected is written into `experiments/swebench/DEVIATIONS.md` (create it if needed) with the date and the reason, before continuing.
- Never re-run a *graded* arm to "improve" it. Only environmental failures (`environmental_failure: true`, or harness `error`) are re-run.

## Operator instructions (Chaitanya)

### 0. Machine

- x86_64 Linux (or macOS with Docker Desktop), 16 GB+ RAM, **200 GB+ free disk** (SWE-bench environment images), Docker 24+.
- Apple Silicon also works, but image builds are slower. Record the platform in DEVIATIONS.md.

### 1. Code and Python

```bash
git clone https://github.com/3Founders/stealth-lab && cd stealth-lab
git checkout main && git pull
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install swebench datasets huggingface_hub
cd ..
```

- **Check:** `docker version` works, and `python -c "import swebench"` works.

### 2. Keys and local database

1. Copy the team's `backend/.env` from the password manager. Kel's judge and extraction need `JEV_*`, `GEMINI_API_KEY(S)`, `GENERAL_COMPUTE_API_KEY(S)` and `GENERAL_COMPUTE_JUDGE_MODEL`.
2. Run `cd backend && python -m app.ingestion.admin judge-health`. Every provider must be `ok`. **Remove any key that shows HTTP 401.**
3. Local Postgres (any 15+):

   ```bash
   createdb kel_swebench
   export KEL_SWEBENCH_DSN=postgresql://postgres@127.0.0.1:5432/kel_swebench   # local only; the scripts refuse anything else
   cd backend
   DATABASE_URL=$KEL_SWEBENCH_DSN python scripts/migrate.py
   python scripts/migrate.py --target search --dsn $KEL_SWEBENCH_DSN
   ```

4. The agent's model endpoint (OpenAI-compatible):

   ```bash
   export EXPERIMENT_BASE_URL=https://api.generalcompute.com/v1   # or your OpenRouter URL
   export EXPERIMENT_API_KEY=<key>                                 # never commit it
   ```

5. **Fill in** `experiments/swebench/experiment.json` → `model`:
   - `id`, e.g. `gpt-oss-120b`;
   - `openrouter_provider_order`, only on OpenRouter; for example `["<provider>"]` pins the backend with no fallbacks;
   - `price_per_mtok`, for cost reporting.

   Commit this change.
6. **Check:** `cd experiments/swebench && python check_env.py --check` prints "environment ready".

### 3. Sample and harness sanity check

```bash
cd experiments/swebench
python design.py          # prints scored repos, train/test/calibration sizes, design sha256
python grade.py --gold    # gold patches on the calibration set MUST all resolve
```

- **Check:**
  - `grade.py --gold` prints `gold patches resolved: N/N`;
  - it stops with STOP if not. Fix Docker before going on; do not proceed with a broken harness.

### 4. Calibration: freeze the step budget (unscored)

```bash
python generate.py --part calibration --arm A0 --max-steps 40
python generate.py --part calibration --arm A0 --max-steps 60
python calibrate.py       # prints CHOSEN max_steps
```

- **Freeze the budget:** write the chosen value into `experiment.json` → `agent.max_steps`, then **commit**.
- **From here the setup is frozen:** the next scored step writes `runs/pinned.json`, recording the commit, swebench version, Docker version, Python version, dataset revision, model and step budget. Every later step refuses to run if any of these change.

### 5. Train pool (Kel's experience)

```bash
python generate.py --part train --arm A0
python grade.py --tag train_A0       # re-run until it reports no errored instances
```

- **Check:** `runs/grades_train_A0.json` covers every train instance. A resolved rate below about 15% means too few successes for Kel to learn from. Stop and report it (the weak-model floor, research note §7.10).

### 6. Kel learns (train pool only), then freeze

```bash
python learn.py name
python learn.py import
python learn.py worker
python learn.py extract
python learn.py evidence
```

- **Check:** `extract` ends with `procedures N of M resolved train instances`, and N is at least 50% of M.

### 7. Held-out notes, built from frozen knowledge

```bash
python notes.py queries
python notes.py K
python notes.py E
python notes.py controls
```

- **Check:** `notes.py K` prints how many held-out issues got notes. Record the number.

### 8. Held-out arms

1. **A0 first**, because the other arms reuse it wherever they have no notes:

   ```bash
   python generate.py --part test --arm A0
   ```

2. **Then the other four, started together**, in four terminals or with `&`, so they run at the same time and share provider conditions:

   ```bash
   python generate.py --part test --arm K
   python generate.py --part test --arm E
   python generate.py --part test --arm C1
   python generate.py --part test --arm C2
   ```

3. **Grade all five.** Re-run any with errored instances until none remain:

   ```bash
   for arm in A0 K E C1 C2; do python grade.py --tag test_$arm; done
   ```

### 9. Analysis and hand-back

```bash
python analyze.py         # prints the primary result, secondaries, cost and VERDICT
```

Zip and share `experiments/swebench/runs/`, **excluding `runs/logs/`** (large Docker logs; keep them locally), plus `DEVIATIONS.md`.

**Estimated size:** about 290 train plus about 190 × 5 held-out agent episodes. Episodes where an arm had no notes reuse A0, so there are fewer in practice. At 100–200k tokens per episode on `gpt-oss-120b`, that is roughly 150–250M tokens. Harness grading is about 5–10 minutes per instance per arm, with 4 workers.

**Tested without Docker (2026-09-27)** on a synthetic repo:
- design;
- agent generation in git worktrees;
- reuse of A0 where there are no notes;
- Kel learning (import, the real placement worker, extraction, evidence);
- all four note builders;
- the analysis and decision rule.

**Not testable without Docker**, so the gold check in step 3 is mandatory:
- the harness call itself;
- the location of the per-instance `report.json`, which `grade.py` parses from `runs/logs/run_evaluation/<run_id>/**/report.json`.
