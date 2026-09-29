# Ingestion pipelines (rebuilt 2026-09-29)

The step 0–8 pipelines were abandoned. `backend/app/ingest/` replaces them, built from `docs/ingestion_sources_plan.md`. It reuses only the core writers (Goals, Procedures, Claims, traces, evidence, routing) and the license and held-out rulebooks. Three pipelines: **OpenHands trajectories**, **SkillMD skills** and **verified solutions** (SWE-rebench, SWE-rebench-V2, SWE-bench-extra, SWE-Gym).

## How to run

```bash
# local test database (the DSN is read from the named environment variable, never typed or printed)
python -m app.ingest.cli skills    --target local --dsn-env KEL_INGEST_DSN --max-usd 2 --limit 20
python -m app.ingest.cli openhands --target local --dsn-env KEL_INGEST_DSN --max-usd 2 --limit 5
python -m app.ingest.cli verified  --target local --dsn-env KEL_INGEST_DSN --max-usd 2 --limit 5 --sources swe-rebench

# production: only with a named approver
python -m app.ingest.cli openhands --target production --approved-by "Anuj" --max-usd 20
```

A job worker must be running on the same database (`python -m app.ingestion.worker --loop`). It finishes Goal placement and trace normalization.

## What every run guarantees

| Guard | What it does |
|---|---|
| Target | A local run can't reach any hosted database: every database address in the process is rebound and checked, shards included. Production needs `--approved-by`. |
| Migrations | Refuses to start if any migration is pending. |
| One embedding space | Refuses to start if any stored vector was made by a different embedding model than the configured one (the mix found on 2026-09-29). |
| Live queue | Refuses to start if queued jobs have waited over 10 minutes unclaimed (no worker running). |
| One run at a time | A database lock, held for the whole run. |
| Models | A one-token probe of every model the run will use; refuses if one isn't served. |
| Spend | `--max-usd` is required; it is a hard 24-hour cap checked before every model call. |
| Ledger | Every item gets a row in `ingest_ledger`: **written**, **rejected** (a policy said no; never redone) or **failed** (infrastructure; retried up to 3 times). Each carries a reason code and the evidence. A killed run resumes where it stopped. One identity is written once across sources. |

## OpenHands trajectories (`nebius/SWE-rebench-openhands-trajectories`)

Pinned at `35455389…`. 67,074 runs of 6,306 tasks → **7,780 items**: 3,792 resolved and 3,988 failed.

1. **One run per task and outcome.** Each task keeps its **most direct successful run** (fewest messages) and **at most one failed run**. The failed run must be one where the agent submitted a fix and the tests rejected it. Crashes, timeouts and turn-limit endings are not wrong approaches, so they are never used.
2. **Held-out** tasks, and every task of a held-out repository, are excluded (410 tasks, 21 repositories; fails closed).
3. **License:** the task repository's license from the parent `nebius/SWE-rebench` (pinned `89cdfbab…`), mapped by an exact table. Names that don't say which license they are ("BSD", "BSD License", "Public Domain") are rejected, not guessed.
4. The run is written through the **same trace → episode → extraction path as a Claude Code session**.
5. **Resolved runs may produce Procedures; failed runs never do.** Failed runs produce Goals and Claims, including the failure modes and recovery patterns.
6. Each Procedure from a resolved run gets the benchmark's grade as **testimony** (`benchmark` evidence). It never makes the Procedure "verified": only Kel's own runs can.
7. Each run records a **routing observation**: model Qwen3-Coder-480B, scaffold OpenHands 0.54, resolved or not, on the run's Goal.
8. **Credit:** the trajectory is Nebius's CC-BY-4.0 work, so its ingestion record carries the credit (and the repository's license), and `find_ways` serves it.

## SkillMD-138K (`FayeZC/SkillMD-138K`)

Pinned at `0d73048a…`. 138,133 files from 20,556 repositories.

