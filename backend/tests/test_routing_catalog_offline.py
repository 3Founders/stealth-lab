"""A model added tomorrow is ranked from day one; a removed one changes nobody else's rank (app/routing/catalog.py),
and LLMRouterBench enters as per-(dataset, model) aggregates (public_evidence.llmrouterbench)."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.routing import cards as cardlib, catalog, predict, prior_bundle, public_evidence as pe


@pytest.fixture(autouse=True)
def _fresh_catalogue(monkeypatch):
    monkeypatch.setattr(catalog, "_state", {"at": 0.0, "ok": False, "cards": {}})
    monkeypatch.setattr(catalog, "_lock", asyncio.Lock())


LIVE = [{"id": "acme/brandnew-70b", "created": 1793232000, "context_length": 200000, "hugging_face_id": "acme/brandnew-70b",
         "pricing": {"prompt": "0.0000002", "completion": "0.0000008"}, "supported_parameters": ["reasoning"]}]


def _fetcher(calls):
    async def fetch(url):
        calls.append(url)
        return LIVE
    return fetch


def test_a_model_no_card_knows_gets_one_from_the_live_catalogue_once_a_day():
    calls = []
    cards, src = asyncio.run(catalog.fill_missing(["brandnew-70b"], {}, fetch=_fetcher(calls)))
    card = cardlib.resolve("brandnew-70b", cards)
    assert src == {"brandnew-70b": "openrouter_live"}
    assert card.price_in == pytest.approx(0.2) and card.params_b == 70 and card.family == "acme/brandnew"
    asyncio.run(catalog.fill_missing(["brandnew-70b"], {}, fetch=_fetcher(calls)))
    assert len(calls) == 1, "cached for a day"


def test_known_models_are_untouched_and_the_catalogue_is_not_fetched():
    calls = []
    known = {"gpt-5": cardlib.ModelCard("gpt-5", price_in=1.25, price_out=10.0)}
    cards, src = asyncio.run(catalog.fill_missing(["gpt-5"], known, fetch=_fetcher(calls)))
    assert src == {} and calls == [] and cards["gpt-5"].price_in == 1.25


def test_a_model_no_catalogue_lists_is_carded_from_the_callers_own_prices_and_its_name():
    async def down(url):
        raise OSError("offline")
    hints = {"glm-9-flash-30b": {"price_in": 0.1, "price_out": 0.4, "price_cached": 0.02}}
    cards, src = asyncio.run(catalog.fill_missing(["glm-9-flash-30b", "mystery"], {}, hints, fetch=down))
    card = cardlib.resolve("glm-9-flash-30b", cards)
    assert src == {"glm-9-flash-30b": "caller"} and card.price_out == 0.4 and card.params_b == 30
    assert card.family == "glm-flash"
    assert cardlib.resolve("mystery", cards) is None, "no prices: still no card (the plan cannot cost it)"
    assert catalog.hint_card("x", {"price_in": -1, "price_out": 1}) is None


def test_a_family_named_without_its_provider_matches_the_fitted_family():
    d = cardlib.design_for([cardlib.ModelCard("a", family="z-ai/glm-flash"), cardlib.ModelCard("b", family="openai/gpt")])
    assert d.family_index(cardlib.ModelCard("c", family="glm-flash")) == d.families.index("z-ai/glm-flash")
    assert d.family_index(cardlib.ModelCard("c", family="nobody")) == -1


def test_with_the_bundle_a_new_model_ranks_by_what_it_is_and_removing_one_moves_no_one_else():
    g = prior_bundle.load_globals()
    if g is None:
        pytest.skip("no bundled prior")
    cards = dict(cardlib.load_cards(prior_bundle.card_rows()))
    new = {"tiny-new-1b": catalog.hint_card("tiny-new-1b", {"price_in": 0.02, "price_out": 0.05, "context_k": 32,
                                                            "release_date": "2026-10-29", "reasoning": "none"}),
           "frontier-new": catalog.hint_card("frontier-new", {"price_in": 5, "price_out": 25, "context_k": 1000,
                                                              "release_date": "2026-10-29", "reasoning": "high"})}
    cards.update(new)
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    names = ["claude-opus-5-5", "gemma-4-31b-it", "tiny-new-1b", "frontier-new"]

    def means(ms):
        base, _ = predict.unit_terms(g, [(m, "direct") for m in ms], {}, now, np.random.default_rng(0), cards=cards)
        return dict(zip(ms, base.mean(0)))

    full = means(names)
    assert full["frontier-new"] > full["gemma-4-31b-it"] > full["tiny-new-1b"]
    without = means([m for m in names if m != "gemma-4-31b-it"])
    for m in without:
        assert without[m] == pytest.approx(full[m], abs=0.25), "each model's terms are its own"


def test_llmrouterbench_counts_each_model_once_per_dataset_and_skips_the_router(tmp_path):
    rows = [
        {"dataset": "hle-test", "model": "gpt-5", "run": "hle-test-gpt-5-20251202_073816", "n": 2158, "k": 600},
        {"dataset": "hle-subset_500", "model": "gpt-5", "run": "hle-subset_500-gpt-5-20251020_150418", "n": 500, "k": 140},
        {"dataset": "hle-subset_500", "model": "Qwen3-8B", "run": "hle-subset_500-Qwen3-8B-20251020_150418", "n": 500, "k": 20},
        {"dataset": "verified", "model": "glm-4.6", "run": "SWE-Bench-verified_glm-4.6-20251023_195048", "n": 500, "k": 109},
        {"dataset": "aime-hybrid", "model": "openrouter", "run": "aime-hybrid-openrouter-20251125_102816", "n": 60, "k": 26},
        {"dataset": "arenahard-coding", "model": "gpt-5", "run": "arenahard-coding-gpt-5-20251029_191638", "n": 0, "k": 0},
    ]
    p = tmp_path / "summary.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = {(a["benchmark"], a["model_key"]): a for a in pe.llmrouterbench(str(p))}
    assert set(out) == {("llmrouterbench:hle-test", "gpt-5"), ("llmrouterbench:hle-test", "qwen3-8b"),
                        ("llmrouterbench:swe-bench-verified", "glm-4-6")}
    assert out[("llmrouterbench:hle-test", "gpt-5")]["n"] == 2158, "the full set wins over its subset"
    assert out[("llmrouterbench:hle-test", "qwen3-8b")]["n"] == 500, "a subset counts where there is no full run"
    assert out[("llmrouterbench:swe-bench-verified", "glm-4-6")]["scaffold"] == "llmrouterbench-agent"
    assert out[("llmrouterbench:hle-test", "gpt-5")]["occurred_at"] == datetime(2025, 12, 2, 7, 38, 16, tzinfo=timezone.utc)


def test_name_cards_give_uncatalogued_checkpoints_their_size_and_family():
    (c,) = pe.name_cards(["qwen2-5-coder-7b"])
    assert c.params_b == 7 and c.open_weights is True and c.source == "name"
    merged = pe.merge_cards([cardlib.ModelCard("gpt-5", price_in=1.25, price_out=10.0)], pe.name_cards(["gpt-5"]))
    assert merged["gpt-5"].price_in == 1.25 and merged["gpt-5"].open_weights is None, "a catalogue card is not overwritten"
