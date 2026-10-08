"""Token levels for the cost model, calibrated from published per-task costs (costs.py, plan §4).

Public results give a dollar cost per task (mini-swe-agent runs publish it), not token counts. Providers price
in near-fixed ratios (cache reads ~0.1x input, output ~4-8x input), so the split between uncached input, cache
reads and output cannot be recovered from costs alone. The SHAPE of an agent task's token use is therefore a
setting (`token_mix`: output per input token, and the share of input served from cache -- agent loops re-send a
growing context, so most input is cache reads), and the costs calibrate its SCALE: one level for all models,
and a factor per model (a model that writes long answers or takes many turns uses more). Both come out in the
token-stat format the cost model already pools (service.token_summary), and each candidate is then priced with
ITS OWN cache-aware price table.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

from app.routing.config import DEFAULTS, RoutingDefaults

# Shape of one agent task's tokens (per input token: output tokens, and the share of input that is a cache read).
# Override with STEALTH_ROUTING_OUT_PER_IN / STEALTH_ROUTING_CACHED_SHARE when a deployment measures its own.
OUT_PER_IN = 0.02
CACHED_SHARE = 0.85


def _mix(cfg: RoutingDefaults) -> tuple[float, float]:
    import os

    def f(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except ValueError:
            return default

    return f("STEALTH_ROUTING_OUT_PER_IN", OUT_PER_IN), min(max(f("STEALTH_ROUTING_CACHED_SHARE", CACHED_SHARE), 0.0), 0.99)


def _prices(card: Any) -> Optional[tuple[float, float, float]]:
    if card is None or card.price_in is None or card.price_out is None or card.price_in <= 0:
        return None
    cached = card.price_cached if card.price_cached is not None and card.price_cached > 0 else card.price_in
    return float(card.price_in), float(cached), float(card.price_out)


def calibrate(observations: Sequence[Mapping[str, Any]], resolve_card: Any,
              cfg: RoutingDefaults = DEFAULTS, *, min_rows: int = 30) -> dict[str, Any]:
    """{"global": {outcome: stats}, "units": {}, "models": {model: {outcome: stats}}, "calibration": {...}}.
    stats = [n, mean log tokens_in, mean log tokens_out, mean log(1 + cached)], written so that the cost model's
    log-normal mean exp(m + sd^2/2) gives back the calibrated count. `resolve_card(model_key)` -> card or None."""
    out_per_in, share = _mix(cfg)
    logs: dict[str, list[tuple[str, float]]] = defaultdict(list)      # outcome -> (model, log input tokens)
    for o in observations:
        cost = o.get("cost_usd")
        if cost is None or not cost > 0:
            continue
        p = _prices(resolve_card(o["model_key"]))
        if p is None:
            continue
        per_input_token = ((1 - share) * p[0] + share * p[1] + out_per_in * p[2]) / 1e6
        logs["1" if o.get("accepted") else "0"].append((o["model_key"], math.log(float(cost) / per_input_token)))
    shift = cfg.prior_log_sd ** 2 / 2
    kappa = cfg.token_pooling_strength
    result: dict[str, Any] = {"global": {}, "units": {}, "models": {},
                              "calibration": {"out_per_in": out_per_in, "cached_share": share}}

    def stats(n: float, log_in: float) -> list[float]:
        t_in = math.exp(log_in)
        return [float(n), log_in - shift, math.log(t_in * out_per_in) - shift, math.log(1 + t_in * share)]

    for outcome, rows in logs.items():
        if len(rows) < min_rows:
            continue
        values = [v for _, v in rows]
        level = sum(values) / len(values)
        result["global"][outcome] = stats(len(rows), level)
        by_model: dict[str, list[float]] = defaultdict(list)
        for model, v in rows:
            by_model[model].append(v - level)
        for model, devs in by_model.items():
            # shrink the model's factor toward the global level until it has data (the cost model's kappa)
            result["models"].setdefault(model, {})[outcome] = stats(len(devs), level + sum(devs) / (len(devs) + kappa))
        result["calibration"][outcome] = {"rows": len(rows), "models": len(by_model),
                                          "median_tokens_in": round(math.exp(sorted(values)[len(values) // 2]))}
    return result
