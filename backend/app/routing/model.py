"""The success model (docs/model_routing_plan.md §3, §5) as NumPyro code. Worker only.

For attempt r on instance i of Goal g, by unit u = (model m, scaffold s), following
Procedure pi (optional):

    logit P(correct_r) = theta[m, week_r] + gamma[s] + delta[m, s]
                       - b[g] + <a[g], z[m]> + <c[pi], z[m]>
                       - eps[i]

    eps[i] ~ N(0, sigma_eps[g]^2)    shared by every attempt on the instance: failures
                                     of consecutive rungs are correlated, by construction.
A STEP attempt (one run.md node of Procedure pi, step k) adds the step's own terms:

    ... - d[pi, k] + <e[pi, k], z[m]>        d ~ N(mu_role[role], tau_d^2), e ~ N(0, tau_e^2 I)

so a step can be easier or harder than the whole task and need different skills; a
never-seen step is its role's prior. Steps of one run share the run's eps: failing
step 2 is evidence the run is hard, which the next step's recommendation uses.

The Bernoulli-logit draw itself is the attempt-level randomness (a retry of the same
unit is a fresh draw given eps). An extra per-attempt Gaussian term would not be
identifiable from binary outcomes -- it only rescales the logit -- so there is none.
sigma_eps is identified by instances attempted more than once (ladders, retries, and
public results where many models attempt the same benchmark instance).

Goal block x[g] = (b[g], log sigma_eps[g], a[g] in R^K), non-centered and pooled
through the Goal DAG with an embedding baseline:

    x[g] = W [1, phi(g)] + mean_{p in parents(g)} (x[p] - W [1, phi(p)]) + tau * xi[g]

A Goal without parents has an empty sum -- the same formula, not a special case. Each
observation enters the likelihood once, so DAG diamonds cannot double count.

Model side (docs/plan_2026-10_priors_library_survey.md §2): a model with a version predecessor
starts from it; one without starts from its CARD (cards.py) instead of a pure guess,

    theta[m, 0] = beta . x_m + f_theta[family] + sigma_theta * xi,   z[m] = B x_m + f_z[family] + xi_z

with beta, B and the pooled family effects learned across every model with public results.
Public benchmark items published before a model's training cutoff add kappa >= 0 to its logit
(contamination; learned, never applied to live tasks), and leaderboards that publish only a
percentage enter as a binomial likelihood over the benchmark's Goal node (aggregate_loglik).

Identification:
  * mean model ability is fixed at 0 (location of theta vs b);
  * the skill matrix z is lower triangular with a positive diagonal in its first K rows
    (sign / rotation of the (a, z) factorisation);
  * check error rates are pinned by gold-labelled audits ('benchmark' checks define
    correctness: alpha = beta = 0 exactly).

A check observation on an attempt contributes P(accepted | correct) = 1 - beta_k,
P(accepted | wrong) = alpha_k; a host-reported observation additionally carries the
reporter's false-accept offset rho[reporter] (Dawid-Skene style). eps is integrated
out by Gauss-Hermite quadrature: no per-instance latent variables.
"""
from __future__ import annotations

from typing import Any, Mapping

import jax
import jax.numpy as jnp
import numpy as np
import numpyro
import numpyro.distributions as dist
from jax.scipy.special import expit, logit, logsumexp

from app.routing.config import CHECK_KINDS, CHECK_PRIORS, STEP_ROLES
from app.routing.quadrature import standard_normal_rule

from app.routing.model_constants import effective_dims  # noqa: E402,F401
from app.routing.model_constants import SIGMA_NEW_VERSION, SUCCESSOR_Z_SD, TAU_DELTA, TAU_GAMMA  # noqa: E402
BENCHMARK = CHECK_KINDS.index("benchmark")


def _halfnormal(name: str, scale: float, shape: tuple = ()) -> Any:
    return numpyro.sample(name, dist.HalfNormal(scale * jnp.ones(shape)).to_event(len(shape)) if shape
                          else dist.HalfNormal(scale))


