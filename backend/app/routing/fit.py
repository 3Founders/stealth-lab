"""Inference (docs/model_routing_plan.md §8). Worker only (imports JAX / NumPyro).

nightly_refit   joint posterior of everything public: exact NUTS while the latent count
                allows (STEALTH_ROUTING_NUTS_MAX_LATENTS), normalizing-flow VI
                (AutoBNAFNormal) above it. Stores S aligned draws: globals in
                routing_params, every fitted Goal / Procedure in routing_posteriors.
local_refit     one Goal after a new observation: exact NUTS on its local block with the
                stored global draws MARGINALISED as a discrete mixture (a subset of J
                draws), then defensive importance resampling to one local draw per
                global draw -- so the result stays aligned with all S global draws.
simulation_based_calibration   Talts et al. (2018): simulate from the prior, refit,
                check that ranks of the truth are uniform. Tests the computation.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from app.routing.config import CHECK_KINDS, DEFAULTS, RoutingDefaults
from app.routing.predict import Globals, goal_prior_draws, load_globals, pack, unpack, week_index

log = logging.getLogger(__name__)


def _enable_x64() -> None:
    """Posterior tails of near-0 / near-1 success rates need double precision."""
    import jax

    jax.config.update("jax_enable_x64", True)

SCALAR_SITES = ("sigma_theta", "sigma_drift", "tau_c")


# ================================================================ data assembly

def _epoch(observations: Sequence[Mapping[str, Any]]) -> date:
    days = [o["occurred_at"].date() if isinstance(o["occurred_at"], datetime) else o["occurred_at"]
            for o in observations if o.get("occurred_at")]
    first = min(days) if days else datetime.now(timezone.utc).date()
    return first - timedelta(days=first.weekday())          # Monday of the first week


def _topological_models(observed: set[str], registry: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Observed models plus their predecessor chains, predecessors first."""
    wanted, order, seen = set(observed), [], set()
    for m in list(observed):
        cur, hops = registry.get(m, {}).get("predecessor"), 0
        while cur and cur not in wanted and hops < 32:
            wanted.add(cur)
            cur, hops = registry.get(cur, {}).get("predecessor"), hops + 1

    def visit(m: str, depth: int = 0) -> None:
        if m in seen or depth > 64:
            return
        pred = registry.get(m, {}).get("predecessor")
        if pred in wanted:
            visit(pred, depth + 1)
        seen.add(m)
        order.append(m)

    for m in sorted(wanted):
        visit(m)
    return order


def fit_pca(embeddings: Sequence[Optional[Sequence[float]]], dims: int) -> tuple[np.ndarray, np.ndarray]:
    rows = [np.asarray(e, dtype=np.float64) for e in embeddings if e is not None]
    if len(rows) < 2:
        return np.zeros(0), np.zeros((0, 0))
    x = np.stack(rows)
    mean = x.mean(axis=0)
    _, sv, vt = np.linalg.svd(x - mean, full_matrices=False)
    p = min(dims, len(rows) - 1, vt.shape[0])
    comps = vt[:p] / np.maximum(sv[:p, None] / np.sqrt(len(rows)), 1e-9)   # whitened: unit-variance scores
    return mean, comps


def _levels(goals: list[str], parents: Mapping[str, Sequence[str]]) -> list[np.ndarray]:
    index = {g: i for i, g in enumerate(goals)}
    depth: dict[str, int] = {}

    def d(g: str, stack: frozenset = frozenset()) -> int:
        if g in depth:
            return depth[g]
        ps = [p for p in parents.get(g, []) if p in index and p not in stack]
        depth[g] = 0 if not ps else 1 + max(d(p, stack | {g}) for p in ps)
        return depth[g]

    for g in goals:
        d(g)
    by: dict[int, list[int]] = defaultdict(list)
    for g in goals:
        by[depth[g]].append(index[g])
    return [np.asarray(by[k], dtype=np.int32) for k in sorted(by)]


