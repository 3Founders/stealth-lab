"""Decision-time maths on stored posterior draws (numpy only; no JAX in the API).

Every stored draw set is ALIGNED: row s of a Goal's draws, of a Procedure's draws and
of the global draws belong to the same joint posterior sample s (fit.py guarantees
it). Units, Goals and Procedures that no fit has seen get their draws from the prior
of the same model -- same formulas as model.py, evaluated per aligned row -- so a
brand-new Goal or model is handled by the model itself, not by a special case.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from app.routing.model_constants import SIGMA_NEW_VERSION, SUCCESSOR_Z_SD, TAU_DELTA, TAU_GAMMA
from app.routing.quadrature import standard_normal_rule


def pack(arrays: Mapping[str, np.ndarray]) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **{k: np.asarray(v) for k, v in arrays.items()})
    return buf.getvalue()


def unpack(blob: bytes) -> dict[str, np.ndarray]:
    with np.load(io.BytesIO(bytes(blob)), allow_pickle=False) as data:
        return {k: data[k] for k in data.files}


def week_index(ts: datetime | date, epoch: date) -> int:
    day = ts.date() if isinstance(ts, datetime) else ts
    return max(0, (day - epoch).days // 7)


@dataclass
class Globals:
    version: int
    arrays: dict[str, np.ndarray]
    meta: dict[str, Any]

    @property
    def draws(self) -> int:
        return int(self.arrays["tau_c"].shape[0])

    @property
    def k(self) -> int:
        return int(self.meta["k"])

    @property
    def models(self) -> list[str]:
        return list(self.meta["models"])

    @property
    def scaffolds(self) -> list[str]:
        return list(self.meta["scaffolds"])

    @property
    def epoch(self) -> date:
        return date.fromisoformat(self.meta["epoch"])

    def phi1(self, embedding: Optional[Sequence[float]]) -> np.ndarray:
        """[1, PCA(embedding)]. A Goal without an embedding has no phi information:
        its projection is the population mean (0 in the centred PCA space)."""
        p = self.arrays["pca_components"].shape[0]
        if embedding is None or p == 0:
            return np.concatenate([[1.0], np.zeros(p)])
        e = np.asarray(embedding, dtype=np.float64)
        if e.shape[0] != self.arrays["pca_mean"].shape[0]:
            return np.concatenate([[1.0], np.zeros(p)])
        return np.concatenate([[1.0], self.arrays["pca_components"] @ (e - self.arrays["pca_mean"])])


def load_globals(version: int, blob: bytes, meta: Mapping[str, Any]) -> Globals:
    return Globals(version=version, arrays=unpack(blob), meta=dict(meta))


# ---------------------------------------------------------------- goals / procedures

def goal_prior_draws(g: Globals, phi1: np.ndarray, parents: Sequence[tuple[np.ndarray, np.ndarray]],
                     rng: np.random.Generator) -> np.ndarray:
    """(S, D) prior draws of a Goal block given its parents' ALIGNED draws.

    x = W [1, phi] + mean_p (x_p - W [1, phi_p]) + tau * xi   (empty parent set -> no sum)
    """
    w, tau = g.arrays["w"], g.arrays["tau"]                    # (S, D, P+1), (S, D)
    base = np.einsum("sdp,p->sd", w, phi1)
    if parents:
        resid = np.mean([xp - np.einsum("sdp,p->sd", w, php) for xp, php in parents], axis=0)
    else:
        resid = 0.0
    return base + resid + tau * rng.standard_normal(base.shape)


def procedure_prior_draws(g: Globals, rng: np.random.Generator) -> np.ndarray:
    return g.arrays["tau_c"][:, None] * rng.standard_normal((g.draws, g.k))


# ---------------------------------------------------------------- units

def unit_terms(g: Globals, units: Sequence[tuple[str, str]], predecessors: Mapping[str, Optional[str]],
               now: datetime, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """(base (S, U), z (S, U, K)) with base = theta[m, now] + gamma[s] + delta[m, s].

    Known model: its ability at its last fitted week plus the random-walk drift since.
    Unknown model: from its nearest known predecessor (N(theta_pred, 0.5^2), skills
    N(z_pred, 0.3^2)), or from the population prior when it has none."""
    arr, s_count = g.arrays, g.draws
    model_ix = {m: i for i, m in enumerate(g.models)}
    scaffold_ix = {s: i for i, s in enumerate(g.scaffolds)}
    now_week = week_index(now, g.epoch)
    last_week = np.asarray(g.meta["model_last_week"])
    theta_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def model_terms(model_key: str, depth: int = 0) -> tuple[np.ndarray, np.ndarray]:
        if model_key in theta_cache:
            return theta_cache[model_key]
        if model_key in model_ix:
            m = model_ix[model_key]
            gap = max(0, now_week - int(last_week[m]))
            theta = arr["theta_last"][:, m] + arr["sigma_drift"] * np.sqrt(gap) * rng.standard_normal(s_count)
            z = arr["z"][:, m, :]
        else:
            pred = predecessors.get(model_key)
            if pred and depth < 32:
                theta_p, z_p = model_terms(pred, depth + 1)
                theta = theta_p + SIGMA_NEW_VERSION * rng.standard_normal(s_count)
                z = z_p + SUCCESSOR_Z_SD * rng.standard_normal((s_count, g.k))
            else:
                theta = arr["sigma_theta"] * rng.standard_normal(s_count)
                z = rng.standard_normal((s_count, g.k))
        theta_cache[model_key] = (theta, z)
        return theta, z

    base = np.empty((s_count, len(units)))
    zs = np.empty((s_count, len(units), g.k))
    for u, (model_key, scaffold) in enumerate(units):
        theta, z = model_terms(model_key)
        gamma = arr["gamma"][:, scaffold_ix[scaffold]] if scaffold in scaffold_ix \
            else TAU_GAMMA * rng.standard_normal(s_count)
        if model_key in model_ix and scaffold in scaffold_ix:
            delta = arr["delta"][:, model_ix[model_key], scaffold_ix[scaffold]]
        else:
            delta = TAU_DELTA * rng.standard_normal(s_count)
        base[:, u] = theta + gamma + delta
        zs[:, u, :] = z
    return base, zs


def success_given_eps(g: Globals, goal_x: np.ndarray, proc_c: Optional[np.ndarray], base: np.ndarray,
                      z: np.ndarray, *, eps_nodes: int, item_d: Optional[np.ndarray] = None,
                      item_e: Optional[np.ndarray] = None) -> tuple[np.ndarray, np.ndarray]:
    """P(correct | eps node) for every draw and column: (S, U, N); plus the eps node
    weights (N,). eps is left as nodes so the ladder can condition all rungs -- and all
    steps of a run -- on the SAME run difficulty.

    A column is a unit attempting an item: the whole task (item_d = item_e = 0) or one
    step of the Procedure (its difficulty item_d (S, U) and skill loading item_e (S, U, K))."""
    ex, ew = standard_normal_rule(eps_nodes)
    a = goal_x[:, 2:]                                          # (S, K)
    skill = np.einsum("sk,suk->su", a, z)
    if proc_c is not None:
        skill = skill + np.einsum("sk,suk->su", proc_c, z)
    if item_e is not None:
        skill = skill + np.einsum("suk,suk->su", item_e, z)
    logit_su = base - goal_x[:, [0]] + skill                   # (S, U)
    if item_d is not None:
        logit_su = logit_su - item_d
    sig_eps = np.exp(goal_x[:, 1])                             # (S,)
    arg = logit_su[:, :, None] - sig_eps[:, None, None] * ex[None, None, :]
    return 1.0 / (1.0 + np.exp(-arg)), ew


def step_draws(g: Globals, stored: Optional[Mapping[str, np.ndarray]], order: int, role: Optional[str],
               rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, str]:
    """(d (S,), e (S, K), role) of one Procedure step, aligned with `g`: its fitted draws
    when the step has been observed, otherwise its role's prior
    d ~ N(mu_role[role], tau_d^2), e ~ N(0, tau_e^2 I) -- the same prior as model.py."""
    from app.routing.config import STEP_ROLES

    if stored is not None:
        orders = [int(o) for o in stored["orders"]]
        if int(order) in orders:
            i = orders.index(int(order))
            return stored["d"][:, i], stored["e"][:, i, :], STEP_ROLES[int(stored["roles"][i])]
    role = role if role in STEP_ROLES else "other"
    arr = g.arrays
    d = arr["mu_role"][:, STEP_ROLES.index(role)] + arr["tau_d"] * rng.standard_normal(g.draws)
    e = arr["tau_e"][:, None] * rng.standard_normal((g.draws, g.k))
    return d, e, role


def check_rates(g: Globals, check_kind: str) -> tuple[np.ndarray, np.ndarray]:
    """(alpha (S,), beta (S,)) of a check kind; 'benchmark' is exactly (0, 0)."""
    kinds = list(g.meta["check_kinds"])
    i = kinds.index(check_kind)
    return g.arrays["alpha"][:, i], g.arrays["beta"][:, i]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