def _std_normal(name: str, shape: tuple) -> Any:
    return numpyro.sample(name, dist.Normal(jnp.zeros(shape), 1.0).to_event(len(shape)))


def skill_matrix(z_raw: Any, z_diag: Any, n_models: int, k: int) -> Any:
    """Lower-triangular-with-positive-diagonal identification of the first K rows."""
    kk = min(n_models, k)
    rows = jnp.arange(n_models)[:, None]
    cols = jnp.arange(k)[None, :]
    z = jnp.where(cols > rows, 0.0, z_raw)                       # upper triangle of first rows = 0
    diag = jnp.zeros((n_models, k)).at[jnp.arange(kk), jnp.arange(kk)].set(z_diag)
    return jnp.where((rows == cols) & (rows < kk), diag, z)


def goal_blocks(xi: Any, w: Any, tau: Any, phi1: Any, parents: Any, parent_mask: Any,
                levels: list[np.ndarray]) -> Any:
    """x (G, D) from non-centered xi, level by level (parents before children)."""
    base = phi1 @ w.T                                            # (G, D): W [1, phi(g)]
    x = jnp.zeros_like(base)
    for level in levels:                                         # static python loop over DAG depth
        idx = jnp.asarray(level)
        par = parents[idx]                                       # (n, maxP) indices into G, -1 pad
        mask = parent_mask[idx]                                  # (n, maxP)
        resid = x[jnp.clip(par, 0)] - base[jnp.clip(par, 0)]    # (n, maxP, D)
        count = mask.sum(axis=1, keepdims=True)
        mean_resid = jnp.where(count > 0, (resid * mask[..., None]).sum(axis=1) / jnp.maximum(count, 1), 0.0)
        x = x.at[idx].set(base[idx] + mean_resid + tau * xi[idx])
    return x


def attempt_loglik(logit_r: Any, sig_eps_r: Any, alpha_r: Any, beta_r: Any,
                   accepted: Any, gold: Any, instance: Any, n_instances: int, eps_nodes: int) -> Any:
    """Sum over instances of log E_eps[ prod_r L_r(eps) ]."""
    ex, ew = standard_normal_rule(eps_nodes)
    arg = logit_r[:, None] - sig_eps_r[:, None] * jnp.asarray(ex)[None, :]      # (R, N)
    log_p, log_q = jax.nn.log_sigmoid(arg), jax.nn.log_sigmoid(-arg)           # log P(correct), log P(wrong)
    acc = accepted[:, None]
    # log P(observation | correct / wrong); exact zeros (the 'benchmark' check, gold labels)
    # become -inf through _safe_log, never log(0) of a traced value
    log_obs_c = jnp.where(acc, _safe_log(1.0 - beta_r)[:, None], _safe_log(beta_r)[:, None])
    log_obs_w = jnp.where(acc, _safe_log(alpha_r)[:, None], _safe_log(1.0 - alpha_r)[:, None])
    neg_inf = -jnp.inf
    log_obs_c = jnp.where(gold[:, None] == 0, neg_inf, log_obs_c)             # gold: -1 unknown, 0 wrong, 1 correct
    log_obs_w = jnp.where(gold[:, None] == 1, neg_inf, log_obs_w)
    loglik = jnp.logaddexp(log_p + log_obs_c, log_q + log_obs_w)
    per_instance = jax.ops.segment_sum(loglik, instance, num_segments=n_instances)   # (I, N)
    return logsumexp(per_instance + jnp.log(jnp.asarray(ew))[None, :], axis=1).sum()


def _safe_log(x: Any) -> Any:
    """log(x) with log(0) = -inf and a finite gradient everywhere (the double-where
    trick: the log never sees a zero, so no NaN flows back through jnp.where)."""
    positive = x > 0
    return jnp.where(positive, jnp.log(jnp.where(positive, x, 1.0)), -jnp.inf)