def build_joint_data(observations: Sequence[Mapping[str, Any]], goal_meta: Mapping[str, Mapping[str, Any]],
                     parents: Mapping[str, Sequence[str]], registry: Mapping[str, Mapping[str, Any]],
                     cfg: RoutingDefaults = DEFAULTS) -> tuple[dict[str, Any], dict[str, Any]]:
    """(numeric data for model.joint_model, index maps / meta)."""
    obs = [o for o in observations if str(o["goal_id"]) in goal_meta]
    epoch = _epoch(obs)
    models = _topological_models({o["model_key"] for o in obs}, registry)
    m_ix = {m: i for i, m in enumerate(models)}
    scaffolds = sorted({o["scaffold"] for o in obs})
    s_ix = {s: i for i, s in enumerate(scaffolds)}
    reporters = sorted({o["reporter"] for o in obs if o.get("reporter")})
    r_ix = {r: i for i, r in enumerate(reporters)}

    goals = sorted(goal_meta)
    g_ix = {g: i for i, g in enumerate(goals)}
    procs = sorted({str(o["procedure_id"]) for o in obs if o.get("procedure_id")})
    p_ix = {p: i for i, p in enumerate(procs)}

    pca_mean, pca_comp = fit_pca([goal_meta[g].get("embedding") for g in goals], cfg.embedding_dims)
    p_dim = pca_comp.shape[0]
    phi1 = np.ones((len(goals), p_dim + 1))
    for g, i in g_ix.items():
        emb = goal_meta[g].get("embedding")
        phi1[i, 1:] = pca_comp @ (np.asarray(emb) - pca_mean) if (emb is not None and p_dim) else 0.0
    max_p = max([len([p for p in parents.get(g, []) if p in g_ix]) for g in goals] + [1])
    par = -np.ones((len(goals), max_p), dtype=np.int32)
    for g, i in g_ix.items():
        ps = [g_ix[p] for p in parents.get(g, []) if p in g_ix]
        par[i, :len(ps)] = ps
    weeks = [week_index(o["occurred_at"], epoch) for o in obs]
    n_weeks = (max(weeks) + 1) if weeks else 1

    first_week = np.zeros(len(models), dtype=np.int32)
    last_week = np.zeros(len(models), dtype=np.int32)
    seen: dict[int, list[int]] = defaultdict(list)
    for o, w in zip(obs, weeks):
        seen[m_ix[o["model_key"]]].append(w)
    for m in range(len(models)):
        if seen[m]:
            first_week[m], last_week[m] = min(seen[m]), max(seen[m])
    pred = np.array([m_ix.get(registry.get(m, {}).get("predecessor"), -1) for m in models], dtype=np.int32)
    from app.routing.model_constants import effective_dims
    k_eff = effective_dims(len(models), cfg.latent_dims)

    instances: dict[tuple[str, str], int] = {}
    att_instance = []
    for o in obs:
        key = (str(o["goal_id"]), str(o["instance_key"]))
        att_instance.append(instances.setdefault(key, len(instances)))
    gold = np.array([-1 if o.get("gold_correct") is None else int(bool(o["gold_correct"])) for o in obs], dtype=np.int32)
    data = {
        "k": k_eff, "n_models": len(models), "n_scaffolds": max(len(scaffolds), 1), "n_goals": len(goals),
        "n_procedures": len(procs), "n_reporters": len(reporters), "n_weeks": n_weeks,
        "n_attempts": len(obs), "n_instances": max(len(instances), 1),
        "phi1": phi1, "parents": par, "parent_mask": (par >= 0).astype(np.float64), "levels": _levels(goals, parents),
        "model_pred": pred, "model_first_week": first_week,
        "att_model": np.array([m_ix[o["model_key"]] for o in obs], dtype=np.int32),
        "att_scaffold": np.array([s_ix[o["scaffold"]] for o in obs], dtype=np.int32),
        "att_goal": np.array([g_ix[str(o["goal_id"])] for o in obs], dtype=np.int32),
        "att_proc": np.array([p_ix.get(str(o.get("procedure_id")), -1) if o.get("procedure_id") else -1 for o in obs],
                             dtype=np.int32),
        "att_week": np.array(weeks, dtype=np.int32),
        "att_check": np.array([CHECK_KINDS.index(o["check_kind"]) for o in obs], dtype=np.int32),
        "att_reporter": np.array([r_ix.get(o.get("reporter"), -1) if o.get("reporter") else -1 for o in obs],
                                 dtype=np.int32),
        "att_accepted": np.array([bool(o["accepted"]) for o in obs]),
        "att_gold": gold,
        "att_instance": np.array(att_instance, dtype=np.int32),
    }
    meta = {
        "k": k_eff, "models": models, "scaffolds": scaffolds or ["_none"], "reporters": reporters,
        "goals": goals, "procedures": procs, "epoch": epoch.isoformat(), "check_kinds": list(CHECK_KINDS),
        "model_pred": [registry.get(m, {}).get("predecessor") for m in models],
        "model_last_week": last_week.tolist(), "n_weeks": n_weeks,
        "registry_predecessors": {m: r["predecessor"] for m, r in registry.items() if r.get("predecessor")},
        "n_observations": len(obs),
        "goal_observations": {g: sum(1 for o in obs if str(o["goal_id"]) == g) for g in goals},
        "procedure_observations": {p: sum(1 for o in obs if str(o.get("procedure_id")) == p) for p in procs},
        "_pca_mean": pca_mean, "_pca_components": pca_comp,
    }
    return data, meta


