"""Cost model (docs/model_routing_plan.md §4): learn TOKENS, price them at decision time.

Log-token means are partially pooled global -> unit -> (Goal, unit), separately for
accepted and rejected attempts (failed attempts usually run longer):

    m_global = (kappa * log(prior) + n * ybar) / (kappa + n)
    m_unit   = (kappa * m_global  + n_u * ybar_u) / (kappa + n_u)
    m_goal   = (kappa * m_unit    + n_gu * ybar_gu) / (kappa + n_gu)

the normal-normal posterior mean with prior strength kappa. The prior token counts
only matter until data exists. Expected tokens = exp(m + sd^2 / 2) (log-normal mean).
Dollars = (in - cached) * p_in + cached * p_cached + out * p_out, from the live price
table, so a price change applies immediately.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

from app.routing.config import DEFAULTS, RoutingDefaults

# stats per outcome ("1" accepted / "0" rejected): [n, mean log tokens_in, mean log tokens_out, mean log(1+cached)]
Stats = Mapping[str, list[float]]


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    cached_per_mtok: Optional[float]


def _pool(prior_mean: float, n: float, mean: Optional[float], kappa: float) -> float:
    if not n or mean is None:
        return prior_mean
    return (kappa * prior_mean + n * mean) / (kappa + n)


def expected_tokens(outcome: str, global_stats: Stats, unit_stats: Optional[Stats], goal_stats: Optional[Stats],
                    cfg: RoutingDefaults = DEFAULTS) -> tuple[float, float, float]:
    """(tokens_in, tokens_out, tokens_cached) expected for one attempt with `outcome`."""
    kappa, sd2 = cfg.token_pooling_strength, cfg.prior_log_sd ** 2
    prior = (math.log(cfg.prior_tokens_in), math.log(cfg.prior_tokens_out), 0.0)
    means = []
    for j in range(3):
        level = prior[j]
        for stats in (global_stats, unit_stats, goal_stats):
            row = (stats or {}).get(outcome)
            level = _pool(level, row[0] if row else 0, row[1 + j] if row else None, kappa)
        means.append(level)
    t_in = math.exp(means[0] + sd2 / 2)
    t_out = math.exp(means[1] + sd2 / 2)
    # cached tokens: geometric mean of (1 + cached) - 1 -- no data means no cache hits assumed,
    # which prices the attempt at the full input rate (the conservative direction)
    cached = max(0.0, math.exp(means[2]) - 1.0)
    return t_in, t_out, min(cached, t_in)


def dollars(price: Price, tokens_in: float, tokens_out: float, tokens_cached: float) -> float:
    cached_rate = price.cached_per_mtok if price.cached_per_mtok is not None else price.input_per_mtok
    return ((tokens_in - tokens_cached) * price.input_per_mtok + tokens_cached * cached_rate
            + tokens_out * price.output_per_mtok) / 1e6
