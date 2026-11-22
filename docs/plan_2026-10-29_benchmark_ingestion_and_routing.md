# Seeding the library from benchmarks, and routing on the result

Written 2026-10-29. Three parts: what exists (from `docs/framew.md`, `docs/results_index.md` and the code), a plan to ingest
benchmarks such as the GDPval paper (`gdp.pdf`), and how routing uses the framework. Status words are used strictly:
**built** = in the repo and tested offline; **measured** = a number exists in `results_index.md`; **proposed** = not built.

`gdp.pdf` was not found on this machine (searched the repo, Downloads, Desktop, Documents). Step 0 below needs its path. Nothing
here was read from it; what is said about GDPval comes from public leaderboard pages and is unverified until the paper is read.

## 1. The framework in one page

A verified procedural memory with a routing layer on top (`docs/framew.md`). Raw traces become goals, claims, procedures and
evidence. Nothing is deleted: an update closes the old record's validity window and appends a new one.

| Primitive | Meaning | Code |
|---|---|---|
| Goal | What is to be achieved, at some abstraction. Specific goals `SPECIALIZES` broader ones (migration 113) | `services/goal_abstraction.py` |
| Claim | A statement with a source and status. A goal's contract is linked claims: assumptions (hold before) and guarantees (hold after) (migration 153) | `services/goal_claims.py` |
| Procedure | An ordered way to reach a goal. Each step has a `binding`: model, tool, MCP tool, HTTP API, sandbox... (`schema.md`) | `services/procedures.py` |
| Evidence | A recorded run, pinned to the exact procedure version. Only execution results and reproductions make a claim verified; benchmark, document and self-report evidence is recorded but never proves a contract | `execution/evidence.py` |
| Check | What decides: a test, a benchmark, a build | `execution/verifiers/` |

What sits on top, all **built** unless noted:
- Retrieval and applicability: hybrid retrieval plus a judge picks the goal; a non-compensatory cascade rules procedures out.
- `find_ways` (knowledge), `call_model` (delegation with policy, budget, data class), `report_result` and evidence tools.
- Routing: hierarchical Bayesian P(pass) per goal and unit, exact ladder choice, per-category ranking, day-one ranking for new models.
- Dispatch: hooks for Claude Code and Cursor that try cheap models and accept only check-passing answers.
- Hosted MCP server with OAuth, org policy, spend ledger, wallet (off), website Delegator rooms.
- Benchmark import and public-result import (section 2).

## 2. Ingesting benchmarks: what exists

- `app/benchmarks/importer.py` (`admin benchmark-import`): a `BenchmarkTask` becomes a goal, an accepted `SPECIALIZES` edge to its
  domain goals, and a frozen benchmark (evaluation protocol, environment, metadata). Idempotent. Used for BigCodeBench-style code tasks.
- `app/routing/import_public.py` (`admin routing-import-public`, dry-run by default): reads local files only and writes model cards,
  evidence items, per-item observations and per-(benchmark, model) aggregates. Sources today: openrouter, swebench, rebench,
  swe-agent, routerbench, llmrouterbench. Every observation has a source-independent `dedupe_key`.
- Tables (migration 147): `routing_model_cards`, `routing_evidence_items` (public items that are not our goals),
  `routing_aggregate_results` (leaderboards that publish only k of n, a binomial likelihood in the fit).
- `app/routing/task_categories.py`: 12 kinds of work, a benchmark mapped to one by name pattern.
- Measured: with LLMRouterBench as a source, the card prior's order on held-out models rose from Spearman -0.07 to 0.60, but the full
  refit flattened the card regression, so it is not shipped as the prior (`results_2026-10-29.md` section 6).

## 3. Plan: ingest GDPval and other benchmarks

### 3.0 Sort every source into one of four kinds first

| Kind | Example | How it enters | What it can be used for |
|---|---|---|---|
| A. Executable check, per-item results | SWE-bench, DS-1000, BIRD, IFEval, tau2-bench | `benchmark-import` for the tasks, `routing-import-public` for runs | Observations on goals; strongest prior |
| B. Per-model aggregates only | LLMRouterBench, RouterBench, vendor leaderboards, Vals Finance Agent | `routing_aggregate_results` | Card and category priors |
| C. Rubric or expert-graded deliverables | GDPval (occupational deliverables graded against human experts) | Goals with rubric claims; results as aggregates or items | Priors only. Never "verified" (a rubric is not an execution check) |
| D. Paper or PDF as the only source | `gdp.pdf` | Parse, then handled as B or C | Same, after extraction review |

### 3.1 Steps

0. **Locate and register the source.** Put the file in a new, empty folder outside the repo; record its path, hash, licence and
   publication date. Licence unknown means "blocked until licensed", as the ingestion plan already says for other sources.