def latent_count(data: Mapping[str, Any], k: int) -> int:
    k = int(data.get("k", k))
    m, s, g, p, w = data["n_models"], data["n_scaffolds"], data["n_goals"], data["n_procedures"], data["n_weeks"]
    return m * (1 + w + k) + s + m * s + g * (k + 2) + max(p, 1) * k + data["n_reporters"] + 40


# ================================================================ joint fit

def run_joint(data: dict[str, Any], cfg: RoutingDefaults = DEFAULTS, *, seed: int = 0,
              num_draws: Optional[int] = None, method: Optional[str] = None) -> tuple[dict[str, np.ndarray], str, dict]:
    """Posterior draws (S of each site / deterministic), the method used, diagnostics."""
    import jax
    import jax.numpy as jnp  # noqa: F401
    from numpyro.infer import MCMC, NUTS, Predictive, SVI, Trace_ELBO
    from numpyro.infer.autoguide import AutoBNAFNormal
    from numpyro.optim import Adam

    from app.routing.model import joint_model

    _enable_x64()
    s_draws = num_draws or cfg.draws
    k = int(data["k"])
    args = (data, k, cfg.gh_eps_nodes)
    chosen = method or ("nuts" if latent_count(data, k) <= cfg.nuts_max_latents else "flow_vi")
    key = jax.random.PRNGKey(seed)
    if chosen == "nuts":
        chains = cfg.nightly_chains
        per_chain = max(int(np.ceil(s_draws / chains)), cfg.nightly_samples)
        mcmc = MCMC(NUTS(joint_model, target_accept_prob=0.9), num_warmup=cfg.nightly_warmup, num_samples=per_chain,
                    num_chains=chains, chain_method="sequential", progress_bar=False)
        mcmc.run(key, *args)
        raw = mcmc.get_samples()
        total = next(iter(raw.values())).shape[0]
        keep = np.linspace(0, total - 1, s_draws).round().astype(int)
        samples = {k_: np.asarray(v)[keep] for k_, v in raw.items()}
        diag = _nuts_diagnostics(mcmc)
    else:
        guide = AutoBNAFNormal(joint_model, num_flows=2)
        svi = SVI(joint_model, guide, Adam(1e-3), Trace_ELBO())
        result = svi.run(key, 20000, *args, progress_bar=False)
        post = Predictive(guide, params=result.params, num_samples=s_draws)(jax.random.PRNGKey(seed + 1), *args)
        samples = {k_: np.asarray(v) for k_, v in post.items()}
        diag = {"final_elbo_loss": float(result.losses[-1]), "svi_steps": 20000}
    det = Predictive(joint_model, posterior_samples=samples, return_sites=[
        "theta_hist", "z", "gamma", "delta", "w", "tau", "goal_x", "proc_c"])(jax.random.PRNGKey(seed + 2), *args)
    samples.update({k_: np.asarray(v) for k_, v in det.items()})
    return samples, chosen, diag


def _nuts_diagnostics(mcmc: Any) -> dict:
    from numpyro.diagnostics import summary

    grouped = mcmc.get_samples(group_by_chain=True)
    stats = summary({k: v for k, v in grouped.items()}, prob=0.9)
    rhat = [float(np.nanmax(s["r_hat"])) for s in stats.values() if "r_hat" in s]
    ess = [float(np.nanmin(s["n_eff"])) for s in stats.values() if "n_eff" in s]
    divergences = int(np.asarray(mcmc.get_extra_fields()["diverging"]).sum())
    return {"max_r_hat": max(rhat) if rhat else None, "min_ess": min(ess) if ess else None, "divergences": divergences}


def global_arrays(samples: Mapping[str, np.ndarray], meta: Mapping[str, Any]) -> dict[str, np.ndarray]:
    last = np.asarray(meta["model_last_week"], dtype=np.int64)
    theta_hist = samples["theta_hist"]
    alpha = np.stack([np.zeros(theta_hist.shape[0]) if k == "benchmark" else samples[f"alpha_{k}"]
                      for k in CHECK_KINDS], axis=1)
    beta = np.stack([np.zeros(theta_hist.shape[0]) if k == "benchmark" else samples[f"beta_{k}"]
                     for k in CHECK_KINDS], axis=1)
    return {
        "theta_last": theta_hist[:, np.arange(theta_hist.shape[1]), last] if theta_hist.shape[1] else
        np.zeros((theta_hist.shape[0], 0)),
        "theta_hist": theta_hist,
        "sigma_theta": samples["sigma_theta"], "sigma_drift": samples["sigma_drift"],
        "z": samples["z"], "gamma": samples["gamma"], "delta": samples["delta"], "w": samples["w"],
        "tau": samples["tau"], "tau_c": samples["tau_c"], "tau_rho": samples["tau_rho"],
        "alpha": alpha, "beta": beta,
        "pca_mean": np.asarray(meta["_pca_mean"]), "pca_components": np.asarray(meta["_pca_components"]),
    }


