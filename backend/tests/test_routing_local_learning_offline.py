"""The workspace's own record moves a unit's level (shift_to_local_rates), and untried units are sized from it."""
import numpy as np

from app.routing.service import _explore_unit, _pool_unit_stats, shift_to_local_rates


def _accept(p, eps_w, draw_w, alpha, beta):
    per = ((p * (1 - beta)[:, None] + (1 - p) * alpha[:, None]) * eps_w[None, :]).sum(axis=1)
    return float(draw_w @ per)


def test_local_record_outweighs_a_pessimistic_prior_after_a_few_attempts():
    S, U, N = 40, 2, 5
    p = np.full((S, U, N), 0.3)                          # the prior says unit 0 passes 30%
    eps_w, draw_w = np.full(N, 1 / N), np.full(S, 1 / S)
    alpha, beta = np.full(S, 0.1), np.full(S, 0.05)
    out = shift_to_local_rates(p, eps_w, draw_w, alpha, beta, [(0, 20, 18)], strength=4.0)   # here: 18 of 20 accepted
    got = _accept(out[:, 0, :], eps_w, draw_w, alpha, beta)
    prior = _accept(p[:, 0, :], eps_w, draw_w, alpha, beta)
    assert abs(got - (prior * 4 + 18) / 24) < 2e-3
    assert np.allclose(out[:, 1, :], 0.3), "units without a local record are untouched"


def test_untried_units_are_sized_optimistically_and_exploration_targets_the_least_tried_cheaper_unit():
    stats = {"a|x": {"1": [5, 9.0, 6.0, 0.0]}, "b|x": {"1": [3, 8.0, 7.0, 0.0], "0": [1, 10.0, 8.0, 0.0]}}
    pooled = _pool_unit_stats(stats)
    assert pooled["1"] == [8.0, 8.0, 6.0, 0.0] and pooled["0"][0] == 1.0
    usable = [("cheap", "x"), ("mid", "x"), ("own", "x")]
    rng = np.random.default_rng(0)
    cost = np.array([0.1, 0.5, 1.0])
    pick = _explore_unit(usable, [{"unit": "cheap|x", "n": 3}, {"unit": "mid|x", "n": 1}], "own|x", cost, rng, 3)
    assert pick == 1, "the least-tried cheaper unit"
    assert _explore_unit(usable, [{"unit": "cheap|x", "n": 3}, {"unit": "mid|x", "n": 3}], "own|x", cost, rng, 3) is None
    assert _explore_unit(usable, [], None, cost, rng, 3) is None, "no session model to fall back to: no exploration"
