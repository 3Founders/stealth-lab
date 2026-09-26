"""What the MCP tool / report_execution call. numpy only (no JAX in the API process)."""
from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from app.routing import costs, ladder, predict, store
from app.routing.config import CHECK_KINDS, DEFAULTS, RoutingDefaults, split_unit, unit_id
from app.services.access import AccessScope


class RoutingError(ValueError):
    pass


@lru_cache(maxsize=4)
def _globals_cached(version: int, blob: bytes, meta_json: str) -> predict.Globals:
    import json

    return predict.load_globals(version, blob, json.loads(meta_json))


async def _globals(pool: Any, version: Optional[int] = None) -> Optional[predict.Globals]:
    import json

    row = await (store.params_version(pool, version) if version is not None else store.active_params(pool))
    if row is None:
        return None
    return _globals_cached(row["version"], row["draws"], json.dumps(row["meta"], sort_keys=True))


def _seed(*parts: Any) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def _parse_units(raw: Sequence[Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            pair = split_unit(item)
        elif isinstance(item, Mapping) and item.get("model") and item.get("scaffold"):
            model = str(item["model"]) + (f"@{item['version']}" if item.get("version") else "")
            pair = (model, str(item["scaffold"]))
        else:
            raise RoutingError(f"a unit is 'model|scaffold' or {{model, scaffold}}; got {item!r}")
        if pair not in out:
            out.append(pair)
    return out


async def recommend(pool: Any, *, goal_id: str, candidates: Sequence[Any], access_scope: AccessScope,
                    procedure_id: Optional[str] = None, check_kind: Optional[str] = None,
                    instance_key: Optional[str] = None, previous_attempts: Sequence[Mapping[str, Any]] = (),
                    constraints: Optional[Mapping[str, Any]] = None, now: Optional[datetime] = None,
                    cfg: RoutingDefaults = DEFAULTS, record: bool = True) -> dict[str, Any]:
    """The ladder to run for one instance of `goal_id` (docs/model_routing_plan.md §6-§7)."""
    constraints = dict(constraints or {})
    goal = await store.visible_goal(pool, goal_id, access_scope)
    if goal is None:
        raise RoutingError(f"goal {goal_id} not found")
    units = _parse_units(candidates)
    if not units:
        raise RoutingError("give at least one candidate unit ('model|scaffold') you can run")
    if len(units) > cfg.max_candidates:
        raise RoutingError(f"at most {cfg.max_candidates} candidate units per request")
    check_kind = check_kind or cfg.default_check_kind
    if check_kind not in CHECK_KINDS:
        raise RoutingError(f"check_kind must be one of {CHECK_KINDS}")

    stored_goal = (await store.load_posteriors(pool, "goal", [goal_id])).get(goal_id)
    # Use the global draws the Goal's posterior was fitted against, so every quantity
    # below comes from one consistent joint posterior.
    g = await _globals(pool, stored_goal["version"]) if stored_goal else await _globals(pool)
    if g is None:
        return {"status": "not_ready", "reason": "no fitted model yet: the nightly refit (admin routing-refit) "
                                                  "has never run", "goal_id": goal_id}
    now = now or predict.utc_now()
    instance_key = instance_key or str(uuid.uuid4())
    rng = np.random.default_rng(_seed(goal_id, instance_key, len(previous_attempts), g.version))

    from app.routing.fit import aligned_goal_draws

    goal_x = (predict.unpack(stored_goal["draws"])["x"] if stored_goal and stored_goal["version"] == g.version
              else await aligned_goal_draws(pool, g, goal_id, rng=rng))
    proc_c = None
    if procedure_id:
        stored_proc = (await store.load_posteriors(pool, "procedure", [procedure_id])).get(procedure_id)
        proc_c = (predict.unpack(stored_proc["draws"])["c"] if stored_proc and stored_proc["version"] == g.version
                  else predict.procedure_prior_draws(g, rng))

    registry = await store.model_registry(pool)
    prices = await store.current_prices(pool, [m for m, _ in units])
    usable, excluded = [], []
    for m, s in units:
        flags = registry.get(m, {})
        if m not in prices:
            excluded.append({"unit": unit_id(m, s), "reason": "no price in routing_prices (admin routing-price)"})
        elif constraints.get("open_weights_only") and not flags.get("open_weights"):
            excluded.append({"unit": unit_id(m, s), "reason": "not registered as open-weights"})
        elif constraints.get("local_only") and not flags.get("local"):
            excluded.append({"unit": unit_id(m, s), "reason": "not registered as running locally"})
        else:
            usable.append((m, s))
    if not usable:
        return {"status": "no_usable_candidates", "excluded": excluded, "goal_id": goal_id}

    attempts_in = [dict(a) for a in previous_attempts]
    attempt_units = _parse_units([a.get("unit") or {"model": a.get("model"), "scaffold": a.get("scaffold"),
                                                     "version": a.get("version")} for a in attempts_in])
    all_units = usable + [u for u in attempt_units if u not in usable]
    predecessors = {m: r.get("predecessor") for m, r in registry.items()}
    base, z = predict.unit_terms(g, all_units, predecessors, now, rng)
    p, eps_w = predict.success_given_eps(g, goal_x, proc_c, base, z, eps_nodes=cfg.gh_eps_nodes)

    token_meta = g.meta.get("tokens", {})
    goal_tokens = await store.goal_token_stats(pool, goal_id)
    cost_ok, cost_fail = np.zeros(len(all_units)), np.zeros(len(all_units))
    for i, (m, s) in enumerate(all_units):
        if m not in prices:
            continue                                           # an earlier attempt's unit: its cost is sunk
        key = unit_id(m, s)
        for outcome, target in (("1", cost_ok), ("0", cost_fail)):
            target[i] = costs.dollars(prices[m], *costs.expected_tokens(
                outcome, token_meta.get("global", {}), token_meta.get("units", {}).get(key), goal_tokens.get(key), cfg))

    value = float(constraints.get("value_usd") or cfg.value_multiplier * max(cost_ok[:len(usable)]))
    wrong_penalty = float(constraints.get("wrong_penalty_usd") or cfg.wrong_penalty_ratio * value)
    alpha, beta = predict.check_rates(g, check_kind)
    attempts = []
    for a, unit in zip(attempts_in, attempt_units):
        a_alpha, a_beta = predict.check_rates(g, a.get("check_kind") or check_kind)
        attempts.append(ladder.Attempt(all_units.index(unit), bool(a.get("accepted")), a_alpha, a_beta))
    try:
        result = ladder.choose(
            p, eps_w, alpha, beta, cost_ok, cost_fail, cost_check=float(constraints.get("check_cost_usd") or 0.0),
            value=value, wrong_penalty=wrong_penalty, candidates=range(len(usable)),
            max_rungs=int(constraints.get("max_rungs") or cfg.max_rungs),
            rho=float(constraints.get("reliability_target") or cfg.reliability_target),
            confidence=float(constraints.get("reliability_confidence") or cfg.reliability_confidence),
            attempts=attempts, rng=rng,
            max_cost=None if constraints.get("max_cost_usd") is None else float(constraints["max_cost_usd"]))
    except ValueError as exc:
        raise RoutingError(str(exc)) from exc

    def describe(i: int) -> dict[str, Any]:
        return {"ladder": [unit_id(*all_units[u]) for u in result.ladders[i]], **result.summary(i)}

    chosen = describe(result.chosen)
    feasible = [i for i in np.argsort(-(result.utility @ result.draw_weights)) if result.feasible[i]]
    alternatives = [describe(int(i)) for i in feasible if int(i) != result.chosen][:4]
    node_w, _ = ladder.belief(eps_w, p, attempts)
    single = {unit_id(*usable[u]): float(result.draw_weights @ (p[:, u, :] * node_w).sum(axis=1))
              for u in range(len(usable))}
    recommendation_id = str(uuid.uuid4())
    response = {
        "status": "ok", "recommendation_id": recommendation_id, "instance_key": instance_key, "goal_id": goal_id,
        "procedure_id": procedure_id, "params_version": g.version,
        "recommended": chosen, "meets_reliability_target": result.meets_target,
        "reliability_target": float(constraints.get("reliability_target") or cfg.reliability_target),
        "alternatives": alternatives, "p_correct_single_attempt": single,
        "value_usd": value, "wrong_penalty_usd": wrong_penalty, "check_kind": check_kind,
        "propensity": result.propensity, "excluded": excluded,
        "evidence": {"goal_observations": stored_goal["n_observations"] if stored_goal else 0,
                     "goal_posterior": (stored_goal or {}).get("method") or "prior (parents + embedding)"},
        "how_to_report": "after each rung, call report_model_run(model, scaffold, accepted, instance_key, "
                         "goal_id or procedure_id, check_kind, tokens..., recommendation_id, attempt_index)",
    }
    if record:
        await store.record_decision(pool, {
            "id": recommendation_id, "goal_id": goal_id, "procedure_id": procedure_id, "instance_key": instance_key,
            "params_version": g.version, "candidates": [unit_id(*u) for u in usable], "ladder": chosen["ladder"],
            "propensity": result.propensity, "meets_target": result.meets_target,
            "predicted": {"recommended": chosen, "alternatives": alternatives},
            "constraints": {**constraints, "check_kind": check_kind, "previous_attempts": len(attempts)},
            "visibility": goal["visibility"], "owner_id": goal["owner_id"]})
    return response


async def record_observation(pool: Any, obs: Mapping[str, Any], *, enqueue_refit: bool = True) -> str:
    """One attempt outcome -> project B, and a local refit of its Goal queued."""
    ids = await store.insert_observations(pool, [obs])
    if enqueue_refit:
        await store.enqueue_local_refit(pool, str(obs["goal_id"]), ids[0], visibility=obs.get("visibility") or "public",
                                        owner_id=obs.get("owner_id"))
    return ids[0]


def token_summary(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Global and per-unit log-token means (the pooled levels above the Goal)."""
    acc: dict[tuple[str, str], list[list[float]]] = {}
    for o in observations:
        if o.get("tokens_in") is None or o.get("tokens_out") is None:
            continue
        row = [math.log(max(o["tokens_in"], 1)), math.log(max(o["tokens_out"], 1)),
               math.log(1 + (o.get("tokens_cached") or 0))]
        outcome = "1" if o["accepted"] else "0"
        acc.setdefault(("_global", outcome), []).append(row)
        acc.setdefault((unit_id(o["model_key"], o["scaffold"]), outcome), []).append(row)
    out: dict[str, Any] = {"global": {}, "units": {}}
    for (key, outcome), rows in acc.items():
        arr = np.asarray(rows)
        stats = [float(len(rows)), *map(float, arr.mean(axis=0))]
        if key == "_global":
            out["global"][outcome] = stats
        else:
            out["units"].setdefault(key, {})[outcome] = stats
    return out