def public_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in meta.items() if not k.startswith("_") and k not in (
        "goals", "procedures", "goal_observations", "procedure_observations")}


async def nightly_refit(pool: Any, cfg: RoutingDefaults = DEFAULTS, *, seed: int = 0,
                        method: Optional[str] = None) -> dict[str, Any]:
    from app.routing import store

    observations = await store.all_observations(pool, public_only=True)
    observed_goals = sorted({str(o["goal_id"]) for o in observations})
    parents = await store.ancestry(pool, observed_goals)
    all_goals = sorted(set(observed_goals) | {p for ps in parents.values() for p in ps})
    rows = await store.goal_rows(pool, all_goals)
    goal_meta = {g: r for g, r in rows.items() if r["visibility"] == "public"}
    registry = await store.model_registry(pool)
    data, meta = build_joint_data(observations, goal_meta, parents, registry, cfg)
    samples, used, diag = run_joint(data, cfg, seed=seed, method=method)
    arrays = global_arrays(samples, meta)
    from app.routing.service import token_summary

    stored_meta = {**public_meta(meta), "tokens": token_summary(observations)}
    version = await store.save_params(pool, method=used, draws=pack(arrays), meta=stored_meta, diagnostics=diag)
    last_at: dict[str, Any] = {}
    for o in observations:
        last_at[str(o["goal_id"])] = o["occurred_at"]
    posts = [{"kind": "goal", "id": g, "version": version, "method": "joint",
              "draws": pack({"x": samples["goal_x"][:, i, :]}),
              "n_observations": meta["goal_observations"].get(g, 0), "last_observation_at": last_at.get(g)}
             for i, g in enumerate(meta["goals"])]
    posts += [{"kind": "procedure", "id": p, "version": version, "method": "joint",
               "draws": pack({"c": samples["proc_c"][:, i, :]}),
               "n_observations": meta["procedure_observations"].get(p, 0)}
              for i, p in enumerate(meta["procedures"])]
    await store.save_posteriors(pool, posts)
    # Goals the joint fit does not cover (private observations) are refreshed against
    # the new global draws right away, so no stored posterior stays misaligned.
    private = await store.all_observations(pool, public_only=False)
    stale = sorted({str(o["goal_id"]) for o in private if o["visibility"] != "public"})
    for g in stale:
        await local_refit(pool, g, cfg, seed=seed)
    return {"version": version, "method": used, "diagnostics": diag, "goals": len(meta["goals"]),
            "procedures": len(meta["procedures"]), "observations": meta["n_observations"],
            "refreshed_private_goals": len(stale)}


# ================================================================ local refit

def may_pool(parent: Mapping[str, Any], child: Mapping[str, Any]) -> bool:
    """A parent's data may inform a child's prior only if everyone who can see the
    child can see the parent: a public parent, or a parent with the child's owner."""
    return parent.get("visibility") == "public" or (
        parent.get("owner_id") is not None and parent.get("owner_id") == child.get("owner_id"))


async def aligned_goal_draws(pool: Any, g: Globals, goal_id: str, *, rng: np.random.Generator,
                             cache: Optional[dict] = None, depth: int = 0) -> np.ndarray:
    """(S, D) draws of a Goal aligned with `g`: its stored posterior if it was fitted
    against these global draws, otherwise its prior given its parents' aligned draws."""
    from app.routing import store

    cache = {} if cache is None else cache
    if goal_id in cache:
        return cache[goal_id]
    stored = (await store.load_posteriors(pool, "goal", [goal_id])).get(goal_id)
    if stored and stored["version"] == g.version:
        cache[goal_id] = unpack(stored["draws"])["x"]
        return cache[goal_id]
    meta = (await store.goal_rows(pool, [goal_id])).get(goal_id, {})
    parent_ids = (await store.goal_parents(pool, [goal_id])).get(goal_id, []) if depth < 64 else []
    parents = []
    for p in parent_ids:
        prow = (await store.goal_rows(pool, [p])).get(p)
        if prow is None or not may_pool(prow, meta):
            continue
        parents.append((await aligned_goal_draws(pool, g, p, rng=rng, cache=cache, depth=depth + 1),
                        g.phi1(prow.get("embedding"))))
    cache[goal_id] = goal_prior_draws(g, g.phi1(meta.get("embedding")), parents, rng)
    return cache[goal_id]


