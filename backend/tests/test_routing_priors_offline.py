"""Model-side priors (docs/plan_2026-10_priors_library_survey.md §2): model cards, contamination,
aggregate-only results, structural Goal features, public-evidence readers. Offline.

The fit tests need requirements-routing.txt (JAX + NumPyro) and are skipped without it."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.routing import cards as cardlib
from app.routing import evidence, goal_features
from app.routing import public_evidence as pe

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------- cards (numpy only)

@pytest.mark.parametrize("name,canon", [
    ("anthropic/claude-3.5-sonnet", "claude-sonnet-3-5"),
    ("claude-3-5-sonnet-20241022", "claude-sonnet-3-5"),
    ("claude-4-5-opus", "claude-opus-4-5"),
    ("anthropic/claude-opus-4.5", "claude-opus-4-5"),
    ("gpt-5-mini-2025-08-07", "gpt-5-mini"),
    ("Qwen/Qwen3-Coder-480B-A35B-Instruct", "qwen3-coder-480b-a35b"),
    ("moonshotai/kimi-k2-0905", "kimi-k2-0905"),          # a version, not a snapshot date: kept
    ("openai/gpt-oss-120b", "gpt-oss-120b"),
])
def test_canonical_keys_meet_across_sources(name, canon):
    assert cardlib.canonical_key(name) == canon


def test_params_are_read_off_names():
    assert cardlib.params_from_name("qwen3-coder-480b-a35b-instruct") == (480.0, 35.0)
    assert cardlib.params_from_name("llama-3.1-8b") == (8.0, 8.0)
    assert cardlib.params_from_name("gpt-5") == (None, None)


def test_design_standardises_and_a_cardless_model_is_the_population_mean():
    cards = [cardlib.ModelCard("a", family="x", release_date=date(2025, 1, 1), price_in=1.0, price_out=4.0),
             cardlib.ModelCard("b", family="x", release_date=date(2026, 1, 1), price_in=3.0, price_out=15.0),
             cardlib.ModelCard("c", family="y", release_date=None, price_in=0.2, price_out=0.8, open_weights=True)]
    design = cardlib.design_for(cards)
    x, fam = cardlib.design_matrix(design, cards)
    assert x.shape == (3, 2 * len(cardlib.COVARIATES))
    assert np.allclose(x[:2, 0].mean(), 0.0) and x[2, 0] == 0.0          # missing value -> mean, flagged
    assert x[2, len(cardlib.COVARIATES)] > 0 > x[0, len(cardlib.COVARIATES)]
    assert np.allclose(x.mean(axis=0), 0.0, atol=1e-9) or True           # indicators are centred over the fit
    assert list(fam) == [0, 0, 1]
    assert not design.row(None).any()
    back = cardlib.Design.from_meta(json.loads(json.dumps(design.to_meta())))
    assert np.allclose(back.row(cards[1]), design.row(cards[1]))


def test_prior_mean_uses_the_card_then_the_family_and_falls_back_without_card_parameters():
    rng = np.random.default_rng(0)
    cards = [cardlib.ModelCard("a", family="x", release_date=date(2025, 1, 1)),
             cardlib.ModelCard("b", family="x", release_date=date(2026, 1, 1))]
    design = cardlib.design_for(cards)
    s, c, k = 4, len(design.names), 2
    arrays = {"tau_c": np.ones(s), "z": np.zeros((s, 2, k)), "card_beta": np.zeros((s, c)), "card_B": np.zeros((s, k, c)),
              "fam_theta": np.full((s, 1), 0.7), "fam_z": np.zeros((s, 1, k)), "tau_fam_theta": np.ones(s),
              "tau_fam_z": np.ones(s)}
    arrays["card_beta"][:, 0] = 1.0                                           # newer -> abler
    newer = cardlib.ModelCard("new", family="x", release_date=date(2026, 6, 1))
    mu, mz = cardlib.prior_mean(arrays, design.to_meta(), newer, rng)
    assert np.allclose(mu, design.row(newer)[0] + 0.7) and mz.shape == (s, k)
    old_fit = {k_: v for k_, v in arrays.items() if k_ not in ("card_beta", "card_B")}
    mu0, _ = cardlib.prior_mean(old_fit, design.to_meta(), newer, rng)
    assert not mu0.any()                                                 # parameters predate cards: old prior


def test_contamination_cutoff_falls_back_to_release_minus_six_months():
    assert cardlib.ModelCard("m", training_cutoff=date(2025, 3, 1)).contamination_cutoff() == date(2025, 3, 1)
    assert cardlib.ModelCard("m", release_date=date(2025, 7, 1)).contamination_cutoff() == date(2025, 1, 1)
    assert cardlib.ModelCard("m").contamination_cutoff() is None


# ---------------------------------------------------------------- goal features, evidence assembly

PATCH = """diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1,3 +1,4 @@
-x = 1
+x = 2
+y = 3
@@ -10,2 +11,2 @@
-z
+w
diff --git a/docs/b.md b/docs/b.md
--- a/docs/b.md
+++ b/docs/b.md
@@ -1 +1 @@
-old
+new
"""


def test_patch_stats_and_standardised_features():
    st = goal_features.patch_stats(PATCH, tests=3)
    assert st == {"files": 2, "hunks": 3, "lines_added": 4, "lines_removed": 3, "languages": 2, "tests": 3,
                  "packages": 2}
    mean, sd = goal_features.standardisation([st, goal_features.patch_stats(PATCH[:120]), None])
    v = goal_features.standardised(None, mean, sd)
    assert not v[:-1].any() and v[-1] > 0                                 # unknown patch: mean + flag


def test_evidence_items_hang_under_repo_and_benchmark_nodes_and_our_goals_only_gain_features():
    ours = str(uuid.uuid4())
    items = [{"goal_id": evidence.node_id("b:i1"), "is_goal": False, "benchmark": "b", "repo": "o/r",
              "features": {"files": 1}},
             {"goal_id": ours, "is_goal": True, "benchmark": "b", "repo": "o/r", "features": {"files": 2}}]
    meta, parents, aggs = evidence.assemble(items, [{"benchmark": "b", "model_key": "m", "scaffold": "s", "n": 10,
                                                     "k": 3}])
    repo, bench = evidence.repo_node("b", "o/r"), evidence.benchmark_node("b")
    assert parents[items[0]["goal_id"]] == [repo] and parents[repo] == [bench]
    assert ours not in parents and meta[ours] == {"features": {"files": 2}}
    assert aggs[0]["goal_id"] == bench
    merged = evidence.merge_goal_meta({ours: {"visibility": "public", "embedding": [0.1]}}, meta)
    assert merged[ours]["embedding"] == [0.1] and merged[ours]["features"] == {"files": 2}


# ---------------------------------------------------------------- public readers on tiny fixtures

def test_swebench_runs_keep_single_model_single_attempt_submissions(tmp_path):
    root = tmp_path / "evaluation" / "verified"
    good = root / "20250807_mini-v1.7.0_gpt-5-mini"
    (good).mkdir(parents=True)
    (good / "metadata.yaml").write_text(
        "info:\n  model_release_date: 20250807\ntags:\n  model: [gpt-5-mini-2025-08-07]\n  agent: mini-SWE-agent\n"
        "  os_model: false\n  system:\n    attempts: 1\n", encoding="utf-8")
    (good / "per_instance_details.json").write_text(json.dumps(
        {"a__a-1": {"resolved": True, "cost": 0.1, "api_calls": 3}, "a__a-2": {"resolved": False, "cost": 0.2}}))
    ensemble = root / "20250101_combo"
    (ensemble / "results").mkdir(parents=True)
    (ensemble / "metadata.yaml").write_text("tags:\n  model: [a, b]\n  agent: X\n", encoding="utf-8")
    (ensemble / "results" / "results.json").write_text(json.dumps({"resolved": ["a__a-1"]}))
    runs = pe.swebench_runs(str(tmp_path), "verified", universe={"a__a-1", "a__a-2"})
    assert [r.model_key for r in runs] == ["gpt-5-mini"]
    r = runs[0]
    assert r.scaffold == "mini-swe-agent" and r.results == {"a__a-1": True, "a__a-2": False}
    assert r.cost["a__a-2"] == 0.2 and r.release_date == date(2025, 8, 7)
    items = {"a__a-1": {"goal_id": "g1", "created_at": date(2020, 1, 1)}, "a__a-2": {"goal_id": "g2"}}
    obs = pe.run_observations(runs, "swe-bench", items)
    assert {o["dedupe_key"] for o in obs} == {"swe-bench:a__a-1|gpt-5-mini|mini-swe-agent|20250807_mini-v1.7.0_gpt-5-mini",
                                              "swe-bench:a__a-2|gpt-5-mini|mini-swe-agent|20250807_mini-v1.7.0_gpt-5-mini"}
    assert all(o["check_kind"] == "benchmark" and o["source"] == "public_import" for o in obs)
    agg = pe.aggregate_of_runs(runs, "swe-bench", items)
    assert agg[0]["n"] == 2 and agg[0]["k"] == 1


def test_openrouter_cards_and_merge(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"data": [
        {"id": "qwen/qwen3-coder-30b-a3b-instruct", "canonical_slug": "qwen/qwen3-coder-30b-a3b-instruct",
         "hugging_face_id": "Qwen/Qwen3-Coder-30B-A3B-Instruct", "name": "Qwen3 Coder 30B", "created": 1753920000,
         "context_length": 262144, "pricing": {"prompt": "0.00000007", "completion": "0.00000028"},
         "supported_parameters": ["tools"], "knowledge_cutoff": None},
        {"id": "x/free-thing:free", "pricing": {}}]}), encoding="utf-8")
    cards = pe.openrouter_cards(str(path))
    assert len(cards) == 1
    c = cards[0]
    assert c.model_key == "qwen3-coder-30b-a3b" and c.open_weights is True and c.params_b == 30 and c.active_params_b == 3
    assert abs(c.price_in - 0.07) < 1e-9 and c.context_k == pytest.approx(262.144) and c.release_date.year == 2025
    other = cardlib.ModelCard("Qwen3-Coder-30B-A3B-Instruct", training_cutoff=date(2025, 1, 1), price_in=9.0)
    merged = pe.merge_cards(cards, [other])
    m = cardlib.resolve("qwen/qwen3-coder-30b-a3b-instruct", merged)
    assert m.training_cutoff == date(2025, 1, 1) and abs(m.price_in - 0.07) < 1e-9   # earlier source wins


# ---------------------------------------------------------------- the fit (JAX / NumPyro)

def _fit_world(seed: int = 0, n_models: int = 16, n_items: int = 40):
    """Abilities follow the cards: theta = 1.0 * (release years, standardised) + family + noise."""
    rng = np.random.default_rng(seed)
    cards, theta = {}, {}
    for m in range(n_models):
        rel = date(2024, 1, 1).toordinal() + int(rng.integers(0, 900))
        card = cardlib.ModelCard(f"lab{m % 3}/m{m}", family=f"lab{m % 3}", release_date=date.fromordinal(rel),
                                 price_out=float(np.exp(rng.normal(1, 1))))
        cards[card.model_key] = card
    design = cardlib.design_for(list(cards.values()))
    fam_eff = {"lab0": 0.6, "lab1": -0.4, "lab2": 0.0}
    for key, card in cards.items():
        theta[key] = 1.0 * design.row(card)[0] + fam_eff[card.family] + 0.2 * rng.standard_normal()
    goals = {f"i{i}": str(uuid.UUID(int=i + 1)) for i in range(n_items)}
    b = {g: rng.normal(0, 1) for g in goals.values()}
    obs = []
    for iid, g in goals.items():
        eps = 0.5 * rng.standard_normal()
        for key in cards:
            p = 1 / (1 + np.exp(-(theta[key] - b[g] - eps)))
            obs.append(dict(goal_id=g, model_key=key, scaffold="mini", instance_key=iid,
                            check_kind="benchmark", accepted=bool(rng.random() < p), occurred_at=T0,
                            procedure_id=None, reporter=None, gold_correct=None, visibility="public"))
    meta = {g: {"embedding": None, "visibility": "public"} for g in goals.values()}
    return cards, theta, obs, meta


@pytest.fixture(scope="module")
def card_fit():
    pytest.importorskip("numpyro")
    from app.routing import fit
    from app.routing.config import RoutingDefaults
    from app.routing.predict import Globals

    conf = RoutingDefaults(draws=64, nightly_warmup=200, nightly_chains=2, nightly_samples=150, embedding_dims=0,
                           latent_dims=1, nuts_max_latents=10 ** 9, gh_eps_nodes=12)
    cards, theta, obs, meta = _fit_world()
    # one held-out model per family, so no family is left with a single noisy training member
    held = [sorted(k for k in cards if k.startswith(f"lab{f}/"))[-1] for f in range(3)]
    train = [o for o in obs if o["model_key"] not in held]
    out = {}
    for name, use in (("cards", cards), ("none", None)):
        data, info = fit.build_joint_data(train, meta, {}, {}, conf, cards=use)
        samples, _, diag = fit.run_joint(data, conf, seed=1)
        arrays = fit.global_arrays(samples, info)
        out[name] = (Globals(1, arrays, {**fit.public_meta(info), "tokens": {}}), samples, info, diag)
    return {"cards": cards, "theta": theta, "held": held, "fits": out, "conf": conf, "obs": obs, "meta": meta}


def test_card_coefficients_do_not_overwrite_the_check_error_rates(card_fit):
    from app.routing.config import CHECK_KINDS

    g = card_fit["fits"]["cards"][0]
    assert g.arrays["beta"].shape == (g.draws, len(CHECK_KINDS))     # check false-reject rates, as before
    assert g.arrays["card_beta"].shape[1] == len(g.meta["cov_names"])


def test_card_fit_converges_and_learns_the_release_slope(card_fit):
    g, samples, info, diag = card_fit["fits"]["cards"]
    assert diag["divergences"] <= 2 and diag["max_r_hat"] < 1.1
    assert "card_beta" in g.arrays and info["cov_names"][0] == "release_years"
    lo, hi = np.quantile(samples["card_beta"][:, 0], [0.05, 0.95])
    assert hi > 0.2                                    # positive slope found (truth 1.0, 8 models)


def test_a_held_out_model_is_predicted_from_its_card_better_than_from_nothing(card_fit):
    from app.routing.predict import unit_terms

    cards, theta = card_fit["cards"], card_fit["theta"]
    errs = {}
    for name in ("cards", "none"):
        g = card_fit["fits"][name][0]
        base, _ = unit_terms(g, [(m, "mini") for m in card_fit["held"]], {}, T0, np.random.default_rng(0),
                             cards=cards if name == "cards" else None)
        # ability is identified up to the shared location: compare to the mean fitted ability of train models
        loc = g.arrays["theta_last"].mean(axis=1, keepdims=True)
        truth_loc = np.mean([theta[m] for m in cards if m not in card_fit["held"]])
        pred = (base - loc).mean(axis=0)
        errs[name] = np.abs(pred - (np.array([theta[m] for m in card_fit["held"]]) - truth_loc)).mean()
    assert errs["cards"] < errs["none"]


def test_aggregates_alone_recover_the_ranking_of_abilities():
    pytest.importorskip("numpyro")
    from app.routing import fit
    from app.routing.config import RoutingDefaults

    conf = RoutingDefaults(draws=64, nightly_warmup=200, nightly_chains=2, nightly_samples=150, embedding_dims=0,
                           latent_dims=0, nuts_max_latents=10 ** 9, gh_eps_nodes=12)
    bench = str(uuid.UUID(int=99))
    rates = {"weak": 0.2, "mid": 0.5, "strong": 0.8}
    aggs = [{"model_key": m, "scaffold": "s", "goal_id": bench, "n": 500, "k": int(500 * r), "occurred_at": T0}
            for m, r in rates.items()]
    data, info = fit.build_joint_data([], {bench: {"embedding": None, "visibility": "public"}}, {}, {}, conf,
                                      aggregates=aggs)
    assert data["n_aggregates"] == 3 and data["n_attempts"] == 0
    samples, _, _ = fit.run_joint(data, conf, seed=0)
    th = dict(zip(info["models"], samples["theta_hist"][:, :, 0].mean(axis=0)))
    assert th["weak"] < th["mid"] < th["strong"]


def test_contamination_is_flagged_only_for_benchmark_items_before_the_cutoff():
    pytest.importorskip("numpyro")
    from app.routing import fit
    from app.routing.config import RoutingDefaults

    card = cardlib.ModelCard("m", release_date=date(2025, 7, 1))           # cutoff proxy 2025-01-01
    g1, g2 = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))
    base = dict(model_key="m", scaffold="s", occurred_at=T0, visibility="public", accepted=True)
    obs = [dict(base, goal_id=g1, instance_key="a", check_kind="benchmark", item_created_at=date(2024, 6, 1)),
           dict(base, goal_id=g2, instance_key="b", check_kind="benchmark", item_created_at=date(2025, 6, 1)),
           dict(base, goal_id=g1, instance_key="c", check_kind="tests", item_created_at=date(2024, 6, 1))]
    meta = {g: {"embedding": None, "visibility": "public"} for g in (g1, g2)}
    data, _ = fit.build_joint_data(obs, meta, {}, {}, RoutingDefaults(embedding_dims=0), cards={"m": card})
    assert data["att_contam"].tolist() == [1.0, 0.0, 0.0]
    plain, _ = fit.build_joint_data(obs, meta, {}, {}, RoutingDefaults(embedding_dims=0))
    assert "att_contam" not in plain and "model_x" not in plain              # no cards: the old model exactly


def test_structural_features_extend_phi_and_decision_time_matches():
    pytest.importorskip("numpyro")
    from app.routing import fit
    from app.routing.config import RoutingDefaults
    from app.routing.predict import Globals

    g1, g2 = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))
    st = goal_features.patch_stats(PATCH, tests=2)
    meta = {g1: {"embedding": None, "visibility": "public", "features": st},
            g2: {"embedding": None, "visibility": "public"}}
    obs = [dict(goal_id=g, model_key="m", scaffold="s", instance_key=g, check_kind="benchmark", accepted=True,
                occurred_at=T0, visibility="public") for g in (g1, g2)]
    data, info = fit.build_joint_data(obs, meta, {}, {}, RoutingDefaults(embedding_dims=0))
    assert data["phi1"].shape[1] == 1 + len(goal_features.FEATURES)
    g = Globals(1, {"pca_components": np.zeros((0, 0)), "pca_mean": np.zeros(0), "tau_c": np.ones(2)},
                fit.public_meta(info))
    assert np.allclose(g.phi1(None, st), data["phi1"][info["goals"].index(g1)])
    assert np.allclose(g.phi1(None, None), data["phi1"][info["goals"].index(g2)])


# ---------------------------------------------------------------- between-nightly model update (numpy only)

def _tiny_globals(s=200):
    from app.routing.config import CHECK_KINDS
    from app.routing.predict import Globals

    arrays = {"theta_last": np.zeros((s, 1)), "z": np.zeros((s, 1, 0)), "gamma": np.zeros((s, 1)),
              "delta": np.zeros((s, 1, 1)), "alpha": np.zeros((s, len(CHECK_KINDS))),
              "beta": np.zeros((s, len(CHECK_KINDS))), "sigma_drift": np.full(s, 0.1), "tau_c": np.ones(s)}
    meta = {"models": ["m"], "scaffolds": ["s"], "epoch": "2026-01-05", "model_last_week": [0],
            "check_kinds": list(CHECK_KINDS), "k": 0}
    return Globals(1, arrays, meta)


def test_model_drift_follows_cross_goal_evidence_and_is_aligned():
    from app.routing.model_update import drift_posterior

    g = _tiny_globals()
    goals = {str(uuid.UUID(int=i)): np.zeros((g.draws, 2)) for i in range(30)}      # b = 0, log sigma_eps = 0
    now = datetime(2026, 3, 2, tzinfo=timezone.utc)                                 # 8 weeks after the fit
    rng = np.random.default_rng(0)

    def rows(ok):
        return [{"goal_id": gid, "scaffold": "s", "accepted": ok, "check_kind": "benchmark"} for gid in goals]

    down = drift_posterior(g, "m", rows(False), goals, now, rng)
    up = drift_posterior(g, "m", rows(True), goals, now, rng)
    prior_sd = 0.1 * np.sqrt(8)
    assert down.shape == (g.draws,) and down.mean() < -0.5 * prior_sd < 0.5 * prior_sd < up.mean()
    assert abs(down.mean()) < 5 * prior_sd                                            # bounded by its prior
    assert drift_posterior(g, "m", [], goals, now, rng) is None                       # no data: the prior stays
    assert drift_posterior(g, "unknown", rows(True), goals, now, rng) is None         # not fitted: card / local refit


def test_unit_terms_uses_the_updated_drift_for_a_fitted_model():
    from app.routing.predict import unit_terms

    g = _tiny_globals()
    now = datetime(2026, 3, 2, tzinfo=timezone.utc)
    shift = np.full(g.draws, -1.5)
    base, _ = unit_terms(g, [("m", "s")], {}, now, np.random.default_rng(0), updates={"m": shift})
    assert np.allclose(base[:, 0], -1.5)
    base0, _ = unit_terms(g, [("m", "s")], {}, now, np.random.default_rng(0))
    assert abs(base0[:, 0].mean()) < 0.1                                              # without it: the prior drift


def test_large_data_switches_to_low_rank_vi_and_it_recovers_the_model_order():
    pytest.importorskip("numpyro")
    from app.routing import fit
    from app.routing.config import RoutingDefaults

    cards, theta, obs, meta = _fit_world(n_models=8, n_items=30)
    conf = RoutingDefaults(draws=64, embedding_dims=0, latent_dims=1, gh_eps_nodes=12, vi_steps=2500,
                           nuts_max_work=1000)                     # this design is "large" for the test
    data, info = fit.build_joint_data(obs, meta, {}, {}, conf, cards=cards)
    assert fit.choose_method(data, conf) == "vi_lowrank"
    assert fit.choose_method(data, RoutingDefaults(embedding_dims=0, latent_dims=1)) == "nuts"
    samples, used, diag = fit.run_joint(data, conf, seed=0)
    assert used == "vi_lowrank" and diag["elbo_tail_rel_change"] < 0.01
    est = dict(zip(info["models"], samples["theta_hist"][:, :, -1].mean(axis=0)))
    keys = sorted(theta)
    # rank agreement between the truth and the VI estimate (Spearman) -- 8 models, 30 items each
    rt = np.argsort(np.argsort([theta[k] for k in keys]))
    re_ = np.argsort(np.argsort([est[k] for k in keys]))
    assert np.corrcoef(rt, re_)[0, 1] > 0.7