def check_rates() -> tuple[Any, Any]:
    """alpha, beta per CHECK_KINDS index; 'benchmark' fixed at 0."""
    alphas, betas = [], []
    for kind in CHECK_KINDS:
        if kind == "benchmark":
            alphas.append(jnp.array(0.0))
            betas.append(jnp.array(0.0))
            continue
        (a1, b1), (a2, b2) = CHECK_PRIORS[kind]
        alphas.append(numpyro.sample(f"alpha_{kind}", dist.Beta(a1, b1)))
        betas.append(numpyro.sample(f"beta_{kind}", dist.Beta(a2, b2)))
    return jnp.stack(alphas), jnp.stack(betas)


def reporter_alpha(alpha_k: Any, check: Any, reporter: Any, rho: Any) -> Any:
    """Per-attempt false-accept rate: the check's, shifted on the logit scale by the
    reporter's reliability offset for host-reported attempts. Never for 'benchmark'
    (host reports cannot carry that kind -- store.py enforces it)."""
    base = alpha_k[check]
    shifted = expit(logit(jnp.clip(base, 1e-6, 1 - 1e-6)) + rho[jnp.clip(reporter, 0)])
    return jnp.where((reporter >= 0) & (check != BENCHMARK), shifted, base)


def card_prior(data: Mapping[str, Any], m_count: int, k: int) -> tuple[Any, Any]:
    """(mu_theta (M,), mu_z (M, K)): the model-card regression (cards.py) that a model without a
    predecessor starts from. Zeros -- the population prior -- when the data carries no cards."""
    mx = data.get("model_x")
    if mx is None or np.asarray(mx).shape[1] == 0:
        return jnp.zeros(m_count), jnp.zeros((m_count, k))
    x = jnp.asarray(mx)
    c_dim = x.shape[1]
    n_fam = max(int(data.get("n_families", 0)), 1)
    fam = jnp.asarray(data["model_family"])
    # one shared scale: with few models and ~16 covariates the coefficients shrink together toward 0 (the old
    # population prior) unless the data hold them up -- noise covariates cannot each take +-0.5 for free
    tau_beta = _halfnormal("tau_card_beta", 0.5)
    beta = numpyro.deterministic("card_beta", tau_beta * _std_normal("xi_card_beta", (c_dim,)))
    tau_ft = _halfnormal("tau_fam_theta", 0.5)
    # centred: the families' common level is the population location (already shared by abilities and Goal
    # difficulties), so leaving it free gives the sampler a flat ridge to wander along
    xi_ft = _std_normal("xi_fam_theta", (n_fam,))
    fam_theta = tau_ft * (xi_ft - xi_ft.mean())
    numpyro.deterministic("fam_theta", fam_theta)
    mu_theta = x @ beta + jnp.where(fam >= 0, fam_theta[jnp.clip(fam, 0)], 0.0)
    if k == 0:
        return mu_theta, jnp.zeros((m_count, 0))
    b_mat = numpyro.sample("card_B", dist.Normal(jnp.zeros((k, c_dim)), 0.3).to_event(2))
    tau_fz = _halfnormal("tau_fam_z", 0.3)
    xi_fz = _std_normal("xi_fam_z", (n_fam, k))
    fam_z = tau_fz * (xi_fz - xi_fz.mean(axis=0, keepdims=True))
    numpyro.deterministic("fam_z", fam_z)
    mu_z = x @ b_mat.T + jnp.where((fam >= 0)[:, None], fam_z[jnp.clip(fam, 0)], 0.0)
    return mu_theta, mu_z


def contamination(data: Mapping[str, Any]) -> Any:
    """kappa >= 0: the logit bonus a model gets on a public benchmark item published before its
    training cutoff (memorisation). Learned, and never applied at decision time (live tasks are new)."""
    if "att_contam" not in data and "agg_contam" not in data:
        return 0.0
    return _halfnormal("kappa", 0.5)