def _goal_prior_parts(g: Globals, phi1: np.ndarray, parents: Sequence[tuple[np.ndarray, np.ndarray]]
                      ) -> np.ndarray:
    """mu (S, D) of the Goal block: everything except tau * xi."""
    w = g.arrays["w"]
    base = np.einsum("sdp,p->sd", w, phi1)
    if parents:
        base = base + np.mean([xp - np.einsum("sdp,p->sd", w, php) for xp, php in parents], axis=0)
    return base


async def local_refit(pool: Any, goal_id: str, cfg: RoutingDefaults = DEFAULTS, *, seed: int = 0) -> dict[str, Any]:
    """Exact local posterior of one Goal's block (+ the Procedures observed on it)."""
    from app.routing import store

    params = await store.active_params(pool)
    if params is None:
        return {"skipped": "no fitted parameters yet -- run the nightly refit (admin routing-refit)"}
    g = load_globals(params["version"], params["draws"], params["meta"])
    observations = await store.goal_observations(pool, goal_id)
    meta = (await store.goal_rows(pool, [goal_id])).get(goal_id)
    if meta is None:
        return {"skipped": "goal not in goal_search_index"}
    rng = np.random.default_rng(seed)
    cache: dict = {}
    parent_ids = (await store.goal_parents(pool, [goal_id])).get(goal_id, [])
    parents = []
    for p in parent_ids:
        prow = (await store.goal_rows(pool, [p])).get(p)
        if prow is not None and may_pool(prow, meta):
            parents.append((await aligned_goal_draws(pool, g, p, rng=rng, cache=cache), g.phi1(prow.get("embedding"))))
    mu = _goal_prior_parts(g, g.phi1(meta.get("embedding")), parents)
    if not observations:
        x = mu + g.arrays["tau"] * rng.standard_normal(mu.shape)
        await store.save_posteriors(pool, [{"kind": "goal", "id": goal_id, "version": g.version, "method": "local_nuts",
                                            "draws": pack({"x": x}), "n_observations": 0}])
        return {"goal_id": goal_id, "observations": 0}
    local = _local_data(g, observations, rng)
    xi_goal, xi_proc, ess = _local_posterior(g, mu, local, cfg, seed)
    x = mu + g.arrays["tau"] * xi_goal
    rows = [{"kind": "goal", "id": goal_id, "version": g.version, "method": "local_nuts", "draws": pack({"x": x}),
             "n_observations": len(observations), "last_observation_at": observations[-1]["occurred_at"]}]
    for j, pid in enumerate(local["procedures"]):
        rows.append({"kind": "procedure", "id": pid, "version": g.version, "method": "local_nuts",
                     "draws": pack({"c": g.arrays["tau_c"][:, None] * xi_proc[:, j, :]}),
                     "n_observations": sum(1 for o in observations if str(o.get("procedure_id")) == pid)})
    await store.save_posteriors(pool, rows)
    return {"goal_id": goal_id, "observations": len(observations), "params_version": g.version, "ess": ess}


