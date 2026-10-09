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
SuperGLUE-style evals; HTML pages; Figma (design) and reporting (GitHub Pages sites or Tableau workbooks); building validation frameworks**, and similar. The 8 domains of the
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
| 7a | Figma (design to code) | [WebSight](https://huggingface.co/blog/websight) (synthetic screenshot-to-HTML, v0.2 Tailwind) and Design2Code screenshots as a proxy for Figma frames; real Figma frames authored from exported frame JSON + PNG of public community files (*verify licences*) | 2M / authored | 5 | SSIM visual diff + Tailwind token parity | **I found nothing called "Screen2Code"**; confirm what was meant. A screenshot is not a Figma file, so the authored frames are the closer match. |
| 7b | Reporting: GitHub Pages sites (read from "git page"; confirm) | Authored: a report page built from a seeded CSV as a static site | authored | 3 | Site builds; expected sections and charts present (Playwright DOM checks); links resolve; axe-core | No public benchmark found. |
| 7c | Reporting: Tableau workbooks | Authored: build a workbook from a given data spec (worksheets, calculated fields, filters) | authored | 2 | The workbook XML is parsed and checked: data source, field names, calculation formulas, mark types; formulas that translate are evaluated against a pandas reference on seeded data | Tableau cannot render or publish offline, so appearance and Server/Cloud publishing are not checked. Only 2 tasks, so this domain cannot support its own claim. |
| 8 | Building validation frameworks | APIs-guru OpenAPI registry | public specs | 15 | Property-based fuzzing (Hypothesis / fast-check) against the generated validator | The task is to build the validator; fuzzing checks it. |
| | **Core total** | | | **140** | | |

### 3b. Optional extension: analytics engineering (10 tasks, a fixed small add-on)

Only if the pitch needs it; it is not in the Core score. Built by us on public data, no cloud account needed:
PySpark rewrites (join salting, broadcast, window partitioning), Delta-style merges and dbt-style models on DuckDB
(checked against a SQL reference on seeded data), and CPG promo / pricing tasks on public retail data (Dunnhumby Complete
Journey, M5; *verify data licences*). Dashboard-logic items such as LTTB downsampling use unit tests.
Power BI and embedding APIs cannot be checked offline and are excluded. Tableau workbooks are in Core (7c) because the customer builds them.

Left out: Instacart / Favorita (no prices or promotions), ELT-Bench (needs a cloud warehouse), BEAVER (gated access),
InsightBench (LLM-scored, which breaks the correctness rule).

## 4. Scoring and statistics

Changes in this section follow the proposals in `docs/research/llm-judge-vs-execution-and-equivalence-methodology.md`
(section 4 of that file); the reasons and citations are there.

- **Per task:** one attempt, pass or fail by the checker. Harness errors are counted separately and are not failures.
- **No model judges Core correctness, and every checker is itself audited** (section 5, step 3). The one exception is an
  optional open-ended section (narrative analysis, code-review comments), scored by a judge from a model family not used
  in any arm, version pinned, both answer orders, 3 repeats, calibrated on a human-labelled sample (at least 100 items, two
  annotators, rubric written first; the 100 is a suggestion, not from a paper) with kappa reported. Its result is reported
  on its own and never merged into the Core numbers.
- **SQL comparison policy:** each SQL task states ordered, multiset or set comparison: ordered when ORDER BY is part of the
  intent, multiset otherwise, never plain set. Check Spider 2.0's own comparison script before trusting its numbers.
- **Primary measure:** pass@1 averaged over repeats. Secondary: pass^3 (all three repeats pass) for reliability. pass@k is
  not a headline.
- **Equivalence (primary claim):** a paired non-inferiority test on the pooled pass-rate difference (A minus candidate).
  Fix the margin before the run (for example 8 or 10 points); report the one-sided 95% lower bound from a paired bootstrap
  over tasks (resample tasks, keep arms paired) and the exact McNemar discordant counts. No LLM-specific non-inferiority
  paper was found; this is borrowed from clinical-trial practice and should be described that way. The earlier capped
  per-domain ratio is kept only as a descriptive table, because domains of 5-25 tasks are too small to claim parity.
- **Power:** after the 30-task pilot, estimate the A-vs-C discordance rate and compute the number of tasks needed for the
  chosen margin. Do not assume 140 is enough; the review warns the common unpaired shortcut is off by about 2x.
- **Repeats:** run every task in every arm at least 3 times, resample over tasks and runs, and report the per-task flip rate.
- **Cost:** log input, output and cached tokens per call; cost per resolved task and per attempted task; prices pinned to
  the run date; planner, worker, retry and relay tokens all counted (the relay tokens of the main agent in arm C included).
- **Decision rule, fixed before the run:** go if the lower bound of the non-inferiority test clears the pre-registered
  margin and the upper bound of the cost ratio (candidate / baseline, per resolved task) is at most 50%. Otherwise report
  "not shown". The 50-70% saving is a hypothesis until this exists.

## 5. Run plan

1. Choose the baseline and open models; fix prompts, attempt limit, timeouts, the margin and the decision rule; commit the
   task list with them.
2. Wire the public sources into `experiments/harness/`; author the Node.js tasks and any Python / SQL Git conflicts. Give the
   agents no benchmark-lookup tools and keep authored tasks and answers out of any retrievable store.
3. **Checker audit before any scored run:** for each source run its checker on (a) the gold solution, (b) an empty or no-op
   answer, (c) a hand-built near miss. After the pilot, hand-review 20 randomly chosen passes and 20 failures per source and
   report checker false-positive and false-negative counts. Use ELT-Bench-Verified, not the original, if ELT-Bench is ever added.
4. Pilot on 30 tasks, all arms: flaky checkers, cost accounting, run time, discordance. Drop tasks whose reference fails.
5. Full Core run (3 repeats); report the non-inferiority result, cost ratio, the descriptive per-domain table, flip rates,
   harness-error count and every deviation from step 1.
6. Read a sample of agent logs for shortcut behaviour (benchmark lookup, editing the tests, hard-coding expected answers).

## 6. Risks

- **Contamination.** BIRD, SWE-bench, DS-1000, Design2Code and the others are public. Report public and authored tasks
  separately and compare their pass rates; keep SWE-bench Verified Mini as a control only.
- **Checker quality.** Executable does not mean correct: published benchmarks have high annotation-error rates (the review
  cites audits of BIRD Mini-Dev and Spider 2.0-Snow). Budget time for step 3; verify those figures against the papers before quoting them.
- **Licences.** Only DABstep's CC-BY-4.0 was confirmed (it is not used here); check each dataset and model licence before
  redistributing. We run everything locally.
- **Providers and data.** Use only connections with a signed DPA; nothing here is customer data.
- **Claude Code arm.** Parked until Anthropic answers the written question about non-Claude endpoints.
- **Unconfirmed names.** "MergeEval" and "Screen2Code" were not found; tasks 3 and 7a depend on the answer. "git page" is read as GitHub Pages.
  Tableau workbook tasks are checked structurally only (no rendering, no publishing).
- **Not ready to freeze.** Still open: the baseline model, the margin, the dataset names above, and the customer's identity.