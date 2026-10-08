# Routing priors: evaluation results (2026-10-07/08)

What `docs/plan_2026-10_priors_library_survey.md` §2.5 asks before the model-side prior ships: the cards must beat
the old population prior and the family average on held-out log likelihood, **and** our router must beat the
baselines on APGR. Raw outputs: `docs/routing_priors_eval/*.json`. Everything below is from
`backend/scripts/routing_priors_eval.py` on public data only (SWE-bench `experiments` per-instance results,
SWE-bench Verified/Lite/Multilingual items, OpenRouter cards); no database.

**Verdict: half of the ship rule passes.** The cards are a clear, significant improvement for a model we have
never seen (cold start). On routing, the corrected run (with CIs) shows **no router, ours included, distinguishable
from random routing** on this pair (ours APGR 0.463 [0.308, 0.604]). The APGR condition is therefore not met, and
the prior must not be presented as a routing win.

## How it was run (deviations from the handoff)

- Machine: 20 cores, 15.7 GB RAM with ~1-2 GB free (other applications), so every step ran under a memory guard
  at below-normal priority.
- `--fast` configuration (128 draws; VI 6,000 steps, rank 32), as the handoff's commands say.
- **`--method auto`** (new flag): each fit uses what production would choose for the same data
  (`fit.choose_method`), which at 39k observations is low-rank VI. The script's original default forces NUTS,
  which the handoff reports never finished; `--method nuts` still does that. Every fit's VI diagnostics are in the
  JSON; the loss changed by 0.07-0.08% over the last tenth of steps (converged by the < 1% rule).

## Cold start: leave models out (5 folds, 51 models, 39,441 observations)

Each fold's models are absent from the fit and predicted from their card only.

| predictor for an unseen model | held-out log lik / obs | Brier | AUC | mean abs error of a model's resolve rate |
|---|---|---|---|---|
| **cards** | **-0.435** | **0.139** | **0.887** | **0.128** |
| family average of fitted models | -0.488 | 0.159 | 0.845 | 0.158 |
| old prior (no cards) | -0.564 | 0.194 | 0.789 | 0.197 |

Cards vs old prior: **+0.130** nats/obs [95% CI 0.066, 0.195] (bootstrap over models). Cards vs family average:
**+0.053** [0.009, 0.110].

## Temporal split (19 models released on or after 2025-10-01 held out, 13,441 observations)

Cards vs old prior **+0.136** [0.107, 0.174]; vs family average **+0.073** [0.020, 0.125]. Log lik: cards -0.527,
family -0.599, old prior -0.663. Mean abs error of resolve rate: 0.219 / 0.248 / 0.314.

**Both cold-start conditions of the ship rule pass.**

## Routing on held-out items (half of Verified; mini-swe-agent units with published cost)

Strong/weak pair: claude-opus-4-6 (strong, 74.8%) vs deepseek-v3-2-reasoner (weak, 62.0%).

| router | APGR | CPT(50%) | CPT(80%) | AIQ | held-out log lik | AUC |
|---|---|---|---|---|---|---|
| ours | 0.464 | 0.448 | 0.908 | 0.778 | -0.552 | 0.785 |
| ours without task features | 0.476 | 0.532 | 0.836 | 0.777 | -0.597 | 0.724 |
| best single scorer (training resolve rate) | **0.490** | 0.556 | 0.816 | 0.776 | -0.588 | 0.704 |
| RouteLLM MF (re-implemented) | 0.448 | 0.536 | 0.828 | **0.786** | **-0.535** | **0.796** |
| RouteLLM SW ranking | 0.391 | 0.656 | 0.876 | 0.781 | -0.550 | 0.786 |
| kNN on item features | 0.383 | 0.560 | 0.924 | 0.759 | -0.553 | 0.759 |
| random | 0.419 | 0.628 | 0.776 | -- | -- | -- |
| oracle | -- | -- | -- | 0.902 | -- | -- |

(CPT = share of calls to the strong model needed to reach 50% / 80% of its gain; lower is better. Best single
model: gemini-3-flash, 78.8% at $0.35; cheapest: deepseek-v3-2-reasoner, 62.0% at $0.029.)

