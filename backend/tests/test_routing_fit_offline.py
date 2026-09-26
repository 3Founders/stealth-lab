"""Model recommender inference (app/routing/fit.py, model.py) on synthetic data.

Needs requirements-routing.txt (JAX + NumPyro); skipped without it. The design is
realistic: every instance is attempted by several models (as public benchmark results
and escalation ladders produce), which is what identifies the instance difficulty."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

pytest.importorskip("numpyro")

from app.routing import fit  # noqa: E402
from app.routing.config import RoutingDefaults  # noqa: E402
from app.routing.predict import Globals, success_given_eps, unit_terms  # noqa: E402

CFG = RoutingDefaults(draws=64, nightly_warmup=300, nightly_chains=2, nightly_samples=300, embedding_dims=3,
                      local_global_draws=8, local_warmup=150, latent_dims=2)
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
THETA = {"lab/small": -1.0, "lab/big": 1.0, "lab/big-2": 1.5}
REGISTRY = {"lab/small": {"predecessor": None}, "lab/big": {"predecessor": None},
            "lab/big-2": {"predecessor": "lab/big"}}


def _design(rng, goals, b, instances=100):
    obs = []
    for i in range(instances):
        g = goals[i % len(goals)]
        eps = 0.8 * rng.standard_normal()
        for m, th in THETA.items():
            p = 1 / (1 + np.exp(-(th - b[g] - eps)))
            obs.append(dict(goal_id=g, model_key=m, scaffold="claude-code", instance_key=f"{g}-{i}",
                            check_kind="benchmark", accepted=bool(rng.random() < p),
                            occurred_at=T0 + timedelta(days=i // 10), procedure_id=None, reporter=None,
                            gold_correct=None, visibility="public"))
    return obs


@pytest.fixture(scope="module")
def fitted():
    rng = np.random.default_rng(0)
    goals = [str(uuid.UUID(int=i + 1)) for i in range(5)]
    b = dict(zip(goals, [0.0, -1.0, 1.0, 0.5, 2.0]))
    parents = {goals[2]: [goals[0]], goals[3]: [goals[0], goals[1]], goals[4]: [goals[2]]}
    meta = {g: {"embedding": list(rng.standard_normal(6)), "visibility": "public"} for g in goals}
    obs = _design(rng, goals, b)
    data, info = fit.build_joint_data(obs, meta, parents, REGISTRY, CFG)
    samples, method, diag = fit.run_joint(data, CFG, seed=1)
    arrays = fit.global_arrays(samples, info)
    stored = {**fit.public_meta(info), "tokens": {}}
    return {"goals": goals, "b": b, "info": info, "samples": samples, "method": method, "diag": diag,
            "globals": Globals(version=1, arrays=arrays, meta=stored)}


def test_joint_nuts_converges_and_recovers_the_truth(fitted):
    assert fitted["method"] == "nuts"
    assert fitted["diag"]["divergences"] == 0
    assert fitted["diag"]["max_r_hat"] < 1.05
    info, s = fitted["info"], fitted["samples"]
    theta = dict(zip(info["models"], s["theta_hist"][:, :, -1].mean(axis=0)))
    assert theta["lab/small"] < theta["lab/big"] < theta["lab/big-2"]
    # only DIFFERENCES of difficulty are identified (ability and difficulty share a
    # location); each true difference lies in its 95% posterior interval
    draws_b = s["goal_x"][:, :, 0]
    truth = np.array([fitted["b"][g] for g in info["goals"]])
    for j in range(1, len(truth)):
        diff = draws_b[:, j] - draws_b[:, 0]
        lo, hi = np.quantile(diff, [0.025, 0.975])
        assert lo <= truth[j] - truth[0] <= hi
    # aligned draw storage: every array has the same S rows
    assert {v.shape[0] for k, v in fitted["globals"].arrays.items() if not k.startswith("pca")} == {CFG.draws}


def test_decision_side_probabilities_order_the_models(fitted):
    g = fitted["globals"]
    goal_x = fitted["samples"]["goal_x"][:, 0, :]
    rng = np.random.default_rng(0)
    units = [("lab/small", "claude-code"), ("lab/big-2", "claude-code"), ("lab/big-3", "claude-code")]
    base, z = unit_terms(g, units, {"lab/big-3": "lab/big-2"}, T0 + timedelta(days=70), rng)
    p, w = success_given_eps(g, goal_x, None, base, z, eps_nodes=20)
    mean = (p * w).sum(axis=2).mean(axis=0)
    assert mean[0] < mean[1]
    # an unseen successor inherits its predecessor's ability (wider): close to it on average
    assert abs(mean[2] - mean[1]) < 0.2


def test_local_refit_stays_aligned_and_learns_from_one_goal(fitted):
    g = fitted["globals"]
    rng = np.random.default_rng(5)
    mu = fit._goal_prior_parts(g, g.phi1(None), [])
    hard = [dict(goal_id="new", model_key="lab/big-2", scaffold="claude-code", instance_key=f"h{i}",
                 check_kind="benchmark", accepted=False, occurred_at=T0 + timedelta(days=60), procedure_id=None,
                 reporter=None, gold_correct=None) for i in range(12)]
    easy = [dict(o, accepted=True, instance_key=f"e{i}", model_key="lab/small") for i, o in enumerate(hard)]
    results = {}
    for name, obs in (("hard", hard), ("easy", easy)):
        local = fit._local_data(g, obs, rng)
        xi_goal, _, _, ess = fit._local_posterior(g, mu, local, CFG, seed=3)
        x = mu + g.arrays["tau"] * xi_goal
        assert x.shape == (CFG.draws, g.k + 2)
        assert ess["median"] > 1.0
        results[name] = x[:, 0].mean()
    assert results["hard"] > results["easy"] + 1.0          # difficulty moved the right way, strongly


def test_simulation_based_calibration_runs_on_the_design(fitted):
    rng = np.random.default_rng(9)
    goals = fitted["goals"][:2]
    obs = _design(rng, goals, {g: 0.0 for g in goals}, instances=20)
    data, _ = fit.build_joint_data(obs, {g: {"embedding": None} for g in goals}, {}, REGISTRY, CFG)
    small = RoutingDefaults(draws=20, nightly_warmup=100, nightly_chains=1, nightly_samples=40, latent_dims=2,
                            embedding_dims=0)
    ranks = fit.simulation_based_calibration(data, small, sims=2, draws=20)
    assert set(ranks) == {"goal0_difficulty", "model0_ability", "goal0_log_sigma_eps"}
    assert all(0 <= r <= 20 for rs in ranks.values() for r in rs)


def test_step_difficulties_are_recovered_and_the_local_refit_returns_them():
    """Runs of a 3-step Procedure (steps share the run's difficulty): the joint fit
    converges and orders the steps' difficulties correctly; the local refit returns
    aligned step draws for every observed step."""
    rng = np.random.default_rng(11)
    goal = str(uuid.UUID(int=77))
    proc = str(uuid.UUID(int=78))
    true_d = {1: -1.5, 2: 0.0, 3: 1.5}
    roles = {1: "plan", 2: "edit", 3: "verify"}
    obs = []
    for run in range(70):
        eps = 0.7 * rng.standard_normal()
        for order, d in true_d.items():
            model = ("lab/small", "lab/big")[run % 2]
            p = 1 / (1 + np.exp(-(THETA[model] - 0.5 - d - eps)))
            obs.append(dict(goal_id=goal, model_key=model, scaffold="claude-code", instance_key=f"run-{run}",
                            check_kind="benchmark", accepted=bool(rng.random() < p),
                            occurred_at=T0 + timedelta(days=run // 10), procedure_id=proc, reporter=None,
                            gold_correct=None, visibility="public", step_order=order, step_role=roles[order]))
    registry = {"lab/small": {"predecessor": None}, "lab/big": {"predecessor": None}}
    data, info = fit.build_joint_data(obs, {goal: {"embedding": None, "visibility": "public"}}, {}, registry, CFG)
    assert data["n_steps"] == 3 and data["n_instances"] == 70
    samples, method, diag = fit.run_joint(data, CFG, seed=2)
    assert method == "nuts" and diag["divergences"] == 0 and diag["max_r_hat"] < 1.05
    orders = [s_[1] for s_ in info["steps"]]
    d_mean = dict(zip(orders, samples["step_d"].mean(axis=0)))
    assert d_mean[1] < d_mean[2] < d_mean[3]
    posts = fit.step_posts(info["steps"], samples["step_d"], samples["step_e"], version=1, method="joint",
                           counts={})
    assert len(posts) == 1 and posts[0]["kind"] == "procedure_steps" and posts[0]["id"] == proc

    g = Globals(version=1, arrays=fit.global_arrays(samples, info), meta={**fit.public_meta(info), "tokens": {}})
    local = fit._local_data(g, obs[:30], np.random.default_rng(0))
    assert [s_[1] for s_ in local["steps"]] == [1, 2, 3]
    mu = fit._goal_prior_parts(g, g.phi1(None), [])
    _, _, steps_out, _ = fit._local_posterior(g, mu, local, CFG, seed=4)
    assert steps_out["d"].shape == (CFG.draws, 3) and steps_out["e"].shape == (CFG.draws, 3, g.k)