def aggregate_loglik(logit_a: Any, sig_eps_a: Any, n: Any, k_ok: Any, eps_nodes: int) -> Any:
    """Binomial log-likelihood of aggregate-only public results (a leaderboard %: k of n items
    resolved), with the per-item difficulty integrated out by Gauss-Hermite quadrature:
    P = E_eps[sigmoid(logit - sigma_eps * eps)]. The binomial coefficient is a constant and omitted.
    Items are treated as exchangeable draws from the benchmark node -- their shared identity across
    models is lost in an aggregate, which only makes this evidence weaker, never biased."""
    ex, ew = standard_normal_rule(eps_nodes)
    arg = logit_a[:, None] - sig_eps_a[:, None] * jnp.asarray(ex)[None, :]
    log_p = logsumexp(jax.nn.log_sigmoid(arg) + jnp.log(jnp.asarray(ew))[None, :], axis=1)
    log_q = logsumexp(jax.nn.log_sigmoid(-arg) + jnp.log(jnp.asarray(ew))[None, :], axis=1)
    return (k_ok * log_p + (n - k_ok) * log_q).sum()


def joint_model(data: Mapping[str, Any], k: int, eps_nodes: int) -> None:
    """The full model over every Goal / Procedure / model / scaffold in `data`
    (built by fit.build_joint_data)."""
    m_count, s_count, g_count = data["n_models"], data["n_scaffolds"], data["n_goals"]
    p_count, r_count, weeks = data["n_procedures"], data["n_reporters"], data["n_weeks"]
    d = k + 2
    p_dim = data["phi1"].shape[1]

    # --- models: ability with version inheritance and weekly drift
    sigma_theta = _halfnormal("sigma_theta", 1.5)
    sigma_drift = _halfnormal("sigma_drift", 0.1)
    xi_theta = _std_normal("xi_theta", (m_count,))
    zeta = _std_normal("zeta", (m_count, weeks))
    mu_theta, mu_z = card_prior(data, m_count, k)
    theta_hist = jnp.zeros((m_count, weeks))
    pred, first = data["model_pred"], data["model_first_week"]
    week_idx = np.arange(weeks)
    for m in range(m_count):                     # predecessors are ordered first (fit.py)
        start = (theta_hist[pred[m], first[m]] + SIGMA_NEW_VERSION * xi_theta[m]) if pred[m] >= 0 \
            else mu_theta[m] + sigma_theta * xi_theta[m]
        steps = jnp.where(week_idx > first[m], zeta[m], 0.0)
        theta_hist = theta_hist.at[m].set(start + sigma_drift * jnp.cumsum(steps))
    numpyro.deterministic("theta_hist", theta_hist)

    if k > 0:
        z_raw = _std_normal("z_raw", (m_count, k))
        no_pred = jnp.asarray(np.asarray(pred) < 0)[:, None]
        z = skill_matrix(jnp.where(no_pred, mu_z + z_raw, z_raw),
                         _halfnormal("z_diag", 1.0, (min(m_count, k),)), m_count, k)
    else:
        z_raw = z = jnp.zeros((m_count, 0))
    for m in range(m_count):                     # a successor's skills start near its predecessor's
        if pred[m] >= 0 and m >= k:
            z = z.at[m].set(z[pred[m]] + SUCCESSOR_Z_SD * z_raw[m])
    numpyro.deterministic("z", z)

    gamma = TAU_GAMMA * _std_normal("xi_gamma", (s_count,))
    delta = TAU_DELTA * _std_normal("xi_delta", (m_count, s_count))
    numpyro.deterministic("gamma", gamma)
    numpyro.deterministic("delta", delta)

    # --- goals: embedding baseline + DAG residual pooling, non-centered
    w0 = numpyro.sample("w_intercept", dist.Normal(jnp.concatenate(
        [jnp.array([0.0, 0.0]), jnp.zeros(k)]), jnp.concatenate([jnp.array([2.0, 0.5]), 0.5 * jnp.ones(k)])).to_event(1))
    if p_dim > 1:
        w_slope = numpyro.sample("w_slope", dist.Normal(jnp.zeros((d, p_dim - 1)), 0.5).to_event(2))
        w = jnp.concatenate([w0[:, None], w_slope], axis=1)
    else:                                                        # no embeddings: intercepts only
        w = w0[:, None]
    numpyro.deterministic("w", w)
    tau = jnp.concatenate([_halfnormal("tau_b", 1.0)[None], _halfnormal("tau_s", 0.3)[None]]
                          + ([_halfnormal("tau_a", 0.5, (k,))] if k > 0 else []))
    numpyro.deterministic("tau", tau)
    xi_goal = _std_normal("xi_goal", (g_count, d))
    x = goal_blocks(xi_goal, w, tau, jnp.asarray(data["phi1"]), jnp.asarray(data["parents"]),
                    jnp.asarray(data["parent_mask"]), data["levels"])
    numpyro.deterministic("goal_x", x)

    tau_c = _halfnormal("tau_c", 0.5)
    c = tau_c * (_std_normal("xi_proc", (max(p_count, 1), k)) if k > 0 else jnp.zeros((max(p_count, 1), 0)))
    numpyro.deterministic("proc_c", c)

    # --- step terms (always sampled: their priors serve never-seen steps at decision time)
    n_steps = data.get("n_steps", 0)
    mu_role = numpyro.sample("mu_role", dist.Normal(jnp.zeros(len(STEP_ROLES)), 1.0).to_event(1))
    tau_d = _halfnormal("tau_d", 0.5)
    tau_e = _halfnormal("tau_e", 0.3)
    step_role = jnp.asarray(data.get("step_role", np.zeros(max(n_steps, 1), dtype=np.int32)))
    step_d = mu_role[step_role] + tau_d * _std_normal("xi_d", (max(n_steps, 1),))
    step_e = tau_e * (_std_normal("xi_e", (max(n_steps, 1), k)) if k > 0 else jnp.zeros((max(n_steps, 1), 0)))
    numpyro.deterministic("step_d", step_d)
    numpyro.deterministic("step_e", step_e)

    alpha_k, beta_k = check_rates()
    tau_rho = _halfnormal("tau_rho", 0.5)
    rho = tau_rho * _std_normal("xi_rho", (max(r_count, 1),))
    kappa = contamination(data)

    if data.get("n_aggregates", 0):
        ma, sa, ga = (jnp.asarray(data["agg_model"]), jnp.asarray(data["agg_scaffold"]),
                      jnp.asarray(data["agg_goal"]))
        logit_a = (theta_hist[ma, jnp.asarray(data["agg_week"])] + gamma[sa] + delta[ma, sa] - x[ga, 0]
                   + (x[ga, 2:] * z[ma]).sum(axis=1) + kappa * jnp.asarray(data["agg_contam"]))
        numpyro.factor("aggregates", aggregate_loglik(
            logit_a, jnp.exp(x[ga, 1]), jnp.asarray(data["agg_n"]), jnp.asarray(data["agg_k"]), eps_nodes))

    if data["n_attempts"] == 0:
        return
    mi, si, gi = data["att_model"], data["att_scaffold"], data["att_goal"]
    pi_, wk = data["att_proc"], data["att_week"]
    zm = z[mi]
    proc_term = jnp.where((pi_ >= 0)[:, None], c[jnp.clip(pi_, 0)], 0.0)
    att_step = jnp.asarray(data.get("att_step", -np.ones(data["n_attempts"], dtype=np.int32)))
    has_step = att_step >= 0
    step_term = jnp.where(has_step, -step_d[jnp.clip(att_step, 0)]
                          + (step_e[jnp.clip(att_step, 0)] * zm).sum(axis=1), 0.0)
    logit_r = (theta_hist[mi, wk] + gamma[si] + delta[mi, si] - x[gi, 0]
               + (x[gi, 2:] * zm).sum(axis=1) + (proc_term * zm).sum(axis=1) + step_term)
    if "att_contam" in data:
        logit_r = logit_r + kappa * jnp.asarray(data["att_contam"])
    sig_eps_r = jnp.exp(x[gi, 1])
    check, reporter = data["att_check"], data["att_reporter"]
    alpha_r = reporter_alpha(alpha_k, check, reporter, rho)
    numpyro.factor("likelihood", attempt_loglik(
        logit_r, sig_eps_r, alpha_r, beta_k[check], jnp.asarray(data["att_accepted"]),
        jnp.asarray(data["att_gold"]), jnp.asarray(data["att_instance"]), data["n_instances"], eps_nodes))