**Correction (2026-10-08): the APGR baselines in this table are not what they look like.** The "best single
scorer" gives every item the same score, so it cannot route item by item; its 0.490 is the APGR of whatever order
the tie-break leaves the items in, i.e. one random ordering. The "random" row is also a single draw (0.419). The
expected APGR of random routing is exactly **0.5** (quality grows linearly with the share sent to the strong model),
so the meaningful reading is: **no router here, ours included, is above random's expectation on this pair**, and
the spread between them is within what one random ordering produces. `routing_priors_eval.py` now reports random
as 500 orderings (mean, 2.5-97.5%), marks the constant scorer, and adds an item-bootstrap 95% CI and
P(APGR <= 0.5) for every router.

**Corrected run (`routing_priors_eval/apgr_v2/routing.json`, 2026-10-08, same data, split and fits):**

| router | APGR | 95% CI (item bootstrap) | P(APGR <= 0.5) |
|---|---|---|---|
| ours | 0.463 | [0.308, 0.604] | 0.71 |
| ours without task features | 0.476 | [0.347, 0.607] | 0.68 |
| RouteLLM MF | 0.448 | [0.290, 0.581] | 0.79 |
| RouteLLM SW ranking | 0.391 | [0.243, 0.522] | 0.96 |
| kNN on item features | 0.383 | [0.223, 0.508] | 0.96 |
| constant (best-single) scorer -- one tie-break ordering, not a router | 0.490 | [0.373, 0.646] | 0.51 |
| random, mean of 500 orderings | 0.501 | -- | -- |

AIQ is unchanged (ours 0.778, MF 0.786, SW 0.781, oracle 0.902).

**The APGR condition fails, and the reason is not the one first given.** Every CI contains 0.5, and every router's
point estimate is at or below random's: on this one pair (strong 74.8% vs weak 62.0%, 250 items) per-item routing
gains nothing measurable over sending a random share to the strong model, for our router and for the published
baselines alike. Ours is not worse than the baselines (its CI overlaps all of them) and its probabilities are good
(log lik second to MF, AUC 0.785), but "beats the baselines on APGR" cannot be shown here. A test that could show it
needs more items or pairs, or a pair whose models fail on different items.

## Leave one benchmark out (fit on two SWE-bench splits, predict the third)

| held out | with task features: log lik / AUC | without: log lik / AUC | rate error with / without |
|---|---|---|---|
| Verified | -0.671 / 0.732 | -0.737 / 0.660 | 0.200 / 0.223 |
| Lite | -0.564 / 0.690 | -0.587 / 0.633 | 0.099 / 0.107 |
| Multilingual (new repos) | -0.641 / 0.581 | -0.651 / 0.572 | **0.096 / 0.079** |

Task features help on every split's log likelihood; on Multilingual (unseen repositories and languages) they help
little and the model-rate error is slightly worse with them.

## Not finished

- **SBC (calibration of the new terms):** stopped twice when the machine ran critically low on memory (memory
  grows with each simulation, to ~3 GB). It is now checkpointed (`--sims-per-process`, `sbc_ranks.json`: each
  simulation's ranks are saved and a rerun skips them; each simulation has its own seed) and re-running in
  3-simulation processes.
- **VI vs NUTS (`scripts/routing_vi_nuts_agreement.py`, `vi_vs_nuts.json`):** 120 items x 51 models, 20% of
  cells held out. VI predicted better than NUTS (+0.077 nats/obs [0.065, 0.089]; AUC 0.914 vs 0.869), but the
  NUTS run **did not converge** in the `--fast` budget (max R-hat 2.66, min ESS 1.1), so this does not show that
  VI matches exact inference. It needs a longer NUTS run (non-fast: 400 warm-up, 250 samples x 2 chains; likely
  > 1 h on this subsample) before VI can be called justified.
- **Slow fit tests** (`tests/test_routing_fit_offline.py`, 3-hard.md fork A item 3): **6/6 passed** on 2026-10-08,
  each in its own process (4-6 min each; run together they exceeded this machine's free memory).

## What to do before shipping the prior

1. Decide what the routing claim is. The CIs are in (above): no router beats random on this pair. Either test on
   more pairs/items, where a difference could show, or ship the prior for its cold-start gain only and say plainly
   that routing gains are unmeasured.
2. Finish SBC and a converged NUTS comparison.
3. Only then import and refit (3-hard.md fork A item 4; the migrations are already applied).
