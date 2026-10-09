# Testing plan: routed open models on the customer's actual work

Status: draft, 2026-10-09. Nothing here has been run. No customer data, code or staff are involved: every task comes from
a public benchmark or is built on public data. Facts are from the cited pages; anything marked *verify* was not confirmed.

## 1. What we are testing

Whether our routed setup (cheaper open models plus the StealthLab knowledge layer and executors) solves the same tasks as
the model the customer's developers use today, at lower cost. Same tasks, same tools, three arms:

| Arm | What runs |
|---|---|
| A. Baseline | The model the customer uses now (decide with them; "Claude 3.5 Sonnet" in the first draft is dated), in its own harness |
| B. Cheap model, solo | An open model via the `docs/provider_catalog.md` providers, no StealthLab memory |
| C. Routed | `find_ways` + the executors (`stealth`, `opencode`) with the routing ladder |

Use the existing rig (`experiments/harness/`, three-arm sweeps, McNemar power) instead of a new one.

## 2. What the customer works on

Per the founders, the customer's work is: **SQL; debugging; Git; data science, Python, HTML, JS and Node.js;
SuperGLUE-style evals; HTML pages; Figma reporting; building validation frameworks**, and similar. The 8 domains of the
original draft match this list, so the domain mix stays. (An earlier revision of this file cut several of them after
judging them against the company's public profile; that was a mistake.)

Public context on the company (analytics delivery, strong data-engineering line, Databricks / Snowflake / Power BI /
Looker partnerships, CPG pricing work via Decision Point), for the optional extension in section 3b:
[PitchBook](https://pitchbook.com/profiles/company/91940-77),
[ICICI Direct note](https://www.icicidirect.com/mailcontent/idirect_latentview_q3fy26.pdf),
[Databricks](https://norfolkdailynews.com/online_features/press_releases/latentview-achieves-databricks-gold-partner-status-accelerating-enterprise-ai-and-data-transformation/article_8f2c6d48-413c-5e76-99c2-2b2f8fa542e1.html),
[Decision Point](https://www.latentview.com/press-release/latentview-analytics-announces-acquisition-of-decision-point-analytics/).

## 3. The suite

### 3a. Core: the customer's list (140 tasks, roughly half the first draft's 285)

Verification is executable or exact-match; no model judges correctness.

| # | Their work | Source | Source size | Core | Verifier | Findings from checking the sources |
|---|---|---|---|---|---|---|
| 1 | SQL | [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev); local parts of [Spider 2.0](https://github.com/xlang-ai/Spider2) (DBT setting, SQLite) | 500 pairs, 11 DBs / 68 + 135 | 25 | Execution result match | BIRD supports SQLite, MySQL and PostgreSQL. Spider 2.0 is much harder (reported 17-21% for older agents) and its Snowflake / BigQuery parts need cloud accounts, so use the local parts only. The first draft's "EXPLAIN ANALYZE plan cost" check is custom, not BIRD's: fix a threshold before running. *Verify Spider licence.* |
| 2 | Debugging | SWE-bench Verified Mini | 50 | 25 | Fail-to-pass and pass-to-pass tests | A community 50-instance subset; sources disagree whether random or curated ([HAL](https://hal.cs.princeton.edu/swebench_verified_mini)). Pin the dataset revision. Public, so likely in training data: treat as a contamination-exposed control. |
| 3 | Git | [ConflictBench](https://conf.researchr.org/details/ase-2024/ase-2024-journal-first-papers/22/ConflictBench-A-Benchmark-to-Evaluate-Software-Merge-Tools) (180 Java scenarios, about 135 true conflicts) or [Merge-Bench](https://arxiv.org/pdf/2605.25890) (7,938 hunks) | 180 / 7,938 | 15 | Clean worktree, no conflict markers, tests pass | **No benchmark called "MergeEval" exists in what I found**; confirm which was meant. Both are Java-centric, so author a few Python / SQL conflict tasks too. |
| 4a | Data science, Python | DS-1000; [DA-Code](https://aclanthology.org/2024.emnlp-main.748) | 1,000 / about 500 | 15 | Executable tests | DA-Code count is from secondary summaries; *verify licence*. |
| 4b | JavaScript | MultiPL-E (JS) | per language | 5 | Vitest / unit tests | Function-level only. |
| 4c | Node.js | Authored (no public benchmark found) | 5 authored | 5 | Node test runner | Small services, route handlers, streams. |
| 5 | SuperGLUE-style evals | IFEval Mini + BoolQ (a SuperGLUE task) | small | 10 | Strict format match / exact answer | Measures language ability, not agentic coding; keep small as a sanity check. |
| 6 | HTML pages | [Design2Code](https://github.com/NoviScl/Design2Code) (484 pages; Hard adds 80) | 484 / 80 | 15 | Headless Playwright DOM checks + axe-core accessibility | |
| 7 | Figma reporting | [WebSight](https://huggingface.co/blog/websight) (synthetic screenshot-to-HTML; v0.2 uses Tailwind) | 2M examples | 10 | SSIM visual diff + Tailwind token parity | **I found nothing called "Screen2Code"**; confirm. I am also assuming "Figma reporting" means design-to-code with a report; confirm what it means. |
| 8 | Building validation frameworks | APIs-guru OpenAPI registry | public specs | 15 | Property-based fuzzing (Hypothesis / fast-check) against the generated validator | The task is to build the validator; fuzzing checks it. |
| | **Core total** | | | **140** | | |

### 3b. Optional extension: analytics engineering (10 tasks, a fixed small add-on)

Only if the pitch needs it; it is not in the Core score. Built by us on public data, no cloud account needed:
PySpark rewrites (join salting, broadcast, window partitioning), Delta-style merges and dbt-style models on DuckDB
(checked against a SQL reference on seeded data), and CPG promo / pricing tasks on public retail data (Dunnhumby Complete
Journey, M5; *verify data licences*). Dashboard-logic items such as LTTB downsampling use unit tests.
Tableau and Power BI embedding cannot be checked offline, so they are excluded.

Left out: Instacart / Favorita (no prices or promotions), ELT-Bench (needs a cloud warehouse), BEAVER (gated access),
InsightBench (LLM-scored, which breaks the correctness rule).

## 4. Scoring and statistics

- **Per task:** one attempt, pass or fail by the checker. Harness errors are counted separately and are not failures.
- **Cost:** dollars per task = every model call in the arm (planner, workers, retries, failed attempts) at list price on
  the run date, including the main agent's relay tokens in arm C. Report cost per resolved task and the ratio vs A.
- **Equivalence:** report the pass-rate difference (A minus candidate) with a paired bootstrap interval; also a
  per-domain table for information. At 140 tasks only the pooled result is interpretable; domains of 5-25 tasks are too
  small to claim parity.
- **Repeats and power:** run the full suite at least 3 times; compute power from the pilot's discordance rate before
  fixing the size (the literature review says a 140-150 task paired comparison is probably underpowered for small margins).
- **Decision rule, fixed before the run:** the lower bound of the candidate's pass rate relative to A is at least 85%
  and the upper bound of the cost ratio is at most 50%. Otherwise report "not shown". The 50-70% saving is a hypothesis
  until this exists.
- **No judge decides correctness.** The reasoning and the one place a judge could be allowed are in
  `docs/research/llm-judge-vs-execution-and-equivalence-methodology.md` (its recommendation: keep the rule for Core, audit
  the checkers by hand on a sample, allow a calibrated judge only for a separate open-ended section).

## 5. Run plan

1. Choose the baseline and open models; fix prompts, attempt limit, timeouts; commit the task list and the decision rule.
2. Wire the public sources into `experiments/harness/`; author the Node.js tasks and any Python / SQL Git conflicts;
   check each checker against a reference solution, and hand-audit a sample of checkers (the review found published
   benchmarks with high annotation-error rates).
3. Pilot on 30 tasks, all arms: flaky checkers, cost accounting, run time, discordance. Drop tasks whose reference fails.
4. Full Core run (3 repeats); report the pooled difference, cost ratio, per-domain table, harness-error count and every
   deviation from step 1.

## 6. Risks

- **Contamination.** BIRD, SWE-bench, DS-1000, Design2Code and the others are public; report them separately from the
  authored tasks.
- **Checker quality.** Executable does not mean correct (see the review); budget time for the audit.
- **Licences.** Only DABstep's CC-BY-4.0 was confirmed (it is not used here); check each dataset and model licence
  before redistributing. We run everything locally.
- **Providers and data.** Use only connections with a signed DPA; nothing here is customer data.
- **Claude Code arm.** Parked until Anthropic answers the written question about non-Claude endpoints.
- **Unconfirmed names.** "MergeEval" and "Screen2Code" are not found; tasks 3 and 7 depend on your answer.