1. **Read it before designing.** Extract: task count, occupations or sectors, how a deliverable is scored (win, tie, rubric points),
   which models were run and with what harness, whether per-task results are published or only per-occupation percentages.
2. **Parse to a local, reviewable file** (CSV or JSON next to the PDF). The importer then reads local files only, never the PDF or the
   network. Tables go through a script, not by hand. Record the extractor name and version on every row (hard rule 2).
3. **Map tasks to goals.** One abstract root per family (for example "produce a professional deliverable"), occupations as the next
   level, specific tasks below, joined by `SPECIALIZES`. Edges from an importer get `decider = benchmark_import`.
4. **Add task categories.** The twelve categories are coding-heavy. Add the families we actually sell into (for example
   `admin_office`, `sales_crm`, `support`, `finance`, `research`) with name patterns for their benchmarks. Extend, do not rename.
5. **Write the goal contract.** For each imported goal, link guarantee claims that restate the benchmark's pass condition
   (for a rubric benchmark: each rubric line is a claim). Mark the check as `rubric` or `human-graded`, so the status stays `open`
   or `contested`, never `verified`, until an execution check exists.
6. **Write results.** Per-item results become observations with `dedupe_key` and `item_created_at` (contamination: an item
   published before a model's training cutoff). Aggregates go to `routing_aggregate_results`. Model cards come from the catalogue.
   Decide once how a win, a tie and a loss against a human map to pass or fail, record that decision, and keep wins-only and
   wins-plus-ties as separate series, because leaderboards report both.
7. **Dry-run, then apply.** Without `--apply` the command reports what it would write. Review the manifest (counts, exclusions,
   duplicates) before any write. A production write needs the founder's go, as with the playbook seed script.
8. **Refit and validate.** Re-fit and check the held-out ordering as for LLMRouterBench: fit without a model, predict its order,
   compare. Do this per category, not only overall. Ship only if the held-out agreement beats the current prior.
9. **Proving tests ship with the code** (hard rule 7): importer idempotency, dedupe, exclusion reasons, the win/tie mapping, and
   that benchmark evidence never turns a claim verified.

### 3.2 Which benchmarks, in order

| Order | Benchmark | Family | Kind | Why |
|---|---|---|---|---|
| 1 | SWE-bench / SWE-rebench, DS-1000, BIRD, IFEval, MultiPL-E | code, SQL, instructions | A | Already imported or measured; keeps coding routing honest |
| 2 | tau2-bench (vendored) | support, tool use | A | Checks the database state; gives per-task pass rates and `pass^k` |
| 3 | GDPval and GDPval-AA | professional deliverables | C | Broad occupations; the nearest public proxy for admin and project work |
| 4 | Vals Finance Agent, CRMArena-Pro | finance, sales | B / A | Per-category cost and score; CRMArena-Pro has per-task checks |
| 5 | TheAgentCompany, Toolathlon, WorkArena | admin, office, tools | A | Long multi-tool tasks, where cheap models are weakest |
| 6 | OpenRouter web-search benchmark, openbenchmarks | search engine x model | B | The only public data on tool choice moving the result |
| 7 | LLMRouterBench (already), RouterBench | general | B | Cross-model priors |

No public project-management benchmark exists. We would build one from checkable outcomes (tickets created with required fields,
no dependency cycles, estimates within capacity, tracker state matches the plan).

### 3.3 Risks

- **A rubric is not a check.** Ingesting GDPval as if it were executable would break the "only execution counts" rule and
  reproduce the acceptance problem (8 of 25 wrong fixes let through).
- **Dilution and bunching.** The LLMRouterBench rebuild flattened the card regression because only 5 of 31 datasets were code.
  Adding non-code data can do the same to the coding prior. Fit per category, and compare before and after.
- **Contamination.** Public items may sit in training data. Use `item_created_at` and keep hidden tests where we can.
- **Licence and provenance** per source; PDFs especially.
- **Tool and harness are not yet a dimension** of an observation (see 4.4).

## 4. How routing works on the framework

### 4.1 Layers

1. **Goal.** Triage decides whether a lookup helps. Hybrid retrieval plus a judge picks the goal and expands up and down the
   `SPECIALIZES` hierarchy. No match: a virtual goal keyed by the request's category and its nearest parents.
2. **Procedure fit.** The applicability cascade rules out any procedure with a violated precondition. This is a filter, not a score.
   (Retrieval fuses signals by RRF; applicability is the opposite on purpose.)
3. **Procedure rank.** Among those that fit, order by computed evidence: verified state, utility, cost, staleness.
4. **Unit choice per step.** A unit is a model and a scaffold (`model|scaffold`). The router estimates P(the check passes) per goal and unit.
5. **Ladder.** Choose the cheapest sequence of tries that meets a reliability target.
6. **Runtime.** Run rung one, run the check, stop on a pass or move to the next rung. Endpoints fail over. Budgets and policy apply first.
7. **Learning.** Every outcome is an observation on the goal; refits update the model.

### 4.2 What the estimate is made of

P(pass | goal, unit) is hierarchical Bayesian. For a goal with few runs it borrows from, in order: the goal's own runs, its
ancestors in the `SPECIALIZES` graph, the category's pool, public per-item and aggregate results (4.3), and the model's card
(release date, size, open weights, price) for a model nobody has run. This is what stops evidence thinning across many variants:
variants share their parents' evidence.

The ladder is exact. For each rung k, reach_k is the chance earlier rungs were rejected; expected cost adds, per rung, the cost
of an accepted-correct answer, a rejected-or-wrong one, and the check. It accounts for the check's false-accept and false-reject
rates, and an instance-difficulty term shared across rungs, so a failure on rung one raises the odds the task is hard. The chosen
ladder maximises value minus expected cost minus a penalty for accepted-wrong answers, subject to a reliability chance constraint
(P(P(ok) >= target) >= confidence). Thompson sampling picks among near-ties. If no ladder meets the target, the most reliable one
is returned and marked as not meeting it.

Step-level routing values a step by the whole run: a step also needs every later step to succeed, so a cheap model on step two is
judged by what it does to step three.

### 4.3 Where benchmarks enter

- Per-item results: observations on our goals, or on evidence items under a benchmark node.
- Aggregates: a binomial likelihood per (benchmark node, model, scaffold).
- Model cards: a prior for models with no results.
- Category: the triage judge names the request's category and the plan ranks for it, so a SQL request and a bug report get different rankings.

### 4.4 What is not in it yet: harness and tools

The unit today is model plus scaffold, so a harness can be encoded in the scaffold string (the replay already compared kell's step
agent with Claude Code on the same model). Tools are not a dimension: a search engine, an MCP tool or a CRM binding is not in the
unit key, and no public result is stored against one. To route on model x harness x tools together:

1. Define the unit as `(model, harness, toolset)`, with the toolset a sorted, hashed list of tool identities and versions.
2. Add it to the observation and aggregate keys (a migration; existing rows read as the empty toolset).
3. Make the same field a step-binding property of a procedure, so a procedure's configuration and the unit it ran on are one record.
4. Seed it with the one public dataset that varies tools (OpenRouter's engine-by-model grid), then our own runs.
5. Fit it as an interaction only after enough cells exist; until then keep tool effects as a per-category offset.

## 5. Built, measured, yet to do

| Area | Built | Measured | Yet to do |
|---|---|---|---|
| Primitives (goal, claim, procedure, evidence) | Yes, bi-temporal, scoped, audited | Memory facts 97% accurate | Part-of link between a step and a goal id; contracts on steps |
| Goal abstraction | `SPECIALIZES` DAG, retrieval expansion, routing ancestry | - | Abstract roots for non-coding families; review of auto-linked edges at scale |
| Library contents | Local `.kell` libraries; ingestion pipelines for server goals (not re-checked what the live database holds today) | Same-repo fixes lifted right-cause 27 to 42 of 96 (p = 0.023) | Procedures in non-coding families; the server library held no procedures when the playbooks shipped (28 Oct), so check it first |
| Benchmark import | Task import, public-result import, dedupe, contamination field | LLMRouterBench prior 0.60 held-out Spearman, not shipped | GDPval and other rubric benchmarks; PDF extraction step; per-source licences |
| Routing | Bayesian fit, exact ladder, categories, day-one new models, local refits | 49% cheaper on 150 code tasks at 3 fewer solved; 2x slower | Per-category validation; harness and tool dimension; utility in routing; repeat runs |
| Checks / acceptance | Project tests, reviewer v2, test-first | Default let 8 of 25 wrong fixes through | A rule meeting the 2-of-25 bar; checks for non-code families |
| Delegation | `call_model`, policy, budget, ledger, Delegator rooms | 7 pilot calls, $0.004 total | `delegate_task`, job status, approval tools; server-side runs |
| Evidence display | Ledger | - | Per-task receipt (task done by kell, API-equivalent cost) |
| Distribution | Hosted MCP, `kell-mcp` package (checks pass, not published) | - | npm publish; backend redeploy; two profiles (assistant, coding agent) |
| Non-coding measurement | - | None | A project-management benchmark; tau2, CRMArena-Pro, GDPval runs |
| Claims we may make | Same-benchmark cost reductions with limits | - | The kell-vs-Opus series; any real-session study |

## 6. Decisions needed

1. Path to `gdp.pdf` and its licence terms.
2. Win, tie and loss mapping for expert-graded benchmarks, and whether rubric lines become claims.
3. New categories to add, and which families come first for the pilot.
4. Whether to add the toolset to the unit key now (a migration) or after the first non-coding results.
5. Founder go-ahead for any production write.
