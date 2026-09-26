"""Exact ladder evaluation and choice (docs/model_routing_plan.md §6, §7). numpy only.

Inputs, per posterior draw s and instance-difficulty node n:
    p[s, u, n]     P(unit u's attempt is correct | eps node n, draw s)
    alpha[s], beta[s]   the runtime check's false-accept / false-reject rates
Given eps (and the draw), rungs are conditionally independent; eps is shared, so
integrating over it at the END gives the correct, correlated ladder:

    reach_1 = 1,  reach_{k+1} = reach_k * P(rejected_k | eps)
    P(ok)    = E_eps sum_k reach_k * p_k (1 - beta)          accepted and correct
    P(wrong) = E_eps sum_k reach_k * (1 - p_k) alpha         accepted but wrong
    E[cost]  = E_eps sum_k reach_k * (p_k c_ok + (1 - p_k) c_fail + c_check)
    U        = V P(ok) - L P(wrong) - E[cost]

Earlier attempts on the same instance update the belief exactly: each node weight is
multiplied by the likelihood of what was observed, and each draw is reweighted by its
marginal likelihood. The re-solved ladder is therefore the Bayes-optimal continuation.

Choice: among ladders meeting the reliability chance constraint
    Pr_posterior( P(ok) >= rho ) >= confidence
Thompson sampling picks one draw s* ~ weights and the ladder maximising U under s*;
its propensity is EXACT (the fraction of draw-weight whose argmax is that ladder).
If no ladder meets the constraint, the ladder with the highest (1 - confidence)
quantile of P(ok) is returned, marked meets_target=False -- the most reliable option,
stated as such.

STEP-LEVEL routing (one run.md node at a time): the value of a step is the value of
the WHOLE run, which also needs the remaining steps to succeed. `continuation[s, n]`
is P(every later step succeeds | eps node n, draw s) under a base policy for those
steps, and it multiplies each node's step success before integrating over eps:

    P(ok_run) = E_eps[ P(step ok | eps) * P(rest ok | eps) ]

Because eps is shared, a hard run lowers both factors together -- the product is
correct, the product of their averages would not be. The reliability target and the
utility then refer to the whole run. This is a one-step rollout (Bertsekas): choose
this step optimally assuming the base policy afterwards, re-solve at the next step
with the updated belief. With continuation = 1 it is exactly the task-level ladder.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


@dataclass(frozen=True)
class Attempt:
    """An earlier attempt on THIS instance: unit index into the probability array."""
    unit: int
    accepted: bool
    alpha: np.ndarray        # (S,)
    beta: np.ndarray         # (S,)


@dataclass
class LadderResult:
    ladders: list[tuple[int, ...]]
    p_ok: np.ndarray          # (L, S)
    p_wrong: np.ndarray       # (L, S)
    cost: np.ndarray          # (L, S)
    utility: np.ndarray       # (L, S)
    draw_weights: np.ndarray  # (S,)
    feasible: np.ndarray      # (L,) bool
    chosen: int
    propensity: float
    meets_target: bool
    thompson_draw: Optional[int] = None
    p_step_ok: Optional[np.ndarray] = None   # (L, S): this step alone (step-level only)
    extra: dict = field(default_factory=dict)

    def summary(self, i: int) -> dict:
        w = self.draw_weights
        out = {}
        if self.p_step_ok is not None:
            out["p_step_success"] = float(w @ self.p_step_ok[i])
        return {
            **out,
            "p_success": float(w @ self.p_ok[i]),
            "p_wrong_delivered": float(w @ self.p_wrong[i]),
            "expected_cost_usd": float(w @ self.cost[i]),
            "expected_utility": float(w @ self.utility[i]),
            "p_success_q05": float(weighted_quantile(self.p_ok[i], w, 0.05)),
            "p_success_q95": float(weighted_quantile(self.p_ok[i], w, 0.95)),
        }


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    cw = np.cumsum(weights[order])
    cw = cw / cw[-1]
    return float(values[order][min(np.searchsorted(cw, q), len(values) - 1)])


def enumerate_ladders(n_units: int, max_rungs: int) -> list[tuple[int, ...]]:
    """Every ordered sequence of 1..max_rungs units, repeats allowed (a retry)."""
    out: list[tuple[int, ...]] = []
    for k in range(1, max_rungs + 1):
        out.extend(itertools.product(range(n_units), repeat=k))
    return out


def belief(node_weights: np.ndarray, p: np.ndarray, attempts: Sequence[Attempt]) -> tuple[np.ndarray, np.ndarray]:
    """(per-draw normalised node weights (S, N), draw weights (S,)) after conditioning
    on earlier attempts of this instance."""
    s_count, _, n_count = p.shape
    log_w = np.broadcast_to(np.log(node_weights)[None, :], (s_count, n_count)).copy()
    for att in attempts:
        pu = p[:, att.unit, :]
        a, b = att.alpha[:, None], att.beta[:, None]
        p_acc = pu * (1 - b) + (1 - pu) * a
        log_w += np.log(np.clip(p_acc if att.accepted else 1 - p_acc, 1e-300, None))
    row_max = log_w.max(axis=1, keepdims=True)
    unnorm = np.exp(log_w - row_max)
    per_draw = unnorm.sum(axis=1)
    node_w = unnorm / per_draw[:, None]
    log_draw = np.log(per_draw) + row_max[:, 0]
    draw_w = np.exp(log_draw - log_draw.max())
    return node_w, draw_w / draw_w.sum()


def evaluate(p: np.ndarray, node_w: np.ndarray, alpha: np.ndarray, beta: np.ndarray,
             cost_ok: np.ndarray, cost_fail: np.ndarray, cost_check: float,
             value: float, wrong_penalty: float, ladders: Sequence[tuple[int, ...]],
             continuation: Optional[np.ndarray] = None,
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(p_ok, p_wrong, cost, utility, p_step_ok), each (L, S). p_ok is the RUN's success
    (this step accepted and correct, times the continuation); p_step_ok the step's own.
    Prefixes are shared (depth-first)."""
    cont = np.ones_like(node_w) if continuation is None else continuation
    s_count = p.shape[0]
    acc_ok = p * (1 - beta)[:, None, None]
    acc_wrong = (1 - p) * alpha[:, None, None]
    reject = 1 - acc_ok - acc_wrong
    step_cost = p * cost_ok[None, :, None] + (1 - p) * cost_fail[None, :, None] + cost_check

    index = {lad: i for i, lad in enumerate(ladders)}
    prefixes = {lad[:j] for lad in ladders for j in range(1, len(lad))}
    out_ok = np.empty((len(ladders), s_count))
    out_step = np.empty_like(out_ok)
    out_wrong = np.empty_like(out_ok)
    out_cost = np.empty_like(out_ok)

    def extend(prefix: tuple[int, ...], reach: np.ndarray, ok: np.ndarray, wrong: np.ndarray, cost: np.ndarray):
        for u in range(p.shape[1]):
            lad = prefix + (u,)
            if lad not in index and lad not in prefixes:
                continue
            ok2 = ok + reach * acc_ok[:, u, :]
            wrong2 = wrong + reach * acc_wrong[:, u, :]
            cost2 = cost + reach * step_cost[:, u, :]
            if lad in index:
                i = index[lad]
                out_ok[i] = (ok2 * cont * node_w).sum(axis=1)
                out_step[i] = (ok2 * node_w).sum(axis=1)
                out_wrong[i] = (wrong2 * node_w).sum(axis=1)
                out_cost[i] = (cost2 * node_w).sum(axis=1)
            if lad in prefixes:
                extend(lad, reach * reject[:, u, :], ok2, wrong2, cost2)

    zeros = np.zeros_like(node_w)
    extend((), np.ones_like(node_w), zeros, zeros, zeros)
    utility = value * out_ok - wrong_penalty * out_wrong - out_cost
    return out_ok, out_wrong, out_cost, utility, out_step


