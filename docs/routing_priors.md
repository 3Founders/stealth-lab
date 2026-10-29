# Routing priors: model cards and public evidence

How the recommender (`backend/app/routing`) knows something about a model before anyone has run it on
our Goals, and how to load that knowledge. Design: [plan_2026-10_priors_library_survey.md](plan_2026-10_priors_library_survey.md) §2.
Measured results: [routing_priors_eval.md](routing_priors_eval.md).

## What enters the model

Everything goes into the one Bayesian model (`model.py`), either as a prior or as a likelihood:

| Input | Where it lives | How it enters |
|---|---|---|
| Model cards: release date, training cutoff, size, open weights, reasoning, context, list price, family | `routing_model_cards` | A model with no fitted predecessor starts from `card_beta . x + family effect` (`cards.py`), not from zero |
| Per-task public results (SWE-bench experiments, SWE-agent trajectories) | `routing_observations`, `source = public_import`, `check_kind = benchmark` | Ordinary observations. A public task that is not one of our Goals becomes an *evidence Goal* under `benchmark → repo` nodes (`evidence.py`); it is never stored as a posterior |
| Leaderboards that publish only a percentage (RouterBench) | `routing_aggregate_results` | A binomial likelihood on the benchmark node, with task difficulty integrated out |
| Task-size features of the reference patch | `routing_evidence_items.features` | Appended to the Goal's `phi` next to the embedding PCA (`goal_features.py`) |
| Task date vs a model's training cutoff | `routing_observations.item_created_at` | `kappa >= 0` added to the logit of contaminated benchmark tasks, learned, never applied to live tasks |

A fit made without cards behaves exactly as before. So does a database the migrations have not reached
yet: the new reads return nothing.

## Loading it (operator)

1. Apply migrations `147_routing_priors.sql` (control) and `148_routing_priors_search.sql` (`--target search`).
2. Download the public files into one folder. All are public and need no key:

   | Folder / file | Source |
   |---|---|
   | `openrouter_models.json` | `https://openrouter.ai/api/v1/models` |
   | `experiments/` | `github.com/SWE-bench/experiments`, a sparse checkout of `evaluation/*/*/{metadata.y*ml,per_instance_details.json,results/results.json}` |
   | `swe_items/{verified,lite,multilingual}.parquet` | HF `princeton-nlp/SWE-bench_Verified`, `princeton-nlp/SWE-bench_Lite`, `SWE-bench/SWE-bench_Multilingual` (test split) |
   | `swe_rebench/*.parquet` | HF `nebius/SWE-rebench` (test split) |
   | `swe_agent_traj/*.parquet` | HF `nebius/SWE-agent-trajectories` |
   | `routerbench/routerbench_0shot.pkl` | HF `withmartian/routerbench` |
   | `llmrouterbench/summary.jsonl` | HF `NPULH/LLMRouterBench` (`bench-release.tar.gz`, 1.3 GB, MIT), summarised by `python scripts/llmrouterbench_summary.py bench-release.tar.gz > llmrouterbench/summary.jsonl` |

3. Run the import as a dry run first. It reads only those files plus `ingest_task_goals` (one query), and writes nothing:

   ```bash
   python -m app.ingestion.admin routing-import-public /path/to/data
   ```

   Then write, and fit:

   ```bash
   python -m app.ingestion.admin routing-import-public /path/to/data --apply
   ```

   ```bash
   python -m app.ingestion.admin routing-refit
   ```

   Re-running is safe: cards and items are upserted, and observations and aggregates carry a dedupe key.
   The OpenHands trajectories are **not** imported here. The ingestion pipeline already records them.

## LLMRouterBench (2026-10-28)