1. **Integrity:** the text must match the row's SHA-256.
2. **Duplicates:** exact (by content hash, across runs and sources) and near duplicates (MinHash, ≥ 0.9 similarity).
3. **Exact commit:** 94% of the dataset's links point at the branch `main`, not a commit. The pipeline finds the commit that holds this exact text, by comparing Git's own blob id: at the head of the default branch, at the commit in the link, or in the file's last 100 commits. If none holds it, the item is rejected (`content_not_at_any_commit`). A deleted repository is `source_gone`.
4. **License at that commit** (GitHub's own detection). No license file → rejected (`license_missing`).
5. **Compiled by the core SKILL.md compiler.** It screens for prompt injection before any model sees the text, applies the admission gate, and produces **candidate** Procedures only. A model outage is retried, never recorded as a rejection.
6. **Credit** on every item: file, repository, commit, license and the dataset.


## One task, one Goal (migration 129)

A SWE task arrives from several sources: its accepted fix (the verified-solution datasets) and agent runs on it (OpenHands). All of it meets on one Goal. The first pipeline to reach a task names its Goal from the issue (one model call; the name must pass the core quality gate) and records it in `ingest_task_goals`; every later pipeline attaches to it. On that Goal:

- **Procedures:** the agents' successful ways (trajectories) and the way to the accepted fix (verified solutions). Steps carry a role (plan / edit / verify) and, where one exists, a concrete check.
- **Verified solution:** the maintainers' accepted fix (gold patch), served with every Procedure of the task. It's never the agent's patch.
- **Benchmark:** the task's own tests, frozen: docker image, test command, which tests must flip, which must keep passing. Created only when the task has a runnable image (SWE-rebench, V2), so Kel can later run Procedures against it.
- **Claims:** failure modes, pitfalls and facts, in plain language, each naming what it's about and its conditions, linked to the Procedure where they apply. Plus one measured Claim per task from the clean signal: "Qwen3-Coder-480B with OpenHands solved *this Goal* in k of n runs, graded by the task's tests".
- **Routing:** each run's model, scaffold and outcome on the task Goal.

## Verified solutions

| Dataset | Pinned revision | License from | Benchmark |
|---|---|---|---|
| nebius/SWE-rebench (test split) | `89cdfbab…` | `license_name` (exact table) | yes (`docker_image`) |
| nebius/SWE-rebench-V2 (32k, 8+ languages) | `475dd5e8…` | `license`; `custom-check-github` → GitHub at the base commit | yes (`image_name`) |
| nebius/SWE-bench-extra | `11dcbfb3…` | `license` (lowercase SPDX) | no image → no Benchmark |
| SWE-Gym/SWE-Gym | `bb94ed9e…` | no column → GitHub at the base commit | no image → no Benchmark |

Rows are rejected for missing fields, tests that fail even with the fix (`FAIL_TO_FAIL`), a fix that breaks passing tests (`PASS_TO_FAIL`), held-out tasks or repositories, and licenses outside the allowlist. SWE-rebench and SWE-bench-extra share 4,568 tasks and SWE-Gym overlaps SWE-rebench on 196; a task is written by the first source that reaches it. The datasets' own LLM quality labels are recorded, not used as a filter (their thresholds are not documented).

## Model

One setting, `INGEST_MODEL`, for every pipeline. Production: Vertex `google/gemini-3.8-flash` (decided 2026-09-29), called through the core Vertex client with `VERTEX_MODEL` set to the same model; the client refuses a mismatch instead of letting a different model answer.

## Core changes made for these pipelines (2026-09-29)

| Change | Why | Where |
|---|---|---|
| **Only Goals with a live Procedure are agent candidates** (`goal_search_index.has_procedures`, migration 128). Goal identity still sees every Goal. | Extraction made a Goal per step: 75 Goals for 9 Procedures on the first test, and the empty ones outranked the real task Goals. With the filter the three test questions went from wrong or second-place Goals to the right one. | `search_projection`, `retrieval_service.search_goals`, `goals.search_goals(require_procedures=)` |
| **A Goal enters the hierarchy when it gets its first Procedure**; person-created Goals are still placed at creation | Placing empty step Goals filled the hierarchy with noise and review items | `goals.find_or_create_goal`, `identity_resolution.enqueue_missing_goal_placements` |
| **Second judge for low-confidence hierarchy links**: two agreeing judgments accept, a confident "unrelated/overlapping" rejects, only disagreement goes to a human | 363 of 367 links stayed "proposed" with nobody reviewing, so hierarchy, benchmark transfer and recommender pooling were all off | `goal_relation_second_judge.py`, placement handler |
| **Source-tested Procedures rank above untested ones** (after how clearly they apply); still no winner without Kel's own runs | A benchmark-passed Procedure ranked the same as an untested SKILL.md | `retrieval_service._select` |
| **Trajectory extractor**: shared task Goal, Procedure dedup, step role and check, and preconditions, verification actions and failure modes kept and linked | These were extracted, paid for and thrown away | `trajectory_semantics.extract_trajectory_semantics(task_goal=, write_procedures=)` |
| **Claims in plain language, formed as reusable facts**: name what they're about, keep conditions, state both sides of a comparison, drop implausible numbers (after Singh et al., arXiv:1802.04538) | Unbound or context-free claims can't be reused or checked | trajectory and document claim prompts (prompt versions bumped) |
| **Benchmark JSON stored as objects** | `create_benchmark` stored its protocol, environment and criteria as JSON strings, so SQL could not read inside them | `product_model` |
| **Benchmark transfer works** | It called `get_judge(pool)`, which takes no arguments, so every transfer failed | `benchmark_transfer._judge_transfer` |
| **The worker's upkeep runs while it loops**, and the nightly model refit is scheduled | In `--loop` mode the projection drain, placement repair and reconciliation only ran at exit; the nightly refit was "schedule it daily" with nothing scheduling it | `ingestion/worker.py` |

## Decisions, in plain words

- **Two small core changes.**
  - The trajectory extractor can now be told "this run failed: no Procedures". It also stores failure modes and recovery patterns, which it used to extract, pay for and throw away.
  - The SKILL.md compiler records the full credit the pipeline gives it.
- **Trajectories use the strong model for every run.** In the first test, the cheap model turned a successful fix into sub-goals and no Procedure. The strong model costs about the same.
- **The trajectory extraction model must be one the provider serves.** The core default strong model (`claude-sonnet-4-6`) isn't served by General Compute, so the run refuses until `TRAJECTORY_EXTRACTION_STRONG_MODEL` names a served model (tested with `deepseek-v3.2`).
- **Credit is recorded for every item, not only CC-BY ones.** The SkillMD card asks for it, and permissive licenses (MIT, BSD) require the notice anyway.
- **An item whose exact text can't be found at any commit is rejected, not ingested from `main`.** That keeps "re-check exactly what was ingested" true.

## Measured on the local test database (2026-09-29)

| Pipeline | Items | Written | Rejected (reasons) | Spend |
|---|---|---|---|---|
| Skills | 30 | 4 skills → 6 candidate Procedures | 23 no license, 2 repository gone, 1 unidentifiable license | $0.064 |
| OpenHands | 7 | 7: 3 failed runs → 8 failure Claims and 0 Procedures (3 proposed, withheld); 3 resolved runs on the strong model → 1 Procedure each with benchmark evidence; 1 resolved run on the cheap model → 0 Procedures (the reason the strong model is now used for every run) | 0 | $0.063 for the 3 resolved runs |

Every run wrote a routing observation. Goals per run are high (5–14): the core extractor makes each step's sub-goal a Goal of its own. That is core behaviour, not this pipeline's.

Most small repositories in SkillMD have no license file, so expect a low admission rate.