def ladder_node_ok(p: np.ndarray, alpha: np.ndarray, beta: np.ndarray, lad: Sequence[int]) -> np.ndarray:
    """(S, N): P(the ladder ends accepted AND correct | eps node) -- NOT integrated, so
    it can be multiplied with other steps' factors at the same eps."""
    reach = np.ones((p.shape[0], p.shape[2]))
    ok = np.zeros_like(reach)
    for u in lad:
        pu = p[:, u, :]
        acc_ok = pu * (1 - beta)[:, None]
        acc_wrong = (1 - pu) * alpha[:, None]
        ok += reach * acc_ok
        reach = reach * (1 - acc_ok - acc_wrong)
    return ok


def choose(p: np.ndarray, node_weights: np.ndarray, alpha: np.ndarray, beta: np.ndarray,
           cost_ok: np.ndarray, cost_fail: np.ndarray, *, cost_check: float, value: float,
           wrong_penalty: float, candidates: Sequence[int], max_rungs: int, rho: float,
           confidence: float, attempts: Sequence[Attempt] = (), max_cost: Optional[float] = None,
           rng: np.random.Generator, continuation: Optional[np.ndarray] = None,
           node_weights_after: Optional[tuple[np.ndarray, np.ndarray]] = None) -> LadderResult:
    """Evaluate every ladder over `candidates` (indices into p's unit axis; earlier
    attempts may use other units) and choose one."""
    node_w, draw_w = node_weights_after if node_weights_after is not None else belief(node_weights, p, attempts)
    cand = list(candidates)
    local = enumerate_ladders(len(cand), max_rungs)
    ladders = [tuple(cand[i] for i in lad) for lad in local]
    ok, wrong, cost, util, step_ok = evaluate(p, node_w, alpha, beta, cost_ok, cost_fail, cost_check,
                                              value, wrong_penalty, ladders, continuation)
    step_ok = None if continuation is None else step_ok
    mass = (draw_w[None, :] * (ok >= rho)).sum(axis=1)
    feasible = mass >= confidence - 1e-12
    if max_cost is not None:
        # hard cap on the WORST case: every rung runs and fails
        worst = np.array([sum(float(cost_fail[u]) + cost_check for u in lad) for lad in ladders])
        feasible &= worst <= max_cost
        affordable = worst <= max_cost
    else:
        affordable = np.ones(len(ladders), dtype=bool)

    if feasible.any():
        masked = np.where(feasible[:, None], util, -np.inf)
        argmax_per_draw = masked.argmax(axis=0)                            # (S,)
        s_star = int(rng.choice(len(draw_w), p=draw_w))
        chosen = int(argmax_per_draw[s_star])
        propensity = float(draw_w[argmax_per_draw == chosen].sum())
        return LadderResult(ladders, ok, wrong, cost, util, draw_w, feasible, chosen, propensity, True, s_star,
                            p_step_ok=step_ok)

    if not affordable.any():
        raise ValueError("no ladder fits max_cost_usd, even a single attempt")
    lower = np.array([weighted_quantile(ok[i], draw_w, 1 - confidence) if affordable[i] else -np.inf
                      for i in range(len(ladders))])
    best = np.flatnonzero(lower == lower.max())
    chosen = int(best[np.argmax((util[best] * draw_w).sum(axis=1))])        # tie: higher expected utility
    return LadderResult(ladders, ok, wrong, cost, util, draw_w, feasible, chosen, 1.0, False, p_step_ok=step_ok)
