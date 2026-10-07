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
