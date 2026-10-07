"""Offline validation of the model-side priors (docs/plan_2026-10_priors_library_survey.md §2.5).

Everything runs on public data downloaded to a local folder (no database, no network):

    python scripts/routing_priors_eval.py --data ../.scratch/routing-priors-data --out ../docs/routing_priors_eval \
        --exp lomo|lobo|temporal|routing|sbc|all

Experiments (each writes <out>/<exp>.json):

  lomo      leave-models-out (pure cold start): models are split into folds; a fold's models are absent
            from the fit and predicted from their CARD only. Compared with the old population prior
            (no cards), the same-family average of fitted models, and the in-sample fit (upper bound).
  temporal  the same, holding out every model released after a date.
  lobo      leave-one-benchmark-out: fit on two SWE-bench splits, predict the third split's items (new
            repositories for Multilingual) for the models seen in training.
  routing   held-out ITEMS (half of SWE-bench Verified): RouteLLM metrics for a strong/weak pair --
            APGR and CPT(50%/80%) -- and a cost-quality frontier over all priced models (AIQ), against:
            random, best single, cheapest, kNN on item features, RouteLLM's SW-ranking and MF routers
            re-implemented on the same data, and our model without the structural features.
  sbc       simulation-based calibration of the new terms (card regression beta, model ability, a
            Goal difficulty) on a small design.

The prediction code is the production code (predict.unit_terms / success_given_eps / goal_prior_draws),
so what is measured is what find_ways would answer.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routing import cards as cardlib  # noqa: E402
from app.routing import evidence, public_evidence as pe  # noqa: E402
from app.routing.config import RoutingDefaults  # noqa: E402
from app.routing.predict import Globals, goal_prior_draws, success_given_eps, unit_terms  # noqa: E402
from app.routing.quadrature import standard_normal_rule  # noqa: E402

BENCH = "swe-bench"                    # one item identity across Verified / Lite / full test
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)   # one week: abilities do not drift inside the evaluation
SPLITS = {"verified": "verified.parquet", "lite": "lite.parquet", "multilingual": "multilingual.parquet"}


def cfg(fast: bool) -> RoutingDefaults:
    return RoutingDefaults(draws=128 if fast else 200, nightly_warmup=250 if fast else 400, nightly_chains=2,
                           nightly_samples=150 if fast else 250, latent_dims=3, embedding_dims=0,
                           nuts_max_latents=10 ** 9)


# ---------------------------------------------------------------- data

class Data:
    def __init__(self, root: str, splits: Sequence[str]):
        self.items: dict[str, dict] = {}
        self.split_items: dict[str, set[str]] = {}
        for split in splits:
            got = pe.swebench_items([os.path.join(root, "swe_items", SPLITS[split])], benchmark=BENCH)
            self.split_items[split] = set(got)
            for iid, it in got.items():
                self.items.setdefault(iid, it)
        self.runs: dict[str, list[pe.Run]] = {
            split: pe.swebench_runs(os.path.join(root, "experiments"), split, universe=self.split_items[split])
            for split in splits}
        orc = pe.openrouter_cards(os.path.join(root, "openrouter_models.json"))
        all_runs = [r for rs in self.runs.values() for r in rs]
        self.cards = pe.merge_cards(orc, pe.run_cards(all_runs))
        # one dedupe across splits: the same (item, model, scaffold, submission) is one observation
        seen, self.obs = set(), []
        for split, rs in self.runs.items():
            for o in pe.run_observations(rs, BENCH, self.items):
                o = {**o, "occurred_at": STAMP, "split": split}
                key = o["dedupe_key"]
                if key not in seen:
                    seen.add(key)
                    self.obs.append(o)

    def card(self, model_key: str) -> Optional[cardlib.ModelCard]:
        return cardlib.resolve(model_key, self.cards)


def goal_inputs(items: Mapping[str, dict], features: bool) -> tuple[dict, dict]:
    """goal_meta and parents for the evidence Goals of `items` (evidence.assemble)."""
    rows = [dict(it, features=it["features"] if features else {}) for it in items.values()]
    meta, parents, _ = evidence.assemble(rows)
    if not features:
        meta = {g: {k: v for k, v in m.items() if k != "features"} for g, m in meta.items()}
    return meta, parents


# ---------------------------------------------------------------- fitting

def fit(obs: Sequence[dict], items: Mapping[str, dict], *, cards: Optional[Mapping[str, Any]], features: bool,
        conf: RoutingDefaults, seed: int = 0, method: str = "nuts") -> dict[str, Any]:
    from app.routing import fit as fitlib

    goal_meta, parents = goal_inputs(items, features)
    data, meta = fitlib.build_joint_data(obs, goal_meta, parents, {}, conf, cards=cards)
    t0 = time.time()
    samples, used, diag = fitlib.run_joint(data, conf, seed=seed, method=method)
    arrays = fitlib.global_arrays(samples, meta)
    g = Globals(version=1, arrays=arrays, meta={**fitlib.public_meta(meta), "tokens": {}})
    goal_x = {gid: samples["goal_x"][:, i, :] for i, gid in enumerate(meta["goals"])}
    return {"g": g, "goal_x": goal_x, "goal_meta": goal_meta, "parents": parents, "seconds": time.time() - t0,
            "method": used, "diag": diag, "n_obs": len(obs)}


def item_draws(f: Mapping[str, Any], it: Mapping[str, Any], rng: np.random.Generator) -> np.ndarray:
    """(S, D) Goal draws of an item: fitted when the fit saw it, else its prior given its repo node (or the
    benchmark node for an unseen repository) -- what decision time does for a new Goal."""
    gid = it["goal_id"]
    if gid in f["goal_x"]:
        return f["goal_x"][gid]
    g = f["g"]
    feats = it.get("features") if g.meta.get("goal_features") else None
    repo_node = evidence.repo_node(BENCH, it["repo"]) if it.get("repo") else None
    bench_node = evidence.benchmark_node(BENCH)
    if repo_node in f["goal_x"]:
        parents = [(f["goal_x"][repo_node], g.phi1(None))]
    elif bench_node in f["goal_x"]:
        # unseen repository: the repo node's prior given the benchmark node, then the item's
        repo_x = goal_prior_draws(g, g.phi1(None), [(f["goal_x"][bench_node], g.phi1(None))], rng)
        parents = [(repo_x, g.phi1(None))]
    else:
        parents = []
    return goal_prior_draws(g, g.phi1(None, feats), parents, rng)


def predict(f: Mapping[str, Any], pairs: Sequence[tuple[str, str]], items: Sequence[dict],
            cards: Optional[Mapping[str, Any]], override: Optional[Mapping[str, tuple[np.ndarray, np.ndarray]]] = None,
            seed: int = 0) -> np.ndarray:
    """P(resolved) (len(items), len(pairs)) averaged over draws and the instance difficulty -- the
    single-attempt success probability find_ways reports."""
    g = f["g"]
    rng = np.random.default_rng(seed)
    base, z = unit_terms(g, list(pairs), {}, STAMP, rng, cards=cards)
    if override:                                        # e.g. the family-average baseline
        from app.routing.model_constants import TAU_DELTA, TAU_GAMMA

        s_ix = {s: i for i, s in enumerate(g.scaffolds)}
        for u, (m, s) in enumerate(pairs):
            if m in override:
                theta, zz = override[m]
                gamma = g.arrays["gamma"][:, s_ix[s]] if s in s_ix else TAU_GAMMA * rng.standard_normal(g.draws)
                base[:, u] = theta + gamma + TAU_DELTA * rng.standard_normal(g.draws)
                z[:, u, :] = zz
    # These are benchmark tasks, so what is being predicted includes memorisation: add the learned kappa where
    # the task predates the model's cutoff, exactly as the fit did. (Live tasks never get it.)
    kappa = g.arrays.get("kappa")
    cutoffs = []
    for m, _s in pairs:
        c = cardlib.resolve(m, cards) if cards else None
        cutoffs.append(c.contamination_cutoff() if c is not None else None)
    out = np.zeros((len(items), len(pairs)))
    for i, it in enumerate(items):
        gx = item_draws(f, it, rng)
        b = base
        if kappa is not None and it.get("created_at") is not None:
            flags = np.array([1.0 if (cut is not None and it["created_at"] < cut) else 0.0 for cut in cutoffs])
            b = base + kappa[:, None] * flags[None, :]
        p, w = success_given_eps(g, gx, None, b, z, eps_nodes=20)
        out[i] = (p @ w).mean(axis=0)
    return np.clip(out, 1e-6, 1 - 1e-6)


def scores(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    ll = float(np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    brier = float(np.mean((p - y) ** 2))
    return {"loglik": ll, "brier": brier, "auc": auc(p, y), "n": int(len(y))}


def auc(p: np.ndarray, y: np.ndarray) -> float:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def bootstrap_diff(a: np.ndarray, b: np.ndarray, groups: np.ndarray, n: int = 2000, seed: int = 0) -> dict[str, float]:
    """Mean difference of per-observation scores a - b with a 95% CI, resampling GROUPS (models) --
    observations of one model are not independent."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    by = {gname: np.where(groups == gname)[0] for gname in uniq}
    diffs = []
    for _ in range(n):
        pick = np.concatenate([by[gname] for gname in rng.choice(uniq, size=len(uniq), replace=True)])
        diffs.append(float(np.mean(a[pick] - b[pick])))
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return {"mean": float(np.mean(a - b)), "ci95": [float(lo), float(hi)],
            "p_le_0": float(np.mean(np.asarray(diffs) <= 0))}