def _local_data(g: Globals, observations: Sequence[Mapping[str, Any]], rng: np.random.Generator) -> dict[str, Any]:
    """Per-draw known terms and slots for latents the global fit has not seen."""
    arr, s_count, k = g.arrays, g.draws, g.k
    m_ix = {m: i for i, m in enumerate(g.models)}
    s_ix = {s: i for i, s in enumerate(g.scaffolds)}
    new_models = sorted({o["model_key"] for o in observations if o["model_key"] not in m_ix})
    new_scaffolds = sorted({o["scaffold"] for o in observations if o["scaffold"] not in s_ix})
    new_pairs = sorted({(o["model_key"], o["scaffold"]) for o in observations
                        if o["model_key"] not in m_ix or o["scaffold"] not in s_ix})
    goal_reporters = sorted({o["reporter"] for o in observations if o.get("reporter")})
    procs = sorted({str(o["procedure_id"]) for o in observations if o.get("procedure_id")})
    n_weeks = arr["theta_hist"].shape[2] if arr["theta_hist"].ndim == 3 else 1
    r_count = len(observations)
    known_theta = np.zeros((s_count, r_count))
    known_z = np.zeros((s_count, r_count, k))
    known_gd = np.zeros((s_count, r_count))
    new_m_slot = -np.ones(r_count, dtype=np.int32)
    new_s_slot = -np.ones(r_count, dtype=np.int32)
    new_pair_slot = -np.ones(r_count, dtype=np.int32)
    for r, o in enumerate(observations):
        m, s = o["model_key"], o["scaffold"]
        if m in m_ix:
            wk = min(week_index(o["occurred_at"], g.epoch), n_weeks - 1)
            known_theta[:, r] = arr["theta_hist"][:, m_ix[m], wk]
            known_z[:, r, :] = arr["z"][:, m_ix[m], :]
        else:
            new_m_slot[r] = new_models.index(m)
        if s in s_ix:
            known_gd[:, r] += arr["gamma"][:, s_ix[s]]
        else:
            new_s_slot[r] = new_scaffolds.index(s)
        if m in m_ix and s in s_ix:
            known_gd[:, r] += arr["delta"][:, m_ix[m], s_ix[s]]
        else:
            new_pair_slot[r] = new_pairs.index((m, s))
    # predecessor anchors for new models (their nearest known ancestor, else population)
    registry_pred = g.meta.get("registry_predecessors", {})
    anchor_theta = np.zeros((s_count, max(len(new_models), 1)))
    anchor_z = np.zeros((s_count, max(len(new_models), 1), k))
    anchor_scale = np.ones(max(len(new_models), 1))
    has_anchor = np.zeros(max(len(new_models), 1), dtype=bool)
    for i, m in enumerate(new_models):
        pred, hops = registry_pred.get(m), 0
        while pred and pred not in m_ix and hops < 32:
            pred, hops = registry_pred.get(pred), hops + 1
        if pred in m_ix:
            anchor_theta[:, i] = arr["theta_last"][:, m_ix[pred]]
            anchor_z[:, i, :] = arr["z"][:, m_ix[pred], :]
            has_anchor[i] = True
    instances: dict[str, int] = {}
    inst = np.array([instances.setdefault(str(o["instance_key"]), len(instances)) for o in observations], dtype=np.int32)
    return {
        "known_theta": known_theta, "known_z": known_z, "known_gd": known_gd,
        "new_m_slot": new_m_slot, "new_s_slot": new_s_slot, "new_pair_slot": new_pair_slot,
        "n_new_models": len(new_models), "n_new_scaffolds": len(new_scaffolds), "n_new_pairs": len(new_pairs),
        "anchor_theta": anchor_theta, "anchor_z": anchor_z, "has_anchor": has_anchor, "anchor_scale": anchor_scale,
        "procedures": procs,
        "proc": np.array([procs.index(str(o["procedure_id"])) if o.get("procedure_id") else -1 for o in observations],
                         dtype=np.int32),
        "check": np.array([CHECK_KINDS.index(o["check_kind"]) for o in observations], dtype=np.int32),
        "reporter": np.array([goal_reporters.index(o["reporter"]) if o.get("reporter") else -1
                              for o in observations], dtype=np.int32),
        "n_reporters": len(goal_reporters),
        "accepted": np.array([bool(o["accepted"]) for o in observations]),
        "gold": np.array([-1 if o.get("gold_correct") is None else int(bool(o["gold_correct"])) for o in observations],
                         dtype=np.int32),
        "instance": inst, "n_instances": max(len(instances), 1),
    }


