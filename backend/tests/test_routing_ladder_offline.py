"""Model recommender: the decision maths (app/routing/ladder.py, costs.py, predict.py).

The exact ladder values are checked against a brute-force simulation of the same
generative process (shared instance difficulty, noisy check), so the correlated-
failure maths is verified, not just re-derived."""
from __future__ import annotations

import numpy as np
import pytest

from app.routing import costs, ladder
from app.routing.quadrature import standard_normal_rule


def _p(logits, sig_eps, nodes):
    ex, ew = standard_normal_rule(nodes)
    lg = np.asarray(logits, dtype=float)                                    # (S, U)
    arg = lg[:, :, None] - np.asarray(sig_eps)[:, None, None] * ex[None, None, :]
    return 1 / (1 + np.exp(-arg)), ew


def _simulate(logit, sig, alpha, beta, lad, c_ok, c_fail, c_check, n, rng):
    eps = sig * rng.standard_normal(n)
    done = np.zeros(n, dtype=bool)
    ok = np.zeros(n)
    wrong = np.zeros(n)
    cost = np.zeros(n)
    for u in lad:
        live = ~done
        correct = rng.random(n) < 1 / (1 + np.exp(-(logit[u] - eps)))
        accept = np.where(correct, rng.random(n) < 1 - beta, rng.random(n) < alpha)
        cost += live * (np.where(correct, c_ok[u], c_fail[u]) + c_check)
        ok += live * (accept & correct)
        wrong += live * (accept & ~correct)
        done |= live & accept
    return ok.mean(), wrong.mean(), cost.mean()


def test_ladder_values_match_brute_force_simulation():
    rng = np.random.default_rng(0)
    logits = np.array([[0.4, 2.2], [-0.3, 1.5]])      # 2 draws x 2 units (cheap, strong)
    sig = np.array([1.2, 0.6])
    alpha, beta = np.array([0.05, 0.10]), np.array([0.08, 0.02])
    c_ok, c_fail = np.array([0.02, 0.60]), np.array([0.05, 0.90])
    p, ew = _p(logits, sig, 40)
    lads = [(0,), (1,), (0, 1), (0, 0, 1)]
    ok, wrong, cost, util = ladder.evaluate(p, np.tile(ew, (2, 1)), alpha, beta, c_ok, c_fail, 0.01, 3.0, 3.0, lads)
    for s in range(2):
        for i, lad in enumerate(lads):
            sim = _simulate(logits[s], sig[s], alpha[s], beta[s], lad, c_ok, c_fail, 0.01, 400_000, rng)
            assert ok[i, s] == pytest.approx(sim[0], abs=4e-3)
            assert wrong[i, s] == pytest.approx(sim[1], abs=3e-3)
            assert cost[i, s] == pytest.approx(sim[2], rel=1e-2)
            assert util[i, s] == pytest.approx(3.0 * ok[i, s] - 3.0 * wrong[i, s] - cost[i, s])


def test_a_failure_makes_the_next_rung_less_likely_than_independence_says():
    """The shared instance difficulty: P(strong ok | cheap failed) < P(strong ok)."""
    p, ew = _p([[0.0, 2.0]], [1.5], 40)
    node_w = ew[None, :]
    p_strong = float((p[0, 1] * ew).sum())
    zero = np.zeros(1)
    after, _ = ladder.belief(ew, p, [ladder.Attempt(0, False, zero, zero)])
    p_strong_given_fail = float((p[0, 1] * after[0]).sum())
    assert p_strong_given_fail < p_strong - 0.05
    # ... and the ladder's P(ok) is exactly p0 + (1 - p0) * P(strong | cheap failed)
    ok, *_ = ladder.evaluate(p, node_w, zero, zero, np.zeros(2), np.zeros(2), 0.0, 1.0, 1.0, [(0, 1)])
    p0 = float((p[0, 0] * ew).sum())
    assert ok[0, 0] == pytest.approx(p0 + (1 - p0) * p_strong_given_fail, abs=1e-9)
    independent = p0 + (1 - p0) * p_strong
    assert ok[0, 0] < independent


