"""Between-nightly update of model abilities, across Goals (numpy only).

The joint model already says a model's ability may change after the last fit: theta[m, now] =
theta[m, last] + drift, drift ~ N(0, sigma_drift^2 * weeks). Decision time used to DRAW that drift
from its prior. This module computes its POSTERIOR from every public observation of the model recorded
since the fit, on any Goal -- so twenty failures of a model today move its ability for every Goal now,
not after the next nightly fit. (A Goal's local refit cannot do this: it holds abilities fixed.)

For each model and each global draw s, the drift is one-dimensional, so its conditional posterior is
computed exactly on a grid (no sampler):

    p(d | data, s)  propto  N(d; 0, sigma_drift_s^2 * weeks) * prod_r P(obs_r | theta_s + d, Goal draws_s)

with the instance difficulty integrated by Gauss-Hermite and the check's error rates applied. One d is
drawn per s, so the result stays aligned with the global draws. The next nightly fit absorbs the same
data and the update starts over against the new draws.

Only PUBLIC observations are used: the result changes a model's ability for everyone.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from app.routing.predict import Globals, pack, unpack, week_index
from app.routing.quadrature import standard_normal_rule

GRID = 81                 # drift grid points over +-5 prior sd
MIN_OBS = 1


def drift_posterior(g: Globals, model_key: str, rows: Sequence[Mapping[str, Any]], goal_x: Mapping[str, np.ndarray],
                    now: datetime, rng: np.random.Generator, *, eps_nodes: int = 20) -> Optional[np.ndarray]:
    """(S,) drift draws for a FITTED model given its observations since the fit (each row: goal_id,
    scaffold, accepted, check_kind), or None when the model is unknown to the fit or there is no data.
    goal_x: (S, D) aligned draws per goal_id."""
    if model_key not in g.models or len(rows) < MIN_OBS:
        return None
    arr, s_count = g.arrays, g.draws
    m = g.models.index(model_key)
    s_ix = {s: i for i, s in enumerate(g.scaffolds)}
    gap = max(1, week_index(now, g.epoch) - int(np.asarray(g.meta["model_last_week"])[m]))
    prior_sd = arr["sigma_drift"] * np.sqrt(gap)                                 # (S,)
    unit = np.linspace(-5.0, 5.0, GRID)
    grid = prior_sd[:, None] * unit[None, :]                                     # (S, G)
    log_post = -0.5 * unit[None, :] ** 2 * np.ones((s_count, 1))                 # standard-normal prior on the grid
    ex, ew = standard_normal_rule(eps_nodes)
    kinds = list(g.meta["check_kinds"])
    theta = arr["theta_last"][:, m]
    z = arr["z"][:, m, :]
    for r in rows:
        x = goal_x.get(str(r["goal_id"]))
        if x is None:
            continue
        s = r["scaffold"]
        gd = (arr["gamma"][:, s_ix[s]] + arr["delta"][:, m, s_ix[s]]) if s in s_ix else 0.0
        logit = theta + gd - x[:, 0] + (x[:, 2:] * z).sum(axis=1)                # (S,)
        arg = (logit[:, None, None] + grid[:, :, None]
               - np.exp(x[:, 1])[:, None, None] * ex[None, None, :])             # (S, G, N)
        p = (1.0 / (1.0 + np.exp(-arg))) @ ew                                    # (S, G) P(correct | d)
        k = kinds.index(r["check_kind"]) if r.get("check_kind") in kinds else kinds.index("self_report")
        alpha, beta = arr["alpha"][:, k][:, None], arr["beta"][:, k][:, None]
        p_acc = p * (1.0 - beta) + (1.0 - p) * alpha
        p_obs = p_acc if r["accepted"] else 1.0 - p_acc
        log_post = log_post + np.log(np.clip(p_obs, 1e-300, None))
    w = np.exp(log_post - log_post.max(axis=1, keepdims=True))
    w /= w.sum(axis=1, keepdims=True)
    cdf = np.cumsum(w, axis=1)
    u = rng.random(s_count)[:, None]
    idx = np.minimum((cdf < u).sum(axis=1), GRID - 1)
    jitter = (rng.random(s_count) - 0.5) * (grid[:, 1] - grid[:, 0])             # within the grid cell
    return grid[np.arange(s_count), idx] + jitter


async def run(pool: Any, *, now: Optional[datetime] = None, seed: int = 0) -> dict[str, Any]:
    """Recompute every fitted model's drift from the public observations since the active fit."""
    from app.routing import store
    from app.routing.fit import aligned_goal_draws
    from app.routing.predict import load_globals, utc_now

    params = await store.active_params(pool)
    if params is None:
        return {"skipped": "no fitted parameters yet"}
    g = load_globals(params["version"], params["draws"], params["meta"])
    since = g.meta.get("fitted_at")
    if not since:
        return {"skipped": "the active fit predates fitted_at (run routing-refit once)"}
    now = now or utc_now()
    rows = await store.observations_since(pool, datetime.fromisoformat(since), public_only=True)
    by_model: dict[str, list] = {}
    for r in rows:
        if r["model_key"] in g.models and r.get("step_order") is None:
            by_model.setdefault(r["model_key"], []).append(r)
    rng = np.random.default_rng(seed)
    cache: dict = {}
    goal_x: dict[str, np.ndarray] = {}
    for gid in sorted({str(r["goal_id"]) for rs in by_model.values() for r in rs}):
        goal_x[gid] = await aligned_goal_draws(pool, g, gid, rng=rng, cache=cache)
    updates = []
    for model_key, rs in sorted(by_model.items()):
        d = drift_posterior(g, model_key, rs, goal_x, now, rng)
        if d is not None:
            updates.append({"model_key": model_key, "drift": d, "n": len(rs)})
    await store.save_model_updates(pool, g.version, updates)
    return {"params_version": g.version, "since": since, "observations": len(rows),
            "models_updated": len(updates), "per_model": {u["model_key"]: u["n"] for u in updates}}


def encode(drift: np.ndarray) -> bytes:
    return pack({"drift": np.asarray(drift, dtype=np.float64)})


def decode(blob: bytes) -> np.ndarray:
    return unpack(blob)["drift"]