def _local_posterior(g: Globals, mu: np.ndarray, local: Mapping[str, Any], cfg: RoutingDefaults,
                     seed: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """NUTS over the local non-centred block with the global draws marginalised as a
    J-component mixture, then one importance-resampled local draw per global draw."""
    import jax
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from jax.scipy.special import logsumexp
    from numpyro.infer import MCMC, NUTS

    from app.routing.model import SIGMA_NEW_VERSION, SUCCESSOR_Z_SD, TAU_DELTA, TAU_GAMMA, attempt_loglik, BENCHMARK

    _enable_x64()
    arr, s_count, k = g.arrays, g.draws, g.k
    d = k + 2
    n_proc = max(len(local["procedures"]), 1)
    rng = np.random.default_rng(seed)
    j_count = min(cfg.local_global_draws, s_count)
    subset = np.sort(rng.choice(s_count, size=j_count, replace=False))

    # reporter offsets are per-Goal latents here (scale tau_rho from the global draw); the
    # nightly joint fit re-estimates them across all Goals
    fixed = {
        "mu": jnp.asarray(mu), "tau": jnp.asarray(arr["tau"]), "tau_c": jnp.asarray(arr["tau_c"]),
        "sigma_theta": jnp.asarray(arr["sigma_theta"]),
        "tau_rho": jnp.asarray(arr["tau_rho"]),
        "alpha": jnp.asarray(arr["alpha"]), "beta": jnp.asarray(arr["beta"]),
        "known_theta": jnp.asarray(local["known_theta"]), "known_z": jnp.asarray(local["known_z"]),
        "known_gd": jnp.asarray(local["known_gd"]), "anchor_theta": jnp.asarray(local["anchor_theta"]),
        "anchor_z": jnp.asarray(local["anchor_z"]),
    }
    nm, ns, npair, nrep = (max(local["n_new_models"], 1), max(local["n_new_scaffolds"], 1),
                           max(local["n_new_pairs"], 1), max(local["n_reporters"], 1))
    m_slot, s_slot, pair_slot = (jnp.asarray(local["new_m_slot"]), jnp.asarray(local["new_s_slot"]),
                                 jnp.asarray(local["new_pair_slot"]))
    has_anchor = jnp.asarray(local["has_anchor"])
    proc_ix, check = jnp.asarray(local["proc"]), jnp.asarray(local["check"])
    rep_ix = jnp.asarray(local["reporter"])
    accepted, gold = jnp.asarray(local["accepted"]), jnp.asarray(local["gold"])
    instance, n_inst = jnp.asarray(local["instance"]), local["n_instances"]

    def loglik_per_draw(latent: Mapping[str, Any], rows: Any) -> Any:
        """(len(rows),) log-likelihood of this Goal's attempts under global draws `rows`."""
        f = {key: v[rows] for key, v in fixed.items()}
        x = f["mu"] + f["tau"] * latent["xi_goal"][None, :]                            # (J, D)
        c = f["tau_c"][:, None, None] * latent["xi_proc"][None, :, :]                  # (J, P, K)
        new_theta = jnp.where(has_anchor[None, :],
                              f["anchor_theta"] + SIGMA_NEW_VERSION * latent["xi_m"][None, :],
                              f["sigma_theta"][:, None] * latent["xi_m"][None, :])     # (J, NM)
        new_z = jnp.where(has_anchor[None, :, None],
                          f["anchor_z"] + SUCCESSOR_Z_SD * latent["xi_z"][None, :, :],
                          jnp.broadcast_to(latent["xi_z"][None, :, :], f["anchor_z"].shape))
        theta = jnp.where(m_slot[None, :] >= 0, new_theta[:, jnp.clip(m_slot, 0)], f["known_theta"])
        z = jnp.where((m_slot >= 0)[None, :, None], new_z[:, jnp.clip(m_slot, 0), :], f["known_z"])
        gd = (f["known_gd"]
              + jnp.where(s_slot >= 0, TAU_GAMMA * latent["xi_s"][jnp.clip(s_slot, 0)], 0.0)[None, :]
              + jnp.where(pair_slot >= 0, TAU_DELTA * latent["xi_pair"][jnp.clip(pair_slot, 0)], 0.0)[None, :])
        cp = jnp.where((proc_ix >= 0)[None, :, None], c[:, jnp.clip(proc_ix, 0), :], 0.0)
        logit_r = (theta + gd - x[:, [0]] + (x[:, None, 2:] * z).sum(-1) + (cp * z).sum(-1))  # (J, R)
        sig_eps = jnp.exp(x[:, 1])
        base_alpha = f["alpha"][:, check]
        rho = f["tau_rho"][:, None] * latent["xi_rho"][None, :]                        # (J, NR)
        shifted = jax.nn.sigmoid(jnp.log(jnp.clip(base_alpha, 1e-6, 1 - 1e-6))
                                 - jnp.log1p(-jnp.clip(base_alpha, 1e-6, 1 - 1e-6)) + rho[:, jnp.clip(rep_ix, 0)])
        alpha_r = jnp.where((rep_ix >= 0) & (check != BENCHMARK), shifted, base_alpha)
        beta_r = f["beta"][:, check]
        return jax.vmap(lambda lg, se, al, be: attempt_loglik(
            lg, jnp.broadcast_to(se, lg.shape), al, be, accepted, gold, instance, n_inst, cfg.gh_eps_nodes)
        )(logit_r, sig_eps, alpha_r, beta_r)

    sub = jnp.asarray(subset)

    def local_model() -> None:
        latent = {
            "xi_goal": numpyro.sample("xi_goal", dist.Normal(jnp.zeros(d), 1.0).to_event(1)),
            "xi_proc": numpyro.sample("xi_proc", dist.Normal(jnp.zeros((n_proc, k)), 1.0).to_event(2)),
            "xi_m": numpyro.sample("xi_m", dist.Normal(jnp.zeros(nm), 1.0).to_event(1)),
            "xi_z": numpyro.sample("xi_z", dist.Normal(jnp.zeros((nm, k)), 1.0).to_event(2)),
            "xi_s": numpyro.sample("xi_s", dist.Normal(jnp.zeros(ns), 1.0).to_event(1)),
            "xi_pair": numpyro.sample("xi_pair", dist.Normal(jnp.zeros(npair), 1.0).to_event(1)),
            "xi_rho": numpyro.sample("xi_rho", dist.Normal(jnp.zeros(nrep), 1.0).to_event(1)),
        }
        numpyro.factor("mixture", logsumexp(loglik_per_draw(latent, sub)) - jnp.log(j_count))

    chains, per_chain = 4, s_count          # 4 * S NUTS samples feed the resampling of S aligned draws
    mcmc = MCMC(NUTS(local_model, target_accept_prob=0.9), num_warmup=cfg.local_warmup, num_samples=per_chain,
                num_chains=chains, chain_method="sequential", progress_bar=False)
    mcmc.run(jax.random.PRNGKey(seed))
    post = mcmc.get_samples()
    t_count = post["xi_goal"].shape[0]
    all_rows = jnp.arange(s_count)
    ll_all = jax.vmap(lambda t: loglik_per_draw({k_: v[t] for k_, v in post.items()}, all_rows))(jnp.arange(t_count))
    ll_all = np.asarray(ll_all)                                       # (T, S)
    log_q = np.asarray(logsumexp(ll_all[:, subset], axis=1)) - np.log(j_count)
    log_w = ll_all - log_q[:, None]                                   # defensive-mixture importance weights
    xi_goal = np.empty((s_count, d))
    xi_proc = np.empty((s_count, n_proc, k))
    ess = []
    for j in range(s_count):
        w = np.exp(log_w[:, j] - log_w[:, j].max())
        w /= w.sum()
        ess.append(1.0 / float((w ** 2).sum()))
        t = int(rng.choice(t_count, p=w))
        xi_goal[j] = np.asarray(post["xi_goal"][t])
        xi_proc[j] = np.asarray(post["xi_proc"][t])
    return xi_goal, xi_proc, {"min": float(np.min(ess)), "median": float(np.median(ess)), "samples": t_count,
                              "mixture_draws": j_count}


# ================================================================ SBC

def simulate(rng: np.random.Generator, data: dict[str, Any], samples: Mapping[str, np.ndarray], s: int,
             k: int) -> np.ndarray:
    """Draw `accepted` for every attempt of `data` under prior draw s (checks = benchmark)."""
    th = samples["theta_hist"][s]
    x, z = samples["goal_x"][s], samples["z"][s]
    c = samples["proc_c"][s]
    mi, gi, pi_ = data["att_model"], data["att_goal"], data["att_proc"]
    proc_term = np.where((pi_ >= 0)[:, None], c[np.clip(pi_, 0, None)], 0.0)
    logit = (th[mi, data["att_week"]] + samples["gamma"][s][data["att_scaffold"]]
             + samples["delta"][s][mi, data["att_scaffold"]] - x[gi, 0] + (x[gi, 2:] * z[mi]).sum(1)
             + (proc_term * z[mi]).sum(1))
    inst_goal = np.zeros(data["n_instances"], dtype=np.int64)
    inst_goal[data["att_instance"]] = gi
    eps = (np.exp(x[inst_goal, 1]) * rng.standard_normal(data["n_instances"]))[data["att_instance"]]
    return rng.random(len(mi)) < 1 / (1 + np.exp(-(logit - eps)))


def simulation_based_calibration(data: dict[str, Any], cfg: RoutingDefaults = DEFAULTS, *, sims: int = 100,
                                 seed: int = 0, draws: int = 100) -> dict[str, list[int]]:
    """Ranks of the true value among `draws` posterior draws, per tracked quantity.
    Uniform ranks <=> the inference computes this model's posterior correctly."""
    import jax
    from numpyro.infer import Predictive

    from app.routing.model import joint_model

    k = int(data["k"])
    prior = Predictive(joint_model, num_samples=sims, return_sites=[
        "theta_hist", "z", "gamma", "delta", "goal_x", "proc_c", "sigma_theta"])(
        jax.random.PRNGKey(seed), {**data, "n_attempts": 0}, k, cfg.gh_eps_nodes)
    prior = {key: np.asarray(v) for key, v in prior.items()}
    rng = np.random.default_rng(seed)
    ranks: dict[str, list[int]] = defaultdict(list)
    sim_cfg = RoutingDefaults(draws=draws, nightly_warmup=cfg.nightly_warmup, nightly_chains=2, latent_dims=k,
                              embedding_dims=cfg.embedding_dims, nuts_max_latents=10 ** 9,
                              nightly_samples=cfg.nightly_samples)
    for s in range(sims):
        sim = dict(data)
        sim["att_accepted"] = simulate(rng, data, prior, s, k)
        sim["att_check"] = np.full(data["n_attempts"], CHECK_KINDS.index("benchmark"), dtype=np.int32)
        post, _, _ = run_joint(sim, sim_cfg, seed=seed + 1 + s, num_draws=draws, method="nuts")
        tracked = {
            "goal0_difficulty": (prior["goal_x"][s, 0, 0], post["goal_x"][:, 0, 0]),
            "model0_ability": (prior["theta_hist"][s, 0, -1], post["theta_hist"][:, 0, -1]),
            "goal0_log_sigma_eps": (prior["goal_x"][s, 0, 1], post["goal_x"][:, 0, 1]),
        }
        for name, (truth, draws_) in tracked.items():
            ranks[name].append(int((np.asarray(draws_) < truth).sum()))
    return dict(ranks)