# ---------------------------------------------------------------- cold start (lomo, temporal)

def family_override(f: Mapping[str, Any], held: Sequence[str], data: Data) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Baseline: a held-out model = the mean of the FITTED models of its family (else of all fitted)."""
    g = f["g"]
    arr = g.arrays
    fam_of = {m: (data.card(m).family if data.card(m) else None) for m in g.models}
    out = {}
    for m in held:
        c = data.card(m)
        same = [i for i, fm in enumerate(g.models) if c is not None and c.family and fam_of[fm] == c.family]
        idx = same or list(range(len(g.models)))
        out[m] = (arr["theta_last"][:, idx].mean(axis=1), arr["z"][:, idx, :].mean(axis=1))
    return out


def cold_start(data: Data, held_sets: Sequence[Sequence[str]], conf: RoutingDefaults, label: str) -> dict:
    per_obs = defaultdict(list)
    folds = []
    for k, held in enumerate(held_sets):
        held = set(held)
        train = [o for o in data.obs if o["model_key"] not in held]
        test = [o for o in data.obs if o["model_key"] in held]
        if not test or not train:
            continue
        items = data.items
        fits = {"cards": fit(train, items, cards=data.cards, features=True, conf=conf, seed=k),
                "no_cards": fit(train, items, cards=None, features=True, conf=conf, seed=k)}
        pairs = sorted({(o["model_key"], o["scaffold"]) for o in test})
        by_item = defaultdict(list)
        for o in test:
            by_item[o["goal_id"]].append(o)
        test_items = [it for it in items.values() if it["goal_id"] in by_item]
        preds = {
            "cards": predict(fits["cards"], pairs, test_items, data.cards),
            "no_cards (old prior)": predict(fits["no_cards"], pairs, test_items, None),
            "family_average": predict(fits["no_cards"], pairs, test_items, None,
                                      override=family_override(fits["no_cards"], sorted(held), data)),
        }
        col = {p: j for j, p in enumerate(pairs)}
        row = {it["goal_id"]: i for i, it in enumerate(test_items)}
        for name, pm in preds.items():
            for o in test:
                per_obs[name].append((pm[row[o["goal_id"]], col[(o["model_key"], o["scaffold"])]], float(o["accepted"]),
                                      o["model_key"]))
        folds.append({"held_out": sorted(held), "train_obs": len(train), "test_obs": len(test),
                      "fit_seconds": {n: round(f["seconds"]) for n, f in fits.items()},
                      "diagnostics": {n: f["diag"] for n, f in fits.items()}})
        print(f"[{label}] fold {k}: {len(held)} models held out, {len(test)} test obs", flush=True)
    result = {"folds": folds, "metrics": {}, "rate_error": {}}
    arrays = {}
    for name, rows in per_obs.items():
        p = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        m = np.array([r[2] for r in rows])
        arrays[name] = (p, y, m)
        result["metrics"][name] = scores(p, y)
        errs = [abs(p[m == mk].mean() - y[m == mk].mean()) for mk in np.unique(m)]
        result["rate_error"][name] = {"mean_abs_error_of_model_resolve_rate": float(np.mean(errs)),
                                      "models": int(len(errs))}
    if "cards" in arrays:
        p_c, y, m = arrays["cards"]
        ll_c = y * np.log(p_c) + (1 - y) * np.log(1 - p_c)
        result["cards_vs"] = {}
        for other in ("no_cards (old prior)", "family_average"):
            p_o = arrays[other][0]
            ll_o = y * np.log(p_o) + (1 - y) * np.log(1 - p_o)
            result["cards_vs"][other] = {"loglik_gain_per_obs": bootstrap_diff(ll_c, ll_o, m)}
    return result


# ---------------------------------------------------------------- leave one benchmark out

def lobo(data: Data, conf: RoutingDefaults) -> dict:
    out = {}
    for held in data.split_items:
        held_items = data.split_items[held]
        other_items = set().union(*(s for k, s in data.split_items.items() if k != held)) - held_items
        train = [o for o in data.obs if o["split"] != held and _iid(o) in other_items]
        train_models = {o["model_key"] for o in train}
        test = [o for o in data.obs if o["split"] == held and _iid(o) in held_items and o["model_key"] in train_models]
        if not train or not test:
            continue
        items = {iid: it for iid, it in data.items.items() if iid in other_items}
        res = {}
        for name, feats in (("with_features", True), ("no_features", False)):
            f = fit(train, items, cards=data.cards, features=feats, conf=conf)
            pairs = sorted({(o["model_key"], o["scaffold"]) for o in test})
            test_items = [data.items[iid] for iid in sorted(held_items)]
            pm = predict(f, pairs, test_items, data.cards)
            col = {p: j for j, p in enumerate(pairs)}
            row = {it["goal_id"]: i for i, it in enumerate(test_items)}
            p = np.array([pm[row[o["goal_id"]], col[(o["model_key"], o["scaffold"])]] for o in test])
            y = np.array([float(o["accepted"]) for o in test])
            mk = np.array([o["model_key"] for o in test])
            rate_err = [abs(p[mk == m].mean() - y[mk == m].mean()) for m in np.unique(mk)]
            # per-item ranking: does the model know WHICH held-out items are hard?
            res[name] = {**scores(p, y), "mean_abs_error_of_model_resolve_rate": float(np.mean(rate_err)),
                         "fit_seconds": round(f["seconds"])}
        out[held] = {"train_obs": len(train), "test_obs": len(test), **res}
        print(f"[lobo] {held}: {res}", flush=True)
    return out


def _iid(o: Mapping[str, Any]) -> str:
    return o["instance_key"].split(":", 1)[1]


# ---------------------------------------------------------------- routing on held-out items

def routing(data: Data, conf: RoutingDefaults, seed: int = 0) -> dict:
    """Half of Verified's items are held out. Units: mini-swe-agent runs with published per-instance
    cost (the same scaffold, so the comparison is between models)."""
    rng = np.random.default_rng(seed)
    verified = sorted(data.split_items["verified"])
    rng.shuffle(verified)
    test_ids = set(verified[: len(verified) // 2])
    obs = [o for o in data.obs if o["split"] == "verified"]
    costed = defaultdict(dict)
    for r in data.runs["verified"]:
        if r.scaffold == "mini-swe-agent" and len(r.cost) >= 0.9 * len(r.results):
            for iid, c in r.cost.items():
                costed[(r.model_key, r.scaffold)][iid] = c
    units = sorted(costed)
    train = [o for o in obs if _iid(o) not in test_ids]
    test = [o for o in obs if _iid(o) in test_ids and (o["model_key"], o["scaffold"]) in costed]
    items_train = {iid: it for iid, it in data.items.items() if iid not in test_ids}
    test_items = [data.items[iid] for iid in sorted(test_ids)]
    truth = np.full((len(test_items), len(units)), np.nan)
    cost = np.full((len(test_items), len(units)), np.nan)
    row = {it["goal_id"]: i for i, it in enumerate(test_items)}
    col = {u: j for j, u in enumerate(units)}
    for o in test:
        truth[row[o["goal_id"]], col[(o["model_key"], o["scaffold"])]] = float(o["accepted"])
    for u, by in costed.items():
        for i, it in enumerate(test_items):
            iid = it["item_key"].split(":", 1)[1]
            if iid in by:
                cost[i, col[u]] = by[iid]
    keep = ~np.isnan(truth).any(axis=1) & ~np.isnan(cost).any(axis=1)
    truth, cost = truth[keep], cost[keep]
    test_items = [it for it, k in zip(test_items, keep) if k]

    scorers: dict[str, np.ndarray] = {}
    f_full = fit(train, items_train, cards=data.cards, features=True, conf=conf, seed=seed)
    scorers["ours"] = predict(f_full, units, test_items, data.cards)
    f_nofeat = fit(train, items_train, cards=data.cards, features=False, conf=conf, seed=seed)
    scorers["ours_no_features"] = predict(f_nofeat, units, test_items, data.cards)
    train_matrix, train_items = _matrix(train, units, items_train)
    scorers["knn_features"] = knn_scores(train_matrix, train_items, test_items)
    scorers["routellm_sw_ranking"] = sw_ranking_scores(train_matrix, train_items, test_items)
    scorers["routellm_mf"] = mf_scores(train_matrix, train_items, test_items, seed=seed)
    scorers["train_rate (best-single scorer)"] = np.tile(np.nanmean(train_matrix, axis=0), (len(test_items), 1))

    out: dict[str, Any] = {"units": [f"{m}|{s}" for m, s in units], "test_items": len(test_items),
                           "mean_cost_usd": dict(zip([f"{m}|{s}" for m, s in units], cost.mean(axis=0).round(4).tolist())),
                           "accuracy": dict(zip([f"{m}|{s}" for m, s in units], truth.mean(axis=0).round(4).tolist()))}
    out["prediction_quality"] = {name: scores(np.clip(s, 1e-6, 1 - 1e-6).ravel(), truth.ravel())
                                 for name, s in scorers.items()}
    out["pairs"] = {}
    acc = truth.mean(axis=0)
    mean_cost = cost.mean(axis=0)
    # the pair is chosen on TRAINING items only (no peeking at the held-out half)
    train_acc = np.nanmean(train_matrix, axis=0)
    strong = int(np.argmax(train_acc))
    weak = int(np.argmin(np.where(train_acc > 0.25, mean_cost, np.inf)))
    for name, s in scorers.items():
        out["pairs"][name] = pair_metrics(s[:, strong] - s[:, weak], truth[:, strong], truth[:, weak])
    out["pairs"]["random"] = pair_metrics(np.random.default_rng(seed).random(len(test_items)), truth[:, strong],
                                          truth[:, weak])
    out["pair"] = {"strong": out["units"][strong], "weak": out["units"][weak]}
    out["frontier"] = {name: frontier(s, truth, cost) for name, s in scorers.items()}
    out["frontier"]["oracle"] = frontier(truth, truth, cost)
    best = int(np.argmax(acc))
    cheapest = int(np.argmin(mean_cost))
    out["baselines"] = {"best_single": {"unit": out["units"][best], "accuracy": float(acc[best]),
                                        "cost": float(mean_cost[best])},
                        "cheapest": {"unit": out["units"][cheapest], "accuracy": float(acc[cheapest]),
                                     "cost": float(mean_cost[cheapest])}}
    return out


def _matrix(obs: Sequence[dict], units: Sequence[tuple[str, str]], items: Mapping[str, dict]):
    ids = sorted(items)
    row = {items[i]["goal_id"]: r for r, i in enumerate(ids)}
    col = {u: j for j, u in enumerate(units)}
    mat = np.full((len(ids), len(units)), np.nan)
    for o in obs:
        u = (o["model_key"], o["scaffold"])
        if u in col and o["goal_id"] in row:
            mat[row[o["goal_id"]], col[u]] = float(o["accepted"])
    return mat, [items[i] for i in ids]


def _feature_rows(its: Sequence[dict], repos: Sequence[str]) -> np.ndarray:
    from app.routing import goal_features as gf

    mean, sd = gf.standardisation([it["features"] for it in its])
    x = np.stack([gf.standardised(it["features"], mean, sd) for it in its])
    onehot = np.array([[1.0 if it.get("repo") == r else 0.0 for r in repos] for it in its])
    return np.concatenate([x, onehot], axis=1)


def knn_scores(train_matrix: np.ndarray, train_items: Sequence[dict], test_items: Sequence[dict], k: int = 25):
    repos = sorted({it.get("repo") for it in train_items})
    x = _feature_rows(list(train_items) + list(test_items), repos)
    xtr, xte = x[: len(train_items)], x[len(train_items):]
    out = np.zeros((len(test_items), train_matrix.shape[1]))
    for i, q in enumerate(xte):
        near = np.argsort(((xtr - q) ** 2).sum(axis=1))[:k]
        out[i] = np.nan_to_num(np.nanmean(train_matrix[near], axis=0), nan=0.5)
    return np.clip((out * k + 0.5) / (k + 1), 1e-3, 1 - 1e-3)


def sw_ranking_scores(train_matrix: np.ndarray, train_items: Sequence[dict], test_items: Sequence[dict],
                      gamma: float = 10.0) -> np.ndarray:
    """RouteLLM's similarity-weighted (SW) ranking router: every training item votes, weighted by
    gamma ** (1 + sim), sim = its cosine similarity to the query rescaled to [0, 1] per query (gamma = 10
    as in the paper). RouteLLM fits Bradley-Terry on weighted pairwise battles; with per-item correctness,
    the weighted per-model success rate is the pointwise analogue of the same estimator."""
    repos = sorted({it.get("repo") for it in train_items})
    x = _feature_rows(list(train_items) + list(test_items), repos)
    x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-9)
    xtr, xte = x[: len(train_items)], x[len(train_items):]
    seen = ~np.isnan(train_matrix)
    y = np.nan_to_num(train_matrix)
    out = np.zeros((len(test_items), train_matrix.shape[1]))
    for i, q in enumerate(xte):
        sim = xtr @ q
        span = sim.max() - sim.min()
        sim = (sim - sim.min()) / span if span > 0 else np.zeros_like(sim)
        w = gamma ** (1 + sim)
        num = (w[:, None] * y * seen).sum(axis=0)
        den = (w[:, None] * seen).sum(axis=0)
        out[i] = (num + 0.5 * w.mean()) / (den + w.mean())          # one pseudo-vote at 0.5: no 0 / 0
    return np.clip(out, 1e-3, 1 - 1e-3)


def mf_scores(train_matrix: np.ndarray, train_items: Sequence[dict], test_items: Sequence[dict], *, dim: int = 16,
              epochs: int = 400, seed: int = 0) -> np.ndarray:
    """RouteLLM's matrix-factorisation router, re-implemented: score(m, q) = w2 . (v_m * (W1 q + b)) with q
    the item's features (+ repository one-hot; RouteLLM uses a text embedding, which we do not have for
    public items). Trained by logistic loss on per-instance outcomes (RouteLLM trains on pairwise
    preferences; with per-instance correctness the pointwise loss is the direct analogue)."""
    import jax
    import jax.numpy as jnp

    repos = sorted({it.get("repo") for it in train_items})
    x = _feature_rows(list(train_items) + list(test_items), repos)
    xtr, xte = jnp.asarray(x[: len(train_items)]), jnp.asarray(x[len(train_items):])
    y = jnp.asarray(np.nan_to_num(train_matrix))
    mask = jnp.asarray(~np.isnan(train_matrix))
    key = jax.random.PRNGKey(seed)
    k1, k2, k3 = jax.random.split(key, 3)
    params = {"v": 0.1 * jax.random.normal(k1, (train_matrix.shape[1], dim)),
              "W1": 0.1 * jax.random.normal(k2, (x.shape[1], dim)), "b": jnp.zeros(dim),
              "w2": 0.1 * jax.random.normal(k3, (dim,)), "bias_m": jnp.zeros(train_matrix.shape[1])}

    def logits(p, q):
        h = q @ p["W1"] + p["b"]                                 # (N, dim)
        return jnp.einsum("nd,md,d->nm", h, p["v"], p["w2"]) + p["bias_m"]

    def loss(p):
        lg = logits(p, xtr)
        ll = y * jax.nn.log_sigmoid(lg) + (1 - y) * jax.nn.log_sigmoid(-lg)
        reg = 1e-3 * sum(jnp.sum(v ** 2) for v in jax.tree_util.tree_leaves(p))
        return -(ll * mask).sum() / mask.sum() + reg

    grad = jax.jit(jax.grad(loss))
    m1 = jax.tree_util.tree_map(jnp.zeros_like, params)
    m2 = jax.tree_util.tree_map(jnp.zeros_like, params)
    lr, b1, b2 = 1e-2, 0.9, 0.999
    for t in range(1, epochs + 1):                       # Adam, full batch
        gr = grad(params)
        m1 = jax.tree_util.tree_map(lambda a, g_: b1 * a + (1 - b1) * g_, m1, gr)
        m2 = jax.tree_util.tree_map(lambda a, g_: b2 * a + (1 - b2) * g_ ** 2, m2, gr)
        params = jax.tree_util.tree_map(
            lambda p_, a, v: p_ - lr * (a / (1 - b1 ** t)) / (jnp.sqrt(v / (1 - b2 ** t)) + 1e-8), params, m1, m2)
    return np.asarray(jax.nn.sigmoid(logits(params, xte)))


def pair_metrics(score: np.ndarray, y_strong: np.ndarray, y_weak: np.ndarray) -> dict[str, float]:
    """RouteLLM: route to the strong model when the score is above a threshold. PGR(x) = (quality at
    strong-call fraction x - weak quality) / (strong - weak); APGR = its area; CPT(t) = the strong-call
    fraction at which PGR first reaches t."""
    n = len(score)
    order = np.argsort(-score, kind="mergesort")
    q_weak, q_strong = y_weak.mean(), y_strong.mean()
    gap = q_strong - q_weak
    fracs, pgr = [0.0], [0.0]
    for c in range(1, n + 1):
        to_strong = np.zeros(n, dtype=bool)
        to_strong[order[:c]] = True
        q = np.where(to_strong, y_strong, y_weak).mean()
        fracs.append(c / n)
        pgr.append((q - q_weak) / gap if gap > 0 else float("nan"))
    fracs, pgr = np.asarray(fracs), np.asarray(pgr)

    def cpt(t: float) -> float:
        hit = np.where(pgr >= t)[0]
        return float(fracs[hit[0]]) if len(hit) else 1.0

    return {"APGR": float(np.trapezoid(pgr, fracs)), "CPT50": cpt(0.5), "CPT80": cpt(0.8),
            "strong_acc": float(q_strong), "weak_acc": float(q_weak)}


def frontier(score: np.ndarray, truth: np.ndarray, cost: np.ndarray, n_lambda: int = 60) -> dict[str, Any]:
    """Per item pick argmax_u score - lambda * expected cost(u) (unit mean cost); sweep lambda. AIQ =
    area under the realised (cost, quality) frontier, normalised by the cost range (RouterBench)."""
    unit_cost = cost.mean(axis=0)
    pts = []
    for lam in np.concatenate([[0.0], np.geomspace(1e-3, 1e3, n_lambda)]):
        pick = np.argmax(score - lam * unit_cost[None, :], axis=1)
        idx = np.arange(len(pick))
        pts.append((float(cost[idx, pick].mean()), float(truth[idx, pick].mean())))
    pts = sorted(set(pts))
    hull, best = [], -1.0
    for c, q in pts:                                    # non-dominated points, cheapest first
        if q > best:
            hull.append((c, q))
            best = q
    cs = np.array([c for c, _ in hull])
    qs = np.array([q for _, q in hull])
    lo, hi = float(unit_cost.min()), float(unit_cost.max())
    grid = np.linspace(lo, hi, 200)
    q_at = np.array([qs[cs <= c].max() if (cs <= c).any() else 0.0 for c in grid])
    return {"AIQ": float(np.trapezoid(q_at, grid) / (hi - lo)) if hi > lo else float(qs.max()),
            "max_quality": float(qs.max()), "points": [[round(c, 4), round(q, 4)] for c, q in hull]}


# ---------------------------------------------------------------- SBC

def sbc(conf: RoutingDefaults, sims: int, seed: int = 0) -> dict:
    """Simulate from the prior of the card model on a small design (12 models with cards, 30 items,
    every model on every item), refit, rank the truth. Uniform ranks <=> correct computation."""
    import jax
    from numpyro.infer import Predictive

    from app.routing import fit as fitlib
    from app.routing.model import joint_model

    rng = np.random.default_rng(seed)
    cards_ = {}
    for m in range(12):
        cards_[f"m{m}"] = cardlib.ModelCard(model_key=f"m{m}", family=f"f{m % 3}",
                                            release_date=date(2024, 1 + m % 12, 1),
                                            price_in=float(np.exp(rng.normal(0, 1.5))),
                                            price_out=float(np.exp(rng.normal(1, 1.5))),
                                            open_weights=bool(m % 2), params_b=None if m % 3 else float(2 ** (m % 7 + 3)))
    items = {f"i{i}": {"goal_id": evidence.node_id(f"sbc:{i}"), "benchmark": "sbc", "repo": f"r{i % 3}",
                       "features": {"files": int(rng.integers(1, 5)), "hunks": int(rng.integers(1, 9)),
                                    "lines_added": int(rng.integers(1, 80)), "lines_removed": int(rng.integers(0, 40)),
                                    "languages": 1, "tests": int(rng.integers(1, 6)), "packages": 1}}
             for i in range(30)}
    obs = [{"goal_id": it["goal_id"], "model_key": m, "scaffold": "s", "instance_key": iid,
            "check_kind": "benchmark", "accepted": False, "occurred_at": STAMP, "visibility": "public"}
           for iid, it in items.items() for m in cards_]
    goal_meta, parents = goal_inputs(items, True)
    data, _ = fitlib.build_joint_data(obs, goal_meta, parents, {}, conf, cards=cards_)
    k = int(data["k"])
    prior = Predictive(joint_model, num_samples=sims, return_sites=[
        "theta_hist", "z", "gamma", "delta", "goal_x", "proc_c", "step_d", "step_e", "card_beta"])(
        jax.random.PRNGKey(seed), {**data, "n_attempts": 0}, k, conf.gh_eps_nodes)
    prior = {key: np.asarray(v) for key, v in prior.items()}
    ranks = defaultdict(list)
    draws = conf.draws
    for s in range(sims):
        sim = dict(data)
        sim["att_accepted"] = fitlib.simulate(rng, data, prior, s, k)
        post, _, _ = fitlib.run_joint(sim, conf, seed=seed + 1 + s, num_draws=draws, method="nuts")
        tracked = {"beta_release_years": (prior["card_beta"][s, 0], post["card_beta"][:, 0]),
                   "beta_log_price_out": (prior["card_beta"][s, 2], post["card_beta"][:, 2]),
                   "model0_ability": (prior["theta_hist"][s, 0, -1], post["theta_hist"][:, 0, -1]),
                   "goal_difficulty": (prior["goal_x"][s, -1, 0], post["goal_x"][:, -1, 0])}
        for name, (truth, d) in tracked.items():
            ranks[name].append(int((np.asarray(d) < truth).sum()))
        print(f"[sbc] {s + 1}/{sims}", flush=True)
    report = {}
    for name, r in ranks.items():
        hist, _ = np.histogram(r, bins=10, range=(0, draws + 1))
        expected = len(r) / 10
        chi2 = float(((hist - expected) ** 2 / expected).sum())
        report[name] = {"histogram": hist.tolist(), "chi2_9df": chi2, "uniform_at_1pct": chi2 < 21.67}
    return report


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exp", default="all", choices=["lomo", "lobo", "temporal", "routing", "sbc", "all"])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--sims", type=int, default=40)
    ap.add_argument("--temporal-after", default="2025-10-01")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    conf = cfg(args.fast)
    exps = ["lomo", "temporal", "lobo", "routing", "sbc"] if args.exp == "all" else [args.exp]
    data = None
    for exp in exps:
        t0 = time.time()
        if exp != "sbc" and data is None:
            splits = ("verified", "lite", "multilingual") if exp in ("lobo",) or args.exp == "all" else ("verified",)
            data = Data(args.data, splits)
            print(f"data: {len(data.items)} items, {len(data.obs)} observations, "
                  f"{len({o['model_key'] for o in data.obs})} models", flush=True)
        if exp == "lomo":
            verified_obs = [o for o in data.obs if o["split"] == "verified"]
            models = sorted({o["model_key"] for o in verified_obs})
            rng = np.random.default_rng(0)
            rng.shuffle(models)
            held = [models[i::args.folds] for i in range(args.folds)]
            sub = _restrict(data, "verified")
            result = cold_start(sub, held, conf, "lomo")
        elif exp == "temporal":
            sub = _restrict(data, "verified")
            cut = date.fromisoformat(args.temporal_after)
            late = sorted({o["model_key"] for o in sub.obs
                           if sub.card(o["model_key"]) and sub.card(o["model_key"]).release_date
                           and sub.card(o["model_key"]).release_date >= cut})
            result = {"released_on_or_after": cut.isoformat(), **cold_start(sub, [late], conf, "temporal")}
        elif exp == "lobo":
            result = lobo(data, conf)
        elif exp == "routing":
            result = routing(_restrict(data, "verified"), conf)
        else:
            result = sbc(conf, args.sims)
        result = {"experiment": exp, "seconds": round(time.time() - t0), "config": conf.__dict__, **result}
        with open(os.path.join(args.out, f"{exp}.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"wrote {exp}.json ({round(time.time() - t0)} s)", flush=True)
    return 0


def _restrict(data: Data, split: str) -> Data:
    sub = Data.__new__(Data)
    sub.items = {iid: data.items[iid] for iid in data.split_items[split]}
    sub.split_items = {split: data.split_items[split]}
    sub.runs = {split: data.runs[split]}
    sub.cards = data.cards
    sub.obs = [o for o in data.obs if o["split"] == split]
    return sub


if __name__ == "__main__":
    raise SystemExit(main())