[LLMRouterBench](https://arxiv.org/abs/2601.07206) (Li et al., ACL Findings 2026) graded 40 models on 31 datasets, with
548,059 answers in all. It covers maths, code (HumanEval, MBPP, LiveCodeBench, SWE-bench Verified), logic, knowledge,
medicine and finance, emotion and tool use. Its models include:

- GPT-5, Claude Opus 4.1, Gemini 2.5, GLM-4.6, Kimi K2, DeepSeek and Qwen3;
- about 20 open 7-9B checkpoints.

**How it enters:** like RouterBench, as one aggregate (k of n) per (dataset, model) on a benchmark node
`llmrouterbench:<dataset>` (`public_evidence.llmrouterbench`). Three rules apply:

- a subset run (`hle-subset_500`) counts only where the model has no run on the full set;
- OpenRouter's own router is skipped, because it is not one model;
- SWE-bench and τ²-bench are marked `llmrouterbench-agent` (run in an agent loop) and the rest `direct`.

**Cards for models no catalogue lists:** a model without an OpenRouter card gets one read off its name
(`public_evidence.name_cards`): size, open weights and family. Without that, the card regression would learn
nothing from those models.

## A model added or removed later (2026-10-28)

A candidate's starting ability is its card's regression plus its family effect (`cards.prior_mean`), learned across
every model with public results. For each candidate that neither the bundle nor the database has a card for,
`recommend()` now tries two sources in order (`app/routing/catalog.py`):

1. **OpenRouter's live catalogue**, fetched at most once a day per process and retried after an hour if the fetch
   fails. It gives the release date, prices, context, reasoning and open weights.
2. **What kell sends about it:** `model_constraints.cards`, built from its exec.json profile (the prices it pays). The
   size comes from the name. A family named without its provider (`glm-flash`) matches the one fitted family with
   that name (`z-ai/glm-flash`), so `glm-6-flash` starts near the GLM flash models.

Either way, the plan's `evidence.new_cards` says where the card came from.

Before this, such a model had no list price and was **excluded** from the plan. Dispatch still tried it, but last
and unranked.

**Removing a model** needs nothing: it stops being a candidate. Each model's terms are its own, so no other model's
estimate moves (test: `tests/test_routing_catalog_offline.py`).

`STEALTH_ROUTING_CATALOG=off` turns the live lookup off; tests run with it off.

## One ranking per kind of work (2026-10-29)

Every benchmark sits under the category of work it measures (`app/routing/task_categories.py`):
category, then benchmark, then repo, then task. Each category node learns where its tasks lie in skill space, so a
model's chance on a category is its ability plus its skills along that node.

**At query time:**
1. The triage judge (JEV, `find_ways_triage@v2`) names one of 12 categories.
2. The hook sends it as `model_constraints.category`.
3. The plan's prior is drawn under that node (`service._category_parent`).

| Category | Fed by now |
|---|---|
| repo_change | SWE-bench Verified, Lite and Multilingual; SWE-rebench; SWE-agent; LLMRouterBench SWE-bench |
| script | LiveCodeBench, HumanEval, MBPP, ArenaHard coding |
| agent_tools | tau2-bench |
| math | AIME, MATH500, LiveMathBench, MathBench, GSM8K, the MMLU maths subjects |
| knowledge | MMLU-Pro, GPQA, SimpleQA, HLE, MedQA, FinQA, ARC, RouterBench MMLU |
| reasoning | BBH, KOR-Bench, Knights and Knaves, ARC-AGI, Winogrande, HellaSwag |
| writing | ArenaHard, MT-Bench, the dialogue and summarising sets |
| frontend, sql, data_science, terminal | no public data yet (Design2Code/WebSight, BIRD, DS-1000, Terminal-Bench to import): the population prior |
| code_question | none: an answer has no check, so these stay with the session's model |

Ranking stays cost-aware (ladder.py): the plan is the cheapest sequence of tries that is as reliable as the session's
own model. That is LLMRouterBench's CostSave point, with the session's model in place of its best single model.

**Active since 2026-10-29.** The shipped bundle is fitted without LLMRouterBench, on SWE-bench, SWE-rebench,
SWE-agent, RouterBench and OpenRouter cards (`build_prior_bundle.py --sources`). It has category nodes for
repo_change, script, math, knowledge, reasoning and writing. A fit with LLMRouterBench was tried first. Its flattened
card prior failed `test_routing_check_calibration`: after bad audits, Opus went first only 43% of the time, against
the 60% required. This one passes. agent_tools has no RouterBench data, so it falls back like the four empty
categories.

**Only the bundle has category nodes, so far.** A database refit stores no draws for the synthetic nodes, so a
deployment with its own fit routes as before until that is added. The same holds for the Way codes.

## Probes: measuring a model instead of guessing (2026-10-29)

`app/routing/probe.py` places a probed model in the ranking from its score on a fixed problem set that anchor models
were graded on. `scripts/routing_probe_modal.py` runs a model there on Modal, grading with LiveCodeBench's own grader
(`scripts/lcb_grader`, vendored, MIT).

**Not shipped as data:** the LiveCodeBench probe (100 problems, 37 anchors) lines up poorly with the fitted
repo-work ranking (r = 0.55 on the 5 anchors the shipped fit knows). Without `data/probe_lcb.json` and
`data/probe_results.json`, the code does nothing. The planned probe for `repo_change` runs about 15 SWE-bench
Verified tasks through kell's step agent, so the results land as ordinary observations.

## Fitting at this size

Exact NUTS costs about a thousand gradients per draw, and each gradient touches every attempt at every
quadrature node. On 39k public results it ran for more than an hour per fit on 4 cores without finishing,
and the full import (about 118k results) would take far longer. `fit.choose_method` therefore uses:

| Condition | Method |
|---|---|
| latents ≤ `STEALTH_ROUTING_NUTS_MAX_LATENTS` (20,000) **and** (attempts + aggregates) × nodes ≤ `STEALTH_ROUTING_NUTS_MAX_WORK` (150,000) | exact NUTS, as before |
| otherwise | low-rank Gaussian VI (`STEALTH_ROUTING_VI_RANK` 32, `STEALTH_ROUTING_VI_STEPS` 6000) |

Measured on the 39k SWE-bench Verified results (4,007 latents): VI took 817 s, and the loss changed by
0.07% over its last tenth of steps. The diagnostics record that tail change, and a value above 1% means
the fit has not converged. `--method nuts` still forces the exact sampler.

## Updating between nightly fits

A model's ability used to move globally only at the nightly fit. Until then, what one Goal learned about a
model stayed with that Goal. `model_update.py` closes this:

- The joint model already lets ability drift after the fit (`theta_last + drift`). The update replaces
  the drift's prior draw with its posterior, from every **public** observation since the fit, on any Goal.
- It is exact: one dimension per model, solved on a grid, and aligned with the stored draws.
- The job `routing_model_update` runs at most once an hour, queued by public observations. You can also
  run it by hand with `admin routing-model-update`.
- The next nightly fit absorbs the same data, and the update starts again from zero.
- Private observations never enter it. They still update their own Goal through the local refit, as before.

## Routing without a production fit or a global Goal (2026-10-08)

**The bundled prior.**
- `scripts/build_prior_bundle.py` fits the model once on the public files only:
  - SWE-bench experiment results;
  - the benchmark items' fix sizes;
  - the OpenRouter cards.

  It uses no database and no link to our Goals.
- The result ships in `backend/app/routing/data/` (`prior_bundle.npz`, `prior_bundle.json`, `prior_cards.json`).
- The service uses it whenever the database has no fitted parameters, and its cards fill in any model the database
  has no card for. A database fit, once one exists, always wins. Bundle version 0 never collides with a database
  version.

**Every task gets a plan.**
- When `find_ways` resolves no global Goal, the task is planned on a key of its own, in this order
  (`server._plan_case`):

  | case | when | key | prior set by |
  |---|---|---|---|
  | global Goal | `resolved` with a Procedure | the Goal | its stored posterior, or its parents + embedding |
  | `library` | a `.kell` library entry the judge says *matches* (not a failed attempt) | uuid5(repo id + entry id) | that entry's own fix size |
  | `repo` | no match, but the repository has an identity | uuid5(repo id) | this repository's median fix size |
  | `generic` | nothing else | one shared key | the population mean |

- In every non-Goal case, `find_ways`' `suggested` Goal (on `ambiguous`) is added as a parent. It pulls the prior
  toward what is known about that Goal.
- **Fix sizes:** `kell library payload` sends them as `task_features`, counted from
  `library/solutions/*.diff` (files, hunks, lines added/removed, languages, packages). Only numbers are sent,
  never the diff. They go through the fitted regression `W`, the same way a Goal's features do.
- **Logging:** a virtual key's decisions and attempts are logged like a Goal's, so these all work unchanged:
  - `report_result` and its next-model replies;
  - the repository's `routing.md` ROUTE/OBS lines, keyed to the virtual id (and naming the library entry);
  - later fits.
- **The plan reports its case:** `model_plan.case = {kind, ref}`, and `evidence.goal_posterior` says
  `case prior (...)`.

## Semantic codes for Ways (2026-10-08)

A Way's embedding maps to a short code, such as `c07.3`, from a two-level code tree: 24 coarse groups, each with up
to 8 sub-codes (`app/routing/semantic_codes.py`). Ways with the same code do similar work.

- **In the fit:**
  - each benchmark task's Goal gets its Way's code as a parent, and the code gets its coarse group;
  - the bundle stores the draws of every code node (`code_x`, meta `code_nodes`);
  - a plan for a Way with a code adds that node as a parent of its case prior (`service._code_parent`). The code
    itself is used when the fit saw it, else its coarse group.
- **Building:** `scripts/build_prior_bundle.py --codes <procedure_vectors.npz>` builds the codebook from the
  embeddings of the Ways that achieve our benchmark Goals, which were exported once read-only. It then fits on
  every public source the production import reads.
- **Coding a repository's Ways:** `kell library link` sends each uncoded Way's name and steps to
  `POST /routing/codes` once (signed in). The server embeds them with the codebook's model, answers with the code
  and the vector, and stores nothing. The code is written on the `W` line (`code=`); the vector stays in
  `.kell/index/way_vectors.json` (gitignored). A later codebook version re-codes from the kept vectors, so the
  text is not sent again.
- **Updating:**
  - **a Way's embedding:** never; new text is a new Way;
  - **the codebook:** a rare, versioned rebuild; `semantic_codes.remap` maps old codes to new ones;
  - **outcomes per code:** every result, through the repository's `routing.md` counts and the refits.
- **Model mismatch:** if the deployment's embedding model differs from the codebook's, no code is given rather
  than a wrong one.

## Defaults the plan uses

- **The caller's own model.** `find_ways(..., my_model="claude-sonnet-4-5")` makes it a candidate, on the
  scaffold named by the MCP client. The plan then says whether to keep the task or hand it off.
- **The deployment's models.** `STEALTH_DEFAULT_MODELS="gpt-oss-120b|direct,qwen3-coder|openhands"` lists
  the units the deployment can run for anyone. It is unset by default, which means inert.
- **Connections** (`app/providers`), as before.

A unit needs a price. If `routing_prices` has none, the card's list price is used, and the reply lists
that unit under `evidence.list_priced`.

## The reply

`model_plan` keeps its old fields and adds (plan §5.2):

```json
{"basis": "prior", "fit_id": 12, "as_of": "2026-10-07T03:10:00+00:00",
 "steps": [{"step": "*", "ladder": [{"unit": "gpt-5-mini|claude-code", "p_ok_mean": 0.61,
            "p_ok_q05": 0.48, "p_ok_q95": 0.73, "cost_mean": 0.042}]}],
 "model_basis": {"gpt-5-mini|claude-code": "card"}}
```

- `basis` is `posterior` once the Goal or a candidate model has observed runs, and `prior` otherwise.
- `model_basis` gives, for each unit, where its ability comes from: `fitted`, `predecessor`, `card` or `population`.
