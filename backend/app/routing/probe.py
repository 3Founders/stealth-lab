"""A model's measured place in the ranking from a short probe, before any real task (numpy only).

A model the fit has never seen starts from its card (cards.prior_mean): a guess from price, size, release date and
family. A probe replaces the guess with a measurement. The model answers a fixed set of coding problems that
LLMRouterBench already graded for dozens of models ("anchors"; `data/probe_lcb.json`, scripts/routing_probe_modal.py),
graded the same way. Among the anchors the fit also knows, its ability theta is regressed on the probe score:

    theta_m = b0 + b1 * logit(acc_m) + e,    e ~ N(0, s^2)

and the probed model's theta is drawn from that line at its own score. Its uncertainty is the residual s plus the
binomial noise of n problems, carried through the slope. The line is fitted against whichever fit is active (bundle
or database), so a refit moves it too. A model with real results is fitted and ignores its probe; one with a fitted
predecessor uses the predecessor first (predict.unit_terms).
"""
from __future__ import annotations

import functools
import json
import math
import os
from typing import Any, Mapping, Optional

import numpy as np

from app.routing import cards as cardlib

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PROBE_FILE = "probe_lcb.json"              # the problems, their tests, and each anchor's result
RESULTS_FILE = "probe_results.json"        # {model_key: {"probe", "n", "k", "at", ...}} for probed models
MIN_ANCHORS = 5


def _logit(k: float, n: float) -> float:
    p = (k + 0.5) / (n + 1.0)
    return math.log(p / (1.0 - p))


@functools.lru_cache(maxsize=4)
def anchor_scores(data_dir: str = DATA_DIR) -> dict[str, tuple[int, int]]:
    """{canonical model key: (k, n)} of every anchor on the probe ({} without the probe file)."""
    path = os.path.join(data_dir, PROBE_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)["items"]
    out: dict[str, list[int]] = {}
    for it in items:
        for model, ok in it["anchors"].items():
            kn = out.setdefault(cardlib.canonical_key(model), [0, 0])
            kn[0] += int(ok)
            kn[1] += 1
    return {m: (k, n) for m, (k, n) in out.items()}


@functools.lru_cache(maxsize=4)
def results(data_dir: str = DATA_DIR) -> dict[str, dict]:
    """Probed models' scores, by canonical key ({} without the file)."""
    path = os.path.join(data_dir, RESULTS_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {cardlib.canonical_key(m): r for m, r in json.load(fh).items()}


def line(g: Any, anchors: Mapping[str, tuple[int, int]]) -> Optional[dict]:
    """The regression of the fit's mean theta on probe logit accuracy over the anchors the fit knows."""
    theta = {}
    for m, (k, n) in anchors.items():
        if m in g.models:
            theta[m] = float(np.mean(g.arrays["theta_last"][:, g.models.index(m)]))
    if len(theta) < MIN_ANCHORS:
        return None
    ms = sorted(theta)
    x = np.array([_logit(*anchors[m]) for m in ms])
    y = np.array([theta[m] for m in ms])
    b1, b0 = np.polyfit(x, y, 1)
    resid = y - (b0 + b1 * x)
    s = float(np.sqrt(resid @ resid / max(len(ms) - 2, 1)))
    r = float(np.corrcoef(x, y)[0, 1]) if len(ms) > 2 else float("nan")
    return {"b0": float(b0), "b1": float(b1), "s": s, "r": r, "anchors": ms}


def theta_draws(g: Any, result: Mapping[str, Any], rng: np.random.Generator,
                anchors: Optional[Mapping[str, tuple[int, int]]] = None) -> Optional[np.ndarray]:
    """(S,) theta draws for a probed model, or None when the probe cannot be placed (no anchors in this fit, a
    result from another probe, a degenerate line)."""
    anchors = anchor_scores() if anchors is None else anchors
    if not anchors or result.get("probe") != probe_id():
        return None
    fit = line(g, anchors)
    if fit is None or not fit["b1"] > 0:
        return None
    k, n = float(result["k"]), float(result["n"])
    x = _logit(k, n)
    p = (k + 0.5) / (n + 1.0)
    se_x = 1.0 / math.sqrt((n + 1.0) * p * (1.0 - p))          # sd of the logit of a binomial proportion
    sd = math.sqrt(fit["s"] ** 2 + (fit["b1"] * se_x) ** 2)
    return fit["b0"] + fit["b1"] * x + sd * rng.standard_normal(g.draws)


@functools.lru_cache(maxsize=1)
def probe_id(data_dir: str = DATA_DIR) -> str:
    path = os.path.join(data_dir, PROBE_FILE)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return str(json.load(fh).get("probe") or "")
