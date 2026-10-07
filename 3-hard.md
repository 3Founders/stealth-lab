# 3-hard: handoff for the four October 2026 forks (priors, library, survey, local eval)

**Read this first.** It is written for whoever picks this up next, human or agent. It says what was
built, where the code is, how to check it, and what is left, in priority order. Everything listed as
done is on `main` as of the merge commit that adds this file.

The design they all implement is
[docs/plan_2026-10_priors_library_survey.md](docs/plan_2026-10_priors_library_survey.md). Its §5
contracts are shared by all four, so read §0, §5 and §6 before changing any of them.

## Why there are four forks

| Fork | Branch, already merged here | Goal |
|---|---|---|
| A | `feat/routing-priors` | `find_ways` picks a model **by default** and knows something before any of our own data: model cards and public benchmark results enter the Bayesian router |
| B | `feat/library` | `.stealth/library.md` (this repo's solved problems + diffs), `routing.md`, SUMMARY and indexes; `find_ways` takes the repo's own library and local results |
| C | `feat/survey` | A deterministic `stealthlab-mcp survey` scanner that handles every repo shape (monorepos etc.) and writes cited facts + repo identity |
| D | `feat/local-eval` | An experiment that proves (or disproves) that local `.stealth` context alone pays off, with enterprise and global knowledge as extra arms |

How the merge was done:
- Order: C, B, A, D.
- Conflicts were resolved by keeping both sides:
  - `hook.mjs` keeps survey's `readClaims(cwd, prompt)` and library's `extra`;
  - `prompts.py` has both survey's per-unit claims pages and library's library-first lookup;
  - `server.py` has both library's `repo_identity` / `library_rows` / `route_obs` and A's `my_model`;
  - `service.py` keeps both the local-results reweighting (B) and the card / per-rung evidence (A).
- Tests on the merged tree:
  - backend routing, MCP and library: 88 passed;
  - fast prior tests: 20 passed;
  - npm: 185 passed, 4 skipped, 0 failed.

## Fork A: model priors and default routing

**Docs:** [docs/routing_priors.md](docs/routing_priors.md) (operator guide).
**Code:** `backend/app/routing/`.

### Mechanism, in one paragraph
- **The model.** One hierarchical Bayesian IRT model (`model.py`):
  `logit P = θ[m] + γ[s] + δ[m,s] − b[g] + ⟨a[g],z[m]⟩ + ⟨c[π],z[m]⟩ − ε`.
- **New in this fork:**
  1. A model with no fitted predecessor starts from its **card** (`cards.py`): `θ = card_beta·x + family`.
     - `x` = release date, price, size, open weights, reasoning, context.
     - The coefficients share one shrinkage scale, and the family effects are centred.
  2. **Public per-task results** are ordinary observations (`public_evidence.py`, `evidence.py`).
     - Public tasks that are not our Goals are "evidence Goals" under `benchmark → repo` nodes.
     - They are never stored as posteriors.
  3. **Leaderboard percentages** enter as a binomial likelihood (`model.aggregate_loglik`).
  4. A learned **memorisation bonus** `κ` applies to benchmark tasks published before a model's
     cutoff. It is never applied to live tasks.
  5. **Task-size features** of the reference patch are appended to the Goal's φ (`goal_features.py`).
  6. A **between-nightly model update** (`model_update.py`):
     - each fitted model's ability drift gets an exact grid posterior from **public** results since the
       fit, across Goals;
     - it runs hourly as the job `routing_model_update`.
  7. **Inference that scales** (`fit.choose_method`):
     - NUTS only while latents ≤ 20k **and** (attempts × quadrature nodes) ≤ 150k;
     - otherwise low-rank Gaussian VI;
     - measured: 39k results in 817 s, converged. NUTS ran over an hour per fit without finishing.
- **Default plan.**
  - Candidates: `find_ways(my_model=...)` (the caller's own model, scaffold = its MCP client), plus
    `STEALTH_DEFAULT_MODELS` (the deployment's models), plus connections.
  - Card list prices are used when `routing_prices` has none.
  - The reply adds `basis` (prior/posterior), `fit_id`, `as_of`, `steps[].ladder[]` with
    `p_ok_mean/q05/q95` and `cost_mean`, and `model_basis`.
- **Import.** `admin routing-import-public DIR [--apply]`:
  - Sources: OpenRouter cards, SWE-bench experiments (Verified, Lite, Multilingual), SWE-rebench task
    features, SWE-agent trajectories, RouterBench.
  - It is a dry run by default. The dry run on local files found 384 cards, 117,731 per-task results on
    70 models, and 946 aggregates.
  - The OpenHands trajectories are **not** imported, because the ingestion pipeline already records them.

### Key finding that changed the plan
- Our 20.6k Goals are **SWE-rebench** tasks, not SWE-bench Verified: only 14 IDs overlap.
- So SWE-bench cross-model evidence lands on evidence Goals, not directly on our Goals.
- The SWE-agent trajectories overlap 2,275 of our Goals.

### Remaining for A, in order
1. **Run the evaluation** (`backend/scripts/routing_priors_eval.py`) and write
   `docs/routing_priors_eval.md` with the numbers, losses included. It never finished on the 8 GB laptop.
   - Use a machine with ≥16 GB RAM and ≥8 cores.
   - It needs the public files described in `docs/routing_priors.md`, in one folder.
   - Run each command from `backend/`:

     ```bash
     python scripts/routing_priors_eval.py --data DATA --out ../docs/routing_priors_eval --exp lomo --fast --folds 5 --fold K
     ```

     ```bash
     python scripts/routing_priors_eval.py --data DATA --out ../docs/routing_priors_eval --exp lomo --fast --folds 5 --merge
     ```

     ```bash
     python scripts/routing_priors_eval.py --data DATA --out ../docs/routing_priors_eval --exp routing --fast
     ```

     Then the same for `--exp temporal`, `--exp lobo` and `--exp sbc`.
   - Run the first command once per fold (K = 0–4); it is resumable, since each fold writes its own file.
     The second command combines the folds.
   - **What the experiments compare:**
     - `lomo` and `temporal`: cards vs. the old population prior vs. the family average.
     - `routing`: RouteLLM APGR/CPT, and the frontier/AIQ against kNN, RouteLLM-MF, RouteLLM SW-ranking,
       best single, cheapest, and ours without features.
     - `lobo`: hold out one benchmark (fit on two SWE-bench splits, predict the third).
     - `sbc`: calibration of the new terms.
   - **Ship rule (plan §2.5):** the cards must beat the old prior and the family average on held-out log
     likelihood, and ours must beat the baselines on APGR. If not, fix the model first. Do not ship the
     prior silently.
2. **VI vs NUTS agreement.** On a subsample small enough for NUTS (≈120 items × 51 models), fit both and
   compare held-out log likelihood. That justifies using VI in production.
3. **Re-run the slow existing fit tests** (`tests/test_routing_fit_offline.py`). They were not re-run after
   the changes, because they take tens of minutes under load.
4. **Production (needs the owner's OK: it writes to Neon):**
   1. Apply migrations `147_routing_priors.sql` (control) and `148_routing_priors_search.sql` (`--target search`).
   2. Download the files, then run `routing-import-public DIR` and check the dry-run report.
   3. Run it again with `--apply`.
   4. Run `admin routing-refit`. It will choose VI; check that `diagnostics.elbo_tail_rel_change` is < 0.01.
   5. Optionally set `STEALTH_DEFAULT_MODELS`.

   Until a fit exists, `find_ways` model plans say `not_ready`. Production has 0 observations today.
5. **Known limitations:**
   - The per-Goal local refit costs about 45 s of CPU per observation. Batch it before traffic grows.
   - Model keys are matched by `cards.canonical_key`: snapshot dates, -preview and -instruct are
     stripped, and Claude word order is normalised. Check the aliases when a new provider naming
     scheme appears.

## Fork B: library.md and routing.md

**Docs:** `docs/stealth_library.md`.
**Code:**
- `backend/app/stealth/library.py`, `packaging/npm/lib/library.mjs` and `library_cli.mjs`
  (`stealthlab-mcp library ...`), with shared fixtures in `packaging/npm/test/fixtures/library`;
- `backend/app/services/library_context.py`;
- `find_ways` args `repo_identity` / `library_rows` / `route_obs`;
- local OBS = importance reweighting of the stored joint draws in `routing/service.py`
  (`local_obs_loglik`, `reweight_by_local_obs`);
- migration `146_benchmarks_repo_index.sql`.

**Remaining:**
1. **End-to-end quality is not measured.** That is fork D's job (below).
2. **Survey → library:** pass `meta.json` `repo_identity` from the survey into `find_ways` (the
   `stealthlab-mcp library payload` path), and make sure `strength: weak` identities never match other
   repos. A merged test that covers both forks together is still missing.
3. **Tests in a worktree** need `STEALTHLAB_MCP_TOKEN=offline-test-dummy` (there is no `.env`). The
   `exec_runtime` timing tests fail under heavy CPU load; that is unrelated.

## Fork C: survey scanner

**Docs:** `docs/survey_eval_2026-10.md`.
**Code:**
- `packaging/npm/lib/survey/` (in the npm client, so users need no Python);
- `stealthlab-mcp survey [path]`;
- the gold/held-out scripts `packaging/npm/scripts/survey-gold.mjs` and `survey-*-truth.json`.

**Remaining:**
1. **Bug: history mining is anchored to today.** It uses `--since=2.years.ago`, relative to TODAY, so an
   older checkout or a repo idle for 2+ years mines nothing. Fix: anchor `since` to HEAD's commit date.
   Fork D passes `since` explicitly, so it is unaffected.
2. **Each held-out round has found new repo-shape gaps.** Run another held-out set:

   ```bash
   node scripts/survey-gold.mjs <dir> --set heldout2 --seed N
   ```

   Then fix what it finds.
3. **Publish to npm.** The `survey_repo` prompt calls `npx -y stealthlab-mcp survey`, which works only
   after `stealthlab-mcp` is published to npm (the owner's step).

## Fork D: local vs. global evaluation

**Code:** `experiments/local_eval/`. Frozen design in `experiment.json`:
- dataset SWE-rebench-V2;
- 322 scored tasks with a cap of 8 per repo;
- agent model gpt-oss-120b;
- four arms: none / local `.stealth` / + enterprise / + global.

It has:
- task builder, notes per arm (`build_notes.py`, `history.py`; the `survey` provider runs fork C's
  scanner at the base commit);
- blind grading, statistics, analysis;
- the valid-task rule (gold resolves, empty patch does not), fixed before grading;
- 20+ offline tests.

**Remaining:**
1. **Write `experiments/local_eval/PREREGISTRATION.md`.** `experiment.json` and `valid_tasks.py` refer to
   it, but only `experiments/ds1000/PREREGISTRATION.md` exists. Hypotheses, sample size, primary metric
   and analysis plan must be frozen **before** any arm is run.
2. **Not run end to end.** Run the build, then the arms, then grading, then analysis, on a machine with
   enough RAM: `build_tasks.py` alone used about 2 GB on the laptop.
3. **Report** effect sizes with confidence intervals and p-values, negative results included.

## Ground rules for whoever continues
- **Secrets:** never print or commit keys or DSNs. Never commit `.env`, `.env.bak*` or `.neon_shards.env*`.
- **Data use:** no Vertex/GCP. Keep Neon egress minimal: export once to local files.
- **Quality:** ship only measured improvements, and report losses honestly.
- **Migration numbers on main:** up to 139 used, 140 is a proposal, 146 is library, 147/148 are routing
  priors. Next free: **149**.
