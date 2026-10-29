"""A probed model takes its place in the ranking from its probe score (app/routing/probe.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.routing import cards as cardlib, probe


def _g(thetas: dict[str, float], draws: int = 400):
    models = sorted(thetas)
    return SimpleNamespace(models=models, draws=draws,
                           arrays={"theta_last": np.tile([thetas[m] for m in models], (draws, 1))})


ANCHORS = {"a": (90, 100), "b": (70, 100), "c": (50, 100), "d": (30, 100), "e": (10, 100), "unfitted": (95, 100)}
THETA = {"a": 2.0, "b": 1.0, "c": 0.0, "d": -1.0, "e": -2.0}


def test_the_line_runs_through_the_anchors_the_fit_knows():
    fit = probe.line(_g(THETA), ANCHORS)
    assert fit["anchors"] == ["a", "b", "c", "d", "e"], "an anchor the fit does not know is not used"
    assert fit["b1"] > 0 and fit["r"] > 0.99


def test_a_probed_model_lands_by_its_score_with_honest_uncertainty(monkeypatch):
    monkeypatch.setattr(probe, "probe_id", lambda *a: "p1")
    g = _g(THETA)
    rng = np.random.default_rng(0)
    strong = probe.theta_draws(g, {"probe": "p1", "k": 80, "n": 100}, rng, ANCHORS)
    weak = probe.theta_draws(g, {"probe": "p1", "k": 20, "n": 100}, rng, ANCHORS)
    few = probe.theta_draws(g, {"probe": "p1", "k": 4, "n": 5}, rng, ANCHORS)
    assert 1.0 < strong.mean() < 2.0 and -2.0 < weak.mean() < -1.0
    assert few.std() > strong.std(), "five problems say less than a hundred"
    assert probe.theta_draws(g, {"probe": "other", "k": 80, "n": 100}, rng, ANCHORS) is None
    assert probe.theta_draws(_g({"a": 1.0, "b": 0.0}), {"probe": "p1", "k": 80, "n": 100}, rng, ANCHORS) is None, \
        "too few anchors in the fit: no placement, the card prior stands"


def test_unit_terms_takes_a_probed_models_theta_from_its_probe(monkeypatch):
    from app.routing import predict, prior_bundle

    g = prior_bundle.load_globals()
    if g is None:
        pytest.skip("no bundled prior")
    by_theta = sorted(g.models, key=lambda m: -float(g.arrays["theta_last"][:, g.models.index(m)].mean()))
    anchors = {m: (k, 100) for m, k in zip(by_theta[::8][:8], (90, 80, 70, 60, 50, 40, 30, 20))}
    monkeypatch.setattr(probe, "anchor_scores", lambda *a: anchors)
    monkeypatch.setattr(probe, "probe_id", lambda *a: "p1")
    card = {"newbie-7b": cardlib.ModelCard("newbie-7b", price_in=0.1, price_out=0.2)}
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)

    def mean(probes):
        base, _ = predict.unit_terms(g, [("newbie-7b", "direct")], {}, now, np.random.default_rng(0), cards=card,
                                     probes=probes)
        return float(base[:, 0].mean())

    hi = mean({"newbie-7b": {"probe": "p1", "k": 90, "n": 100}})
    lo = mean({"newbie-7b": {"probe": "p1", "k": 15, "n": 100}})
    fit = probe.line(g, anchors)
    assert hi > lo and fit is not None
    assert mean({}) != pytest.approx(hi), "without a probe it is the card prior"