def test_choice_is_thompson_with_exact_propensity_and_honours_the_chance_constraint():
    rng = np.random.default_rng(1)
    draws = 200
    logits = np.column_stack([rng.normal(1.0, 0.8, draws), rng.normal(3.0, 0.3, draws)])
    p, ew = _p(logits, np.full(draws, 0.8), 20)
    zero = np.zeros(draws)
    res = ladder.choose(p, ew, zero, zero, np.array([0.02, 0.6]), np.array([0.02, 0.6]), cost_check=0.0,
                        value=3.0, wrong_penalty=3.0, candidates=[0, 1], max_rungs=2, rho=0.9, confidence=0.9, rng=rng)
    assert res.meets_target and res.feasible[res.chosen]
    # the propensity is the posterior mass whose argmax is the chosen ladder
    masked = np.where(res.feasible[:, None], res.utility, -np.inf)
    per_draw = masked.argmax(axis=0)
    assert res.propensity == pytest.approx(res.draw_weights[per_draw == res.chosen].sum())
    # every feasible ladder meets the target with >= 90% posterior mass
    for i in np.flatnonzero(res.feasible):
        assert res.draw_weights @ (res.p_ok[i] >= 0.9) >= 0.9 - 1e-12
    # a cheap-first ladder wins here: same reliability, far lower expected cost
    assert res.ladders[res.chosen][0] == 0


def test_when_nothing_meets_the_target_the_most_reliable_ladder_is_returned_and_flagged():
    rng = np.random.default_rng(2)
    p, ew = _p(np.array([[-2.0, -1.0]] * 50), np.full(50, 0.5), 20)
    zero = np.zeros(50)
    res = ladder.choose(p, ew, zero, zero, np.ones(2), np.ones(2), cost_check=0.0, value=10.0, wrong_penalty=10.0,
                        candidates=[0, 1], max_rungs=2, rho=0.95, confidence=0.9, rng=rng)
    assert not res.meets_target and res.propensity == 1.0
    lower = [ladder.weighted_quantile(res.p_ok[i], res.draw_weights, 0.1) for i in range(len(res.ladders))]
    assert lower[res.chosen] == max(lower)


def test_max_cost_is_a_hard_cap_on_the_worst_case():
    rng = np.random.default_rng(3)
    p, ew = _p(np.array([[1.0, 3.0]] * 20), np.full(20, 0.5), 10)
    zero = np.zeros(20)
    res = ladder.choose(p, ew, zero, zero, np.array([0.1, 1.0]), np.array([0.1, 1.0]), cost_check=0.0, value=5.0,
                        wrong_penalty=5.0, candidates=[0, 1], max_rungs=3, rho=0.5, confidence=0.5,
                        max_cost=0.35, rng=rng)
    assert all(u == 0 for u in res.ladders[res.chosen])
    with pytest.raises(ValueError):
        ladder.choose(p, ew, zero, zero, np.array([0.1, 1.0]), np.array([0.1, 1.0]), cost_check=0.0, value=5.0,
                      wrong_penalty=5.0, candidates=[0, 1], max_rungs=1, rho=0.5, confidence=0.5, max_cost=0.01, rng=rng)


def test_token_costs_pool_toward_data_and_price_live():
    none = costs.expected_tokens("1", {}, None, None)
    assert none[0] > 0 and none[2] == 0                          # the prior, no cache hits assumed
    rich = {"1": [1000.0, np.log(2000.0), np.log(100.0), np.log(1 + 500.0)]}
    pooled = costs.expected_tokens("1", {}, rich, None)
    assert pooled[0] < none[0] and pooled[2] > 0                 # lots of unit data dominates the prior
    price = costs.Price(3.0, 15.0, 0.3)
    assert costs.dollars(price, 1e6, 1e6, 0) == pytest.approx(18.0)
    assert costs.dollars(price, 1e6, 0, 1e6) == pytest.approx(0.3)


def test_ladder_enumeration_allows_retries():
    lads = ladder.enumerate_ladders(2, 3)
    assert len(lads) == 2 + 4 + 8 and (0, 0, 0) in lads and (1, 0) in lads
