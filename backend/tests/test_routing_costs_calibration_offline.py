"""Token levels calibrated from published per-task costs, cache-aware list prices, measured check error rates."""
from __future__ import annotations

import math

from app.routing import costs, token_calibration
from app.routing.cards import ModelCard
from app.routing.config import CHECK_PRIORS, DEFAULTS
from app.routing.service import _unit_tokens

CHEAP = ModelCard("cheap-1", price_in=1.0, price_out=5.0, price_cached=0.1)
DEAR = ModelCard("dear-1", price_in=10.0, price_out=50.0, price_cached=1.0)
CARDS = {"cheap-1": CHEAP, "dear-1": DEAR}


def _obs(model, cost, ok, n):
    return [{"model_key": model, "cost_usd": cost, "accepted": ok} for _ in range(n)]


def test_costs_calibrate_token_levels_and_a_per_model_factor():
    # both models use the same tokens per task, so the dearer one costs 10x: one shared level, factors near 1
    out_per_in, share = token_calibration.OUT_PER_IN, token_calibration.CACHED_SHARE
    t_in = 400_000
    per_tok = lambda c: ((1 - share) * c.price_in + share * c.price_cached + out_per_in * c.price_out) / 1e6
    obs = _obs("cheap-1", t_in * per_tok(CHEAP), True, 40) + _obs("dear-1", t_in * per_tok(DEAR), True, 40)
    tm = token_calibration.calibrate(obs, CARDS.get)
    n, log_in, log_out, log_cached = tm["global"]["1"]
    t_in_est, *_ = costs.expected_tokens("1", tm["global"], None, None, DEFAULTS)
    assert n == 80 and abs(t_in_est / t_in - 1) < 0.15                       # pooled with the prior, still close
    assert abs(math.exp(log_cached) - 1 - share * t_in) / (share * t_in) < 0.01
    for model in ("cheap-1", "dear-1"):
        assert abs(tm["models"][model]["1"][1] - tm["global"]["1"][1]) < 1e-6


def test_a_model_that_uses_more_tokens_gets_a_larger_factor():
    obs = _obs("cheap-1", 0.10, True, 40) + _obs("dear-1", 3.0, True, 40)       # dear-1 costs 30x, prices are 10x
    tm = token_calibration.calibrate(obs, CARDS.get)
    assert tm["models"]["dear-1"]["1"][1] > tm["models"]["cheap-1"]["1"][1]


def test_too_few_rows_or_no_price_gives_nothing():
    assert token_calibration.calibrate(_obs("cheap-1", 0.1, True, 5), CARDS.get)["global"] == {}
    assert token_calibration.calibrate(_obs("unknown", 0.1, True, 50), CARDS.get)["global"] == {}


def test_unit_tokens_fall_back_to_the_models_calibrated_level():
    meta = {"units": {"a|x": {"1": [1, 1, 1, 0]}}, "models": {"cheap-1": {"1": [2, 2, 2, 0]}}}
    assert _unit_tokens(meta, "a", "a|x", CARDS) == {"1": [1, 1, 1, 0]}
    assert _unit_tokens(meta, "cheap-1", "cheap-1|claude-code", CARDS) == {"1": [2, 2, 2, 0]}
    assert _unit_tokens(meta, "nobody", "nobody|y", CARDS) is None


def test_cache_reads_are_priced_at_the_cache_rate():
    p = costs.Price(CHEAP.price_in, CHEAP.price_out, CHEAP.price_cached)
    assert math.isclose(costs.dollars(p, 1_000_000, 0, 900_000), (100_000 * 1.0 + 900_000 * 0.1) / 1e6)


def test_tests_check_prior_matches_what_we_measured():
    (a1, b1), (a2, b2) = CHECK_PRIORS["tests"]
    assert abs(a1 / (a1 + b1) - 0.20) < 0.01 and a2 / (a2 + b2) < 0.05
