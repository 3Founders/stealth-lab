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
                    cfg: RoutingDefaults = DEFAULTS, record: bool = True,
                    step_order: Optional[int] = None, step_role: Optional[str] = None,
                    previous_steps: Sequence[Mapping[str, Any]] = (),
                    remaining_steps: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The ladder to run for one instance of `goal_id` (docs/model_routing_plan.md §6-§7, §10).

    Task level (step_order None): a ladder for the whole task.
    Step level: a ladder for ONE step of `procedure_id`'s run. `previous_steps` are the
    run's earlier steps (they share the run's difficulty, so a failure there informs
    this step); `remaining_steps` are the steps still to come, so the target and the
    value are the WHOLE run's (one-step rollout, see ladder.py)."""
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
    step_level = step_order is not None
    if (step_level or previous_steps or remaining_steps) and not procedure_id:
        raise RoutingError("step-level routing needs the procedure_id whose steps these are")
    if not step_level and (previous_steps or remaining_steps):
        raise RoutingError("previous_steps / remaining_steps need step_order (the step being routed)")

    stored_goal = (await store.load_posteriors(pool, "goal", [goal_id])).get(goal_id)
    # Use the global draws the Goal's posterior was fitted against, so every quantity
    # below comes from one consistent joint posterior.
    g = await _globals(pool, stored_goal["version"]) if stored_goal else await _globals(pool)
    if g is None:
        return {"status": "not_ready", "reason": "no fitted model yet: the nightly refit (admin routing-refit) "
                                                  "has never run", "goal_id": goal_id}
    if step_level and "mu_role" not in g.arrays:
        return {"status": "not_ready", "reason": "the fitted parameters predate step-level routing: run "
                                                  "admin routing-refit once", "goal_id": goal_id}
    now = now or predict.utc_now()
    instance_key = instance_key or str(uuid.uuid4())
    rng = np.random.default_rng(_seed(goal_id, instance_key, step_order, len(previous_attempts),
                                      len(previous_steps), g.version))

    from app.routing.fit import aligned_goal_draws

    goal_x = (predict.unpack(stored_goal["draws"])["x"] if stored_goal and stored_goal["version"] == g.version
              else await aligned_goal_draws(pool, g, goal_id, rng=rng))
    proc_c, stored_steps = None, None
    if procedure_id:
        posts = await store.load_posteriors(pool, "procedure", [procedure_id])
        stored_proc = posts.get(procedure_id)
        proc_c = (predict.unpack(stored_proc["draws"])["c"] if stored_proc and stored_proc["version"] == g.version
                  else predict.procedure_prior_draws(g, rng))
        if step_level:
            steps_row = (await store.load_posteriors(pool, "procedure_steps", [procedure_id])).get(procedure_id)
            if steps_row and steps_row["version"] == g.version:
                stored_steps = predict.unpack(steps_row["draws"])

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

    # ---- columns: (unit, item); item None = the whole task, int = that step's order
    item_cache: dict[Optional[int], tuple[np.ndarray, np.ndarray]] = {}
    roles: dict[int, str] = {}

    def item_terms(order: Optional[int], role: Optional[str] = None) -> tuple[np.ndarray, np.ndarray]:
        if order not in item_cache:
            if order is None:
                item_cache[order] = (np.zeros(g.draws), np.zeros((g.draws, g.k)))
            else:
                d, e, used_role = predict.step_draws(g, stored_steps, int(order), role, rng)
                item_cache[order] = (d, e)
                roles[int(order)] = used_role
        return item_cache[order]

    current = int(step_order) if step_level else None
    item_terms(current, step_role)
    columns: list[tuple[tuple[str, str], Optional[int]]] = [(u, current) for u in usable]
    attempt_specs = []
    for a in previous_attempts:
        attempt_specs.append((a, current))
    for a in previous_steps:
        if a.get("step_order") is None:
            raise RoutingError("every previous_steps entry needs its step_order")
        order = int(a["step_order"])
        item_terms(order, a.get("step_role"))
        attempt_specs.append((a, order))
    attempts_cols = []
    for a, order in attempt_specs:
        unit = _parse_units([a.get("unit") or {"model": a.get("model"), "scaffold": a.get("scaffold"),
                                               "version": a.get("version")}])[0]
        if (unit, order) not in columns:
            columns.append((unit, order))
        attempts_cols.append((a, columns.index((unit, order))))

    predecessors = {m: r.get("predecessor") for m, r in registry.items()}
    distinct_units = list(dict.fromkeys(u for u, _ in columns))
    base_u, z_u = predict.unit_terms(g, distinct_units, predecessors, now, rng)

    def success(cols: Sequence[tuple[tuple[str, str], Optional[int]]]) -> tuple[np.ndarray, np.ndarray]:
        idx = [distinct_units.index(u) for u, _ in cols]
        item_d = np.stack([item_terms(o)[0] for _, o in cols], axis=1)
        item_e = np.stack([item_terms(o)[1] for _, o in cols], axis=1)
        return predict.success_given_eps(g, goal_x, proc_c, base_u[:, idx], z_u[:, idx, :],
                                         eps_nodes=cfg.gh_eps_nodes, item_d=item_d, item_e=item_e)

    p, eps_w = success(columns)

    token_meta = g.meta.get("tokens_step" if step_level else "tokens", {})
    goal_tokens = await store.goal_token_stats(pool, goal_id, steps=step_level)
    cost_ok, cost_fail = np.zeros(len(columns)), np.zeros(len(columns))
    for i, (u, _order) in enumerate(columns[:len(usable)]):          # earlier attempts' costs are sunk
        key = unit_id(*u)
        for outcome, target in (("1", cost_ok), ("0", cost_fail)):
            target[i] = costs.dollars(prices[u[0]], *costs.expected_tokens(
                outcome, token_meta.get("global", {}), token_meta.get("units", {}).get(key), goal_tokens.get(key), cfg))

    alpha, beta = predict.check_rates(g, check_kind)
    attempts = []
    for a, col in attempts_cols:
        a_alpha, a_beta = predict.check_rates(g, a.get("check_kind") or check_kind)
        attempts.append(ladder.Attempt(col, bool(a.get("accepted")), a_alpha, a_beta))
    node_w, draw_w = ladder.belief(eps_w, p, attempts)
    max_rungs = int(constraints.get("max_rungs") or cfg.max_rungs)

    # ---- the rest of the run: P(every later step succeeds | eps) under a base policy
    continuation, later = None, []
    for r in remaining_steps:
        if r.get("step_order") is None:
            raise RoutingError("every remaining_steps entry needs its step_order")
        if current is not None and int(r["step_order"]) == current:
            continue
        later.append(r)
    if later:
        continuation = np.ones_like(node_w)
        for r in later:
            order = int(r["step_order"])
            item_terms(order, r.get("step_role"))
            p_r, _ = success([(u, order) for u in usable])
            continuation = continuation * _base_policy_node_ok(p_r, node_w, draw_w, alpha, beta, max_rungs)

    step_scale = 1 + len(later)
    value = float(constraints.get("value_usd") or cfg.value_multiplier * max(cost_ok[:len(usable)]) * step_scale)
    wrong_penalty = float(constraints.get("wrong_penalty_usd") or cfg.wrong_penalty_ratio * value)
    try:
        result = ladder.choose(
            p, eps_w, alpha, beta, cost_ok, cost_fail, cost_check=float(constraints.get("check_cost_usd") or 0.0),
            value=value, wrong_penalty=wrong_penalty, candidates=range(len(usable)), max_rungs=max_rungs,
            rho=float(constraints.get("reliability_target") or cfg.reliability_target),
            confidence=float(constraints.get("reliability_confidence") or cfg.reliability_confidence),
            attempts=attempts, rng=rng, continuation=continuation, node_weights_after=(node_w, draw_w),
            max_cost=None if constraints.get("max_cost_usd") is None else float(constraints["max_cost_usd"]))
    except ValueError as exc:
        raise RoutingError(str(exc)) from exc

    def describe(i: int) -> dict[str, Any]:
        return {"ladder": [unit_id(*columns[u][0]) for u in result.ladders[i]], **result.summary(i)}

    chosen = describe(result.chosen)
    feasible = [i for i in np.argsort(-(result.utility @ result.draw_weights)) if result.feasible[i]]
    alternatives = [describe(int(i)) for i in feasible if int(i) != result.chosen][:4]
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
                         "goal_id or procedure_id, check_kind, tokens..., recommendation_id, attempt_index"
                         + (", step_order, step_role)" if step_level else ")"),
    }
    if step_level:
        response["step"] = {"step_order": current, "step_role": roles.get(current),
                            "fitted": stored_steps is not None and current in [int(o) for o in stored_steps["orders"]],
                            "remaining_steps": len(later), "previous_steps": len(previous_steps),
                            "p_success_is": "the whole run: this step and every remaining step"
                            if later else "this step (no remaining steps given)"}
    if record:
        await store.record_decision(pool, {
            "id": recommendation_id, "goal_id": goal_id, "procedure_id": procedure_id, "instance_key": instance_key,
            "params_version": g.version, "candidates": [unit_id(*u) for u in usable], "ladder": chosen["ladder"],
            "propensity": result.propensity, "meets_target": result.meets_target,
            "predicted": {"recommended": chosen, "alternatives": alternatives},
            "constraints": {**constraints, "check_kind": check_kind, "previous_attempts": len(previous_attempts),
                            "previous_steps": len(previous_steps), "remaining_steps": len(later)},
            "visibility": goal["visibility"], "owner_id": goal["owner_id"], "step_order": current})
    return response


def _base_policy_node_ok(p: np.ndarray, node_w: np.ndarray, draw_w: np.ndarray, alpha: np.ndarray,
                         beta: np.ndarray, max_rungs: int, top: int = 3) -> np.ndarray:
    """(S, N) P(a later step succeeds | eps) under its base policy: the most reliable
    ladder over its three most reliable units (the rollout's reference behaviour for
    the rest of the run; each later step is re-solved properly when its turn comes)."""
    single = (p * node_w[:, None, :]).sum(axis=2).T @ draw_w          # (U,) expected single-attempt success
    best_units = list(np.argsort(-single)[:min(top, p.shape[1])])
    best, best_ok, best_nodes = None, -1.0, None
    for lad in ladder.enumerate_ladders(len(best_units), max_rungs):
        units = [int(best_units[i]) for i in lad]
        nodes = ladder.ladder_node_ok(p, alpha, beta, units)
        expected = float(draw_w @ (nodes * node_w).sum(axis=1))
        if expected > best_ok:
            best, best_ok, best_nodes = units, expected, nodes
    return best_nodes


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
