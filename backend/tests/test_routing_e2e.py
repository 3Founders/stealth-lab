"""Model recommender against a real database (migrations 120/121).

Public benchmark results are imported as observations, the nightly joint fit runs,
recommend() returns a ladder and logs the decision, a rejected rung makes the next
recommendation condition on it, a reported attempt queues and runs a local refit that
stays aligned with the global draws, a new Goal is served from its parent's posterior
(prior pooling), unpriced units are excluded, and a private Goal is invisible to others."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

pytest.importorskip("numpyro")

from app.routing import store  # noqa: E402
from app.routing.config import RoutingDefaults  # noqa: E402
from app.routing.fit import local_refit, nightly_refit  # noqa: E402
from app.routing.service import RoutingError, record_observation, recommend  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from tests.test_goal_abstraction_e2e import DATABASE_URL, _edge, _goal, _run_id, pool  # noqa: E402,F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")
ANON = AccessScope.anonymous()
CFG = RoutingDefaults(draws=48, nightly_warmup=200, nightly_chains=2, nightly_samples=200, embedding_dims=2,
                      local_global_draws=8, local_warmup=150, latent_dims=1)


def _obs(goal_id, model, instance, accepted, day, **kw):
    return {"source": "public_import", "goal_id": goal_id, "model_key": model, "scaffold": "claude-code",
            "instance_key": instance, "check_kind": "benchmark", "accepted": accepted,
            "tokens_in": kw.get("tokens_in", 40000 if "small" in model else 60000),
            "tokens_out": kw.get("tokens_out", 4000), "reporter": None, "visibility": "public",
            "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=day)}


@pytest.mark.asyncio
async def test_recommender_end_to_end(pool):
    run = _run_id()
    small, big = f"e2e{run}/small", f"e2e{run}/big"
    parent = await _goal(pool, f"e2e {run} fix pagination bugs")
    child = await _goal(pool, f"e2e {run} fix off-by-one pagination in django rest framework")
    sibling = await _goal(pool, f"e2e {run} fix cursor pagination ordering")
    await _edge(pool, child, parent)
    await _edge(pool, sibling, parent)
    await store.set_price(pool, small, input_per_mtok=0.1, output_per_mtok=0.4)
    await store.set_price(pool, big, input_per_mtok=5.0, output_per_mtok=25.0)

    # public per-instance results: both models attempt every instance (identifies eps)
    rng = np.random.default_rng(0)
    rows = []
    for i in range(40):
        eps = 0.7 * rng.standard_normal()
        for model, theta in ((small, 0.3), (big, 2.5)):
            p = 1 / (1 + np.exp(-(theta - 0.0 - eps)))
            rows.append(_obs(child, model, f"{run}-c{i}", bool(rng.random() < p), i // 8))
    ids = await store.insert_observations(pool, rows)
    assert len(ids) == 80

    fitted = await nightly_refit(pool, CFG, seed=1)
    assert fitted["method"] == "nuts" and fitted["observations"] >= 80
    version = fitted["version"]
    posts = await store.load_posteriors(pool, "goal", [child, parent])
    assert posts[child]["version"] == version and posts[parent]["version"] == version

    # --- a recommendation: logged, both units priced, a ladder with credible bounds
    res = await recommend(pool, goal_id=child, candidates=[f"{small}|claude-code", f"{big}|claude-code"],
                          access_scope=ANON, cfg=CFG)
    assert res["status"] == "ok" and res["params_version"] == version
    rec = res["recommended"]
    assert rec["ladder"] and 0 <= rec["p_success_q05"] <= rec["p_success"] <= rec["p_success_q95"] <= 1
    assert res["p_correct_single_attempt"][f"{big}|claude-code"] > res["p_correct_single_attempt"][f"{small}|claude-code"]
    from app.services.shards import search_pool
    logs = await search_pool(pool)                      # project B when SEARCH_DATABASE_URL is set
    log = await logs.fetchrow("SELECT ladder, propensity FROM routing_decisions WHERE id = $1::uuid",
                              res["recommendation_id"])
    assert log is not None and 0 < log["propensity"] <= 1

    # --- the small model was rejected on THIS instance: the big one is now less likely too
    again = await recommend(pool, goal_id=child, candidates=[f"{small}|claude-code", f"{big}|claude-code"],
                            access_scope=ANON, instance_key=res["instance_key"], cfg=CFG,
                            previous_attempts=[{"unit": f"{small}|claude-code", "accepted": False}])
    assert (again["p_correct_single_attempt"][f"{big}|claude-code"]
            < res["p_correct_single_attempt"][f"{big}|claude-code"])

    # --- a reported attempt: stored on the log DB, a local refit is queued, and runs aligned
    obs_id = await record_observation(pool, {**_obs(child, big, res["instance_key"], True, 30),
                                             "source": "live", "check_kind": "tests", "reporter": f"host-{run}"})
    job = await pool.fetchrow("SELECT job_type, payload FROM ingestion_jobs WHERE idempotency_key = $1",
                              f"{child}:{obs_id}")
    assert job["job_type"] == "routing_local_refit"
    assert await logs.fetchval("SELECT count(*) FROM routing_observations WHERE id = $1::uuid", obs_id) == 1
    out = await local_refit(pool, child, CFG, seed=2)
    assert out["observations"] == 81 and out["params_version"] == version
    post = (await store.load_posteriors(pool, "goal", [child]))[child]
    assert post["method"] == "local_nuts" and post["version"] == version

    # --- a Goal with no observations is served from its parent's posterior (pooling)
    sib = await recommend(pool, goal_id=sibling, candidates=[f"{big}|claude-code"], access_scope=ANON, cfg=CFG)
    assert sib["status"] == "ok" and sib["evidence"]["goal_observations"] == 0

    # --- an unpriced unit is excluded, with the reason
    unpriced = await recommend(pool, goal_id=child, candidates=[f"e2e{run}/free|claude-code", f"{big}|claude-code"],
                               access_scope=ANON, cfg=CFG, record=False)
    assert [e["unit"] for e in unpriced["excluded"]] == [f"e2e{run}/free|claude-code"]
    assert all(f"e2e{run}/free" not in u for u in unpriced["recommended"]["ladder"])

    # --- a host report may not claim the benchmark check (it cannot define correctness)
    with pytest.raises(store.ObservationRejected):
        await record_observation(pool, {**_obs(child, big, "x", True, 31), "reporter": "host", "check_kind": "benchmark"})

    # --- privacy: someone else's private Goal is not found
    private = await _goal(pool, f"e2e {run} private goal", visibility="private", owner_id=f"alice-{run}")
    with pytest.raises(RoutingError):
        await recommend(pool, goal_id=private, candidates=[f"{big}|claude-code"], access_scope=ANON, cfg=CFG)
    assert (await recommend(pool, goal_id=private, candidates=[f"{big}|claude-code"], cfg=CFG,
                            access_scope=AccessScope.for_user(f"alice-{run}")))["status"] == "ok"


@pytest.mark.asyncio
async def test_unknown_goal_and_empty_candidates_are_refused(pool):
    with pytest.raises(RoutingError):
        await recommend(pool, goal_id=str(uuid.uuid4()), candidates=["a|b"], access_scope=ANON)
    goal = await _goal(pool, f"e2e {_run_id()} refusal goal")
    with pytest.raises(RoutingError):
        await recommend(pool, goal_id=goal, candidates=[], access_scope=ANON)


@pytest.mark.asyncio
async def test_step_level_routing_end_to_end(pool):
    """Step attempts are fitted per (Procedure, step); a step recommendation uses the
    fitted step, conditions on the run's earlier steps, and with remaining steps its
    success is the whole run's; a reported step attempt refits the step locally."""
    run = _run_id()
    small, big = f"e2e{run}/small", f"e2e{run}/big"
    goal = await _goal(pool, f"e2e {run} add pagination to an api endpoint")
    proc = str(uuid.uuid4())
    await store.set_price(pool, small, input_per_mtok=0.1, output_per_mtok=0.4)
    await store.set_price(pool, big, input_per_mtok=5.0, output_per_mtok=25.0)
    rng = np.random.default_rng(3)
    true_d = {1: -1.0, 2: 0.3, 3: 1.2}
    rows = []
    for i in range(36):
        eps = 0.8 * rng.standard_normal()
        for order, d in true_d.items():
            for model, theta in ((small, 0.4), (big, 2.4)):
                p = 1 / (1 + np.exp(-(theta - d - eps)))
                rows.append({**_obs(goal, model, f"{run}-r{i}", bool(rng.random() < p), i // 6,
                                    tokens_in=8000, tokens_out=900),
                             "procedure_id": proc, "step_order": order,
                             "step_role": ("plan", "edit", "verify")[order - 1]})
    await store.insert_observations(pool, rows)
    await nightly_refit(pool, CFG, seed=5)
    steps_post = (await store.load_posteriors(pool, "procedure_steps", [proc]))[proc]
    assert steps_post["method"] == "joint"

    cands = [f"{small}|claude-code", f"{big}|claude-code"]
    base = dict(goal_id=goal, candidates=cands, access_scope=ANON, procedure_id=proc, cfg=CFG, record=False,
                instance_key=f"{run}-live")
    alone = await recommend(pool, step_order=2, step_role="edit", **base)
    assert alone["status"] == "ok" and alone["step"]["fitted"] is True and alone["step"]["step_role"] == "edit"
    with_rest = await recommend(pool, step_order=2, step_role="edit",
                                remaining_steps=[{"step_order": 3, "step_role": "verify"}], **base)
    rec = with_rest["recommended"]
    assert rec["p_success"] < rec["p_step_success"]              # the run also needs step 3
    after_fail = await recommend(pool, step_order=2, step_role="edit",
                                 previous_steps=[{"step_order": 1, "unit": f"{big}|claude-code", "accepted": False}],
                                 **base)
    assert (after_fail["p_correct_single_attempt"][f"{small}|claude-code"]
            < alone["p_correct_single_attempt"][f"{small}|claude-code"])   # a hard run: step 2 is harder too
    unseen = await recommend(pool, step_order=9, step_role="verify", **base)
    assert unseen["step"]["fitted"] is False                     # served from the role's prior

    obs_id = await record_observation(pool, {**_obs(goal, small, f"{run}-live", True, 40), "source": "live",
                                             "check_kind": "tests", "reporter": f"host-{run}", "procedure_id": proc,
                                             "step_order": 2, "step_role": "edit"})
    assert obs_id
    await local_refit(pool, goal, CFG, seed=6)
    assert (await store.load_posteriors(pool, "procedure_steps", [proc]))[proc]["method"] == "local_nuts"

    with pytest.raises(RoutingError):
        await recommend(pool, goal_id=goal, candidates=cands, access_scope=ANON, step_order=1, cfg=CFG)
    with pytest.raises(store.ObservationRejected):
        await record_observation(pool, {**_obs(goal, small, "x", True, 41), "step_order": 1})   # no procedure
