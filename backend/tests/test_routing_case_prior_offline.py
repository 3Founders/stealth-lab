"""Routing with no production fit and no global Goal: the bundled prior and the case-set prior.

No database: every store read the recommender makes is faked. Skipped when the bundle has not been built
(scripts/build_prior_bundle.py).
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from app.routing import prior_bundle, service, store
from app.services.access import AccessScope

pytestmark = pytest.mark.skipif(not prior_bundle.available(), reason="no bundled prior (build_prior_bundle.py)")

SCOPE = AccessScope.unrestricted()
UNITS = ["claude-haiku-4-5|claude-code", "claude-sonnet-4-5|claude-code"]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_database(monkeypatch):
    recorded = []

    async def none(*a, **k):
        return None

    async def empty_dict(*a, **k):
        return {}

    async def empty_list(*a, **k):
        return []

    async def never(*a, **k):
        raise AssertionError("a virtual key has no Goal row")

    async def record(pool, row):
        recorded.append(row)

    monkeypatch.setattr(store, "active_params", none)
    monkeypatch.setattr(store, "visible_goal", never)
    monkeypatch.setattr(store, "load_posteriors", empty_dict)
    monkeypatch.setattr(store, "model_registry", empty_dict)
    monkeypatch.setattr(store, "model_cards", empty_list)
    monkeypatch.setattr(store, "current_prices", empty_dict)
    monkeypatch.setattr(store, "model_updates", empty_dict)
    monkeypatch.setattr(store, "goal_token_stats", empty_dict)
    monkeypatch.setattr(store, "goal_rows", empty_dict)
    monkeypatch.setattr(store, "record_decision", record)
    return recorded


def plan_for(virtual, local_obs=()):
    return run(service._recommend(None, goal_id=service.virtual_goal_id(virtual["kind"], virtual.get("ref", "")),
                                  candidates=UNITS, access_scope=SCOPE, virtual=virtual,
                                  instance_key="k", local_obs=local_obs))


def test_bundle_loads_with_its_cards():
    g = prior_bundle.load_globals()
    assert g is not None and g.version == prior_bundle.BUNDLE_VERSION and g.meta.get("bundled")
    keys = {r["model_key"] for r in prior_bundle.card_rows()}
    assert any("haiku" in k for k in keys) and any("sonnet" in k for k in keys)


def test_a_generic_task_gets_a_plan_from_the_bundled_prior_alone(no_database):
    rec = plan_for({"kind": "generic"})
    assert rec["status"] == "ok", rec
    assert rec["evidence"]["prior"] == "bundled public prior"
    assert rec["evidence"]["case"] == {"kind": "generic"}
    assert rec["recommended"]["ladder"][0] in UNITS
    stored = no_database[0]["constraints"]["_virtual"]
    assert stored == {"kind": "generic"}                     # carried into report_result's re-decisions


def test_the_case_features_move_the_prior(no_database):
    small = {"files": 1, "hunks": 1, "lines_added": 2, "lines_removed": 1, "languages": 1, "packages": 1}
    large = {"files": 30, "hunks": 80, "lines_added": 1500, "lines_removed": 600, "languages": 4, "packages": 6}
    p_small = plan_for({"kind": "library", "ref": "L-1", "features": small})["p_correct_single_attempt"]
    p_large = plan_for({"kind": "library", "ref": "L-2", "features": large})["p_correct_single_attempt"]
    # a much bigger fix is a harder task: every model's chance of passing goes down
    assert all(p_large[u] < p_small[u] for u in UNITS), (p_small, p_large)


def test_local_counts_condition_a_virtual_plan(no_database):
    base = plan_for({"kind": "repo", "ref": "r:1"})["p_correct_single_attempt"]
    obs = [{"unit": UNITS[0], "n": 8, "ok": 0}]                 # the cheap model failed 8 times in this repo
    after = plan_for({"kind": "repo", "ref": "r:1"}, local_obs=obs)
    assert after["evidence"]["local"]["attempts"] == 8
    assert after["p_correct_single_attempt"][UNITS[0]] < base[UNITS[0]]
    assert np.isfinite(after["evidence"]["local"]["ess"])
