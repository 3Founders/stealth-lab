"""Does low-rank Gaussian VI predict as well as exact NUTS? (3-hard.md, fork A item 2.)

Production fits with low-rank VI once the data is too large for NUTS (fit.choose_method). That is only justified
if, where both can run, VI's held-out predictions are as good as NUTS's. This takes a subsample small enough for
NUTS (by default 120 SWE-bench Verified items x every model that ran them), holds out 20% of the observations at
random (seeded; the items and models stay in the fit, only those cells are hidden), fits the SAME model and data
with both methods, and predicts the hidden cells with the production prediction code
(routing_priors_eval.predict -> predict.unit_terms / success_given_eps).

    python scripts/routing_vi_nuts_agreement.py --data DATA --out ../docs/routing_priors_eval [--items 120]

Writes <out>/vi_vs_nuts.json: held-out log likelihood, Brier and AUC per method, the per-observation log-likelihood
difference VI - NUTS with a 95% CI resampling models, the correlation of the two methods' predictions and of their
posterior mean abilities, fit times and diagnostics. Same public data as routing_priors_eval.py; no database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routing_priors_eval as ev  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--items", type=int, default=120)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    conf = ev.cfg(args.fast)

    data = ev._restrict(ev.Data(args.data, ("verified",)), "verified")
    rng = np.random.default_rng(args.seed)
    items = sorted(data.items)
    rng.shuffle(items)
    keep = set(items[: args.items])
    sub_items = {iid: data.items[iid] for iid in keep}
    goal_of = {it["goal_id"]: iid for iid, it in sub_items.items()}
    obs = [o for o in data.obs if o["goal_id"] in goal_of]
    order = rng.permutation(len(obs))
    n_test = int(round(args.holdout * len(obs)))
    test_ix = set(order[:n_test].tolist())
    train = [o for i, o in enumerate(obs) if i not in test_ix]
    test = [o for i, o in enumerate(obs) if i in test_ix]
    models = sorted({o["model_key"] for o in obs})
    print(f"subsample: {len(sub_items)} items, {len(models)} models, {len(train)} train / {len(test)} held-out obs",
          flush=True)

    fits, preds = {}, {}
    pairs = sorted({(o["model_key"], o["scaffold"]) for o in test})
    test_items = [sub_items[goal_of[g]] for g in sorted({o["goal_id"] for o in test})]
    col = {p: j for j, p in enumerate(pairs)}
    row = {it["goal_id"]: i for i, it in enumerate(test_items)}
    for method in ("nuts", "vi_lowrank"):
        t0 = time.time()
        f = ev.fit(train, sub_items, cards=data.cards, features=True, conf=conf, seed=args.seed, method=method)
        pm = ev.predict(f, pairs, test_items, data.cards, seed=args.seed)
        preds[method] = np.array([pm[row[o["goal_id"]], col[(o["model_key"], o["scaffold"])]] for o in test])
        fits[method] = f
        print(f"{method}: {round(time.time() - t0)} s, used {f['method']}", flush=True)

    y = np.array([float(o["accepted"]) for o in test])
    mk = np.array([o["model_key"] for o in test])
    ll = {m: y * np.log(p) + (1 - y) * np.log(1 - p) for m, p in preds.items()}
    theta = {}
    for m, f in fits.items():
        g = f["g"]
        theta[m] = dict(zip(g.models, np.asarray(g.arrays["theta_last"]).mean(axis=0).tolist()))
    common = sorted(set(theta["nuts"]) & set(theta["vi_lowrank"]))
    result = {
        "experiment": "vi_vs_nuts", "config": conf.__dict__, "items": len(sub_items), "models": len(models),
        "train_obs": len(train), "test_obs": len(test), "seed": args.seed,
        "metrics": {m: ev.scores(p, y) for m, p in preds.items()},
        "loglik_vi_minus_nuts_per_obs": ev.bootstrap_diff(ll["vi_lowrank"], ll["nuts"], mk),
        "prediction_correlation": float(np.corrcoef(preds["nuts"], preds["vi_lowrank"])[0, 1]),
        "mean_abs_prediction_gap": float(np.mean(np.abs(preds["nuts"] - preds["vi_lowrank"]))),
        "theta_mean_correlation": float(np.corrcoef([theta["nuts"][k] for k in common],
                                                    [theta["vi_lowrank"][k] for k in common])[0, 1]),
        "fit_seconds": {m: round(f["seconds"]) for m, f in fits.items()},
        "diagnostics": {m: f["diag"] for m, f in fits.items()},
    }
    path = os.path.join(args.out, "vi_vs_nuts.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("metrics", "loglik_vi_minus_nuts_per_obs", "prediction_correlation",
                                             "theta_mean_correlation", "fit_seconds")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
