"""What the MCP tool / report_execution call. numpy only (no JAX in the API process)."""
from __future__ import annotations

import hashlib
import math
import re
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
    """The fitted global draws: the database's (a given version, or the active one), else the bundled public
    prior (app/routing/prior_bundle.py) -- so routing works before any production fit has run."""
    import json

    from app.routing import prior_bundle

    if version == prior_bundle.BUNDLE_VERSION:
        return prior_bundle.load_globals()
    row = await (store.params_version(pool, version) if version is not None else store.active_params(pool))
    if row is None:
        return prior_bundle.load_globals() if version is None else None
    return _globals_cached(row["version"], row["draws"], json.dumps(row["meta"], sort_keys=True))


async def _card_rows(pool: Any) -> list[dict]:
    """Model cards: the bundled ones, with the database's rows taking precedence for the same model."""
    from app.routing import prior_bundle

    rows = {r["model_key"]: dict(r) for r in prior_bundle.card_rows()}
    for r in await store.model_cards(pool):            # field by field: a database row lacks the cache-read price
        merged = rows.setdefault(r["model_key"], {})
        merged.update({k: v for k, v in dict(r).items() if v is not None})
    return fill_twin_prices(list(rows.values()))


PRICE_FIELDS = ("price_in", "price_out", "price_cached", "context_k")


def fill_twin_prices(rows: list[dict]) -> list[dict]:
    """One model can have two cards: one from the public results that measured it (no price) and one from a price
    list, spelled differently (`qwen3-coder-480b-a35b` vs OpenRouter's `qwen/qwen3-coder` with the alias
    `qwen/qwen3-coder-480b-a35b-07-25`). A card without a price takes the missing fields from a priced card whose key
    or aliases name the same model, so the measured model is not dropped as unpriceable."""
    from app.routing.cards import canonical_key

    def names(r: dict) -> set[str]:
        out = set()
        for n in [r.get("model_key"), *(r.get("aliases") or [])]:
            if n:
                k = canonical_key(str(n))
                out |= {k, re.sub(r"-\d{2}-\d{2}$", "", k)}     # a short "-07-25" release suffix too
        return out

    priced = [(names(r), r) for r in rows if r.get("price_in") is not None and r.get("price_out") is not None]
    for r in rows:
        if r.get("price_in") is not None:
            continue
        mine = names(r)
        twin = next((p for n, p in priced if mine & n), None)
        if twin is not None:
            for f in PRICE_FIELDS:
                if r.get(f) is None and twin.get(f) is not None:
                    r[f] = twin[f]
    return rows


# A plan without a stored Goal ("virtual"): the task resolved to no global Goal, so it is routed on a key of its own
# -- this repository's library entry, this repository as a whole, or the generic coding task -- with a prior made
# for that case (prior_draws_for_case). Its decisions and attempts are logged under that key like any Goal's, so
# report_result, the repository's routing.md counts and later fits all work the same way.
VIRTUAL_KEY = "_virtual"
VIRTUAL_KINDS = ("library", "repo", "generic")


def virtual_goal_id(kind: str, ref: str = "") -> str:
    """A stable UUID for a virtual key (uuid5), so the same library entry or repository always maps to one log."""
    if kind not in VIRTUAL_KINDS:
        raise RoutingError(f"virtual goal kind must be one of {VIRTUAL_KINDS}")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stealthlab:routing:{kind}:{ref}"))


async def prior_draws_for_case(pool: Any, g: predict.Globals, virtual: Mapping[str, Any],
                               rng: np.random.Generator) -> np.ndarray:
    """(S, D) prior draws of a virtual Goal, set by its case:
      * its structural features (the fix size of the library entry, or this repository's typical fix) move the
        difficulty through the fitted regression W -- no features means the population mean;
      * `parents` (global Goals it is close to: find_ways' suggested candidate) pull it toward what is known
        about them, exactly as a Goal's parents do."""
    from app.routing.fit import aligned_goal_draws

    parents = []
    code_draws = _code_parent(g, virtual.get("code"))
    if code_draws is not None:          # the Ways like this one (same semantic code) -- semantic_codes.py
        parents.append((code_draws, g.phi1(None, None)))
    for pid in list(virtual.get("parents") or [])[:3]:
        try:
            row = (await store.goal_rows(pool, [str(pid)])).get(str(pid))
        except Exception:  # noqa: BLE001 -- a parent that cannot be read only weakens the prior
            row = None
        if row is None:
            continue
        parents.append((await aligned_goal_draws(pool, g, str(pid), rng=rng),
                        g.phi1(row.get("embedding"), row.get("features"))))
    return predict.goal_prior_draws(g, g.phi1(None, virtual.get("features")), parents, rng)


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


async def recommend(pool: Any, *, access_scope: AccessScope, **kwargs: Any) -> dict[str, Any]:
    """`_recommend` under the caller's routing row-level-security scope (migration 150)."""
    with store.routing_scope(access_scope):
        return await _recommend(pool, access_scope=access_scope, **kwargs)


async def _recommend(pool: Any, *, goal_id: str, candidates: Sequence[Any], access_scope: AccessScope,
                    procedure_id: Optional[str] = None, check_kind: Optional[str] = None,
                    instance_key: Optional[str] = None, previous_attempts: Sequence[Mapping[str, Any]] = (),
                    constraints: Optional[Mapping[str, Any]] = None, now: Optional[datetime] = None,
                    cfg: RoutingDefaults = DEFAULTS, record: bool = True,
                    step_order: Optional[int] = None, step_role: Optional[str] = None,
                    previous_steps: Sequence[Mapping[str, Any]] = (),
                    remaining_steps: Sequence[Mapping[str, Any]] = (),
                    local_obs: Sequence[Mapping[str, Any]] = (),
                    virtual: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The ladder to run for one instance of `goal_id` (docs/model_routing_plan.md §6-§7, §10).

    Task level (step_order None): a ladder for the whole task.
    Step level: a ladder for ONE step of `procedure_id`'s run. `previous_steps` are the
    run's earlier steps (they share the run's difficulty, so a failure there informs
    this step); `remaining_steps` are the steps still to come, so the target and the
    value are the WHOLE run's (one-step rollout, see ladder.py).

    `local_obs` ([{unit, n, ok}], the caller's `.stealth/routing.md` OBS counts) are earlier
    instances of this Goal in the caller's own repository: see `local_obs_loglik`."""
    constraints = dict(constraints or {})
    virtual = virtual or constraints.pop(VIRTUAL_KEY, None)
    if virtual is not None:
        if virtual.get("kind") not in VIRTUAL_KINDS:
            raise RoutingError(f"virtual goal kind must be one of {VIRTUAL_KINDS}")
        viewer = None if access_scope.viewer_id is None else str(access_scope.viewer_id)
        goal = {"visibility": "private" if viewer else "public", "owner_id": viewer, "tenant_id": None}
    else:
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

    stored_goal = None if virtual is not None else (
        await store.load_posteriors(pool, "goal", [goal_id])).get(goal_id)
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

    if virtual is not None:
        goal_x = await prior_draws_for_case(pool, g, virtual, rng)
    else:
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
    from app.routing.cards import load_cards, resolve

    cards = load_cards(await _card_rows(pool))
    prices = await store.current_prices(pool, [m for m, _ in units])
    list_priced = []
    for m, _s in units:                  # no contracted price yet: the card's public list price
        card = resolve(m, cards)
        if m not in prices and card is not None and card.price_in is not None and card.price_out is not None:
            prices[m] = costs.Price(card.price_in, card.price_out, card.price_cached)
            list_priced.append(m)
    usable, excluded = [], []
    for m, s in units:
        flags = registry.get(m, {})
        if m not in prices:
            excluded.append({"unit": unit_id(m, s), "reason": "no price in routing_prices (admin routing-price) "
                                                              "and no model card with a list price"})
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
    local_cols: list[tuple[int, int, int]] = []        # (column, attempts, accepted) of the repo's own past instances
    for o in list(local_obs)[:cfg.max_candidates]:
        unit = _parse_units([o["unit"]])[0]
        item_terms(None)                                  # a whole-task outcome, also on a step-level call
        if (unit, None) not in columns:
            columns.append((unit, None))
        local_cols.append((columns.index((unit, None)), int(o["n"]), int(o["ok"])))

    predecessors = {m: r.get("predecessor") for m, r in registry.items()}
    distinct_units = list(dict.fromkeys(u for u, _ in columns))
    updates = await store.model_updates(pool, g.version)
    base_u, z_u = predict.unit_terms(g, distinct_units, predecessors, now, rng, cards=cards, updates=updates)

    def success(cols: Sequence[tuple[tuple[str, str], Optional[int]]]) -> tuple[np.ndarray, np.ndarray]:
        idx = [distinct_units.index(u) for u, _ in cols]
        item_d = np.stack([item_terms(o)[0] for _, o in cols], axis=1)
        item_e = np.stack([item_terms(o)[1] for _, o in cols], axis=1)
        return predict.success_given_eps(g, goal_x, proc_c, base_u[:, idx], z_u[:, idx, :],
                                         eps_nodes=cfg.gh_eps_nodes, item_d=item_d, item_e=item_e)

    p, eps_w = success(columns)

    token_meta = g.meta.get("tokens_step" if step_level else "tokens", {})
    goal_tokens = await store.goal_token_stats(pool, goal_id, steps=step_level)
    # This Goal's attempts pooled over every unit: how big ITS tasks are. A unit with no attempts here is sized from
    # this, not from the public benchmark's task size -- otherwise a unit that ran once on small tasks looks far
    # cheaper than every untried one, which is then never tried (and never learned).
    pooled_tokens = _pool_unit_stats(goal_tokens)
    goal_costs = await store.goal_cost_stats(pool, goal_id, steps=step_level)
    cost_ok, cost_fail = np.zeros(len(columns)), np.zeros(len(columns))
    for i, (u, _order) in enumerate(columns[:len(usable)]):          # earlier attempts' costs are sunk
        key = unit_id(*u)
        for outcome, target in (("1", cost_ok), ("0", cost_fail)):
            target[i] = costs.dollars(prices[u[0]], *costs.expected_tokens(
                outcome, token_meta.get("global", {}), _unit_tokens(token_meta, u[0], key, cards),
                goal_tokens.get(key) or pooled_tokens, cfg))
            # what this unit's attempts here REALLY cost, when reported: blended with the token estimate, which
            # counts as COST_PRIOR_ATTEMPTS attempts (a few reports and the measured dollars decide)
            seen = (goal_costs.get(key) or {}).get(outcome) or (goal_costs.get(key) or {}).get("1" if outcome == "0" else "0")
            if seen and seen[0] > 0:
                target[i] = (seen[0] * seen[1] + COST_PRIOR_ATTEMPTS * target[i]) / (seen[0] + COST_PRIOR_ATTEMPTS)

    # the caller's session pays to hand an attempt to a SUBAGENT of its own scaffold (another Claude Code model): it
    # reads, delegates and checks. A unit on another scaffold (an open model through the local executor) is
    # dispatched before the session runs (lib/dispatch.mjs) and costs the session nothing.
    baseline_unit = constraints.get("reliability_baseline")
    if baseline_unit:
        handoff = float(constraints["handoff_cost_usd"] if constraints.get("handoff_cost_usd") is not None
                        else cfg.handoff_cost_usd)
        own_scaffold = split_unit(str(baseline_unit))[1]
        for i, (u, _order) in enumerate(columns[:len(usable)]):
            if unit_id(*u) != str(baseline_unit) and u[1] == own_scaffold:
                cost_ok[i] += handoff
                cost_fail[i] += handoff

    alpha, beta = predict.check_rates(g, check_kind)
    attempts = []
    for a, col in attempts_cols:
        a_alpha, a_beta = predict.check_rates(g, a.get("check_kind") or check_kind)
        attempts.append(ladder.Attempt(col, bool(a.get("accepted")), a_alpha, a_beta))
    node_w, draw_w = ladder.belief(eps_w, p, attempts)
    local_evidence = None
    if local_cols:
        draw_w, local_evidence = reweight_by_local_obs(draw_w, local_obs_loglik(p, eps_w, alpha, beta, local_cols))
        local_evidence.update(attempts=sum(n for _, n, _ in local_cols), accepted=sum(k for _, _, k in local_cols),
                              units=len(local_cols))
        # Re-weighting can only choose among the prior's draws, so when this workspace's truth lies outside them
        # (a model much better here than on the public benchmarks) it cannot get there. Each unit's level is also
        # moved to a Beta-binomial posterior in which the prior counts as `local_prior_strength` attempts: with
        # no local data the prior decides; after ~10 attempts this workspace's own record does.
        p = shift_to_local_rates(p, eps_w, draw_w, alpha, beta, local_cols,
                                 float(constraints.get("local_prior_strength") or cfg.local_prior_strength))
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
    rho: Any = float(constraints.get("reliability_target") or cfg.reliability_target)
    target_info: dict[str, Any] = {"mode": "fixed", "target": rho}
    baseline = constraints.get("reliability_baseline")
    if baseline:
        # matched to baseline: per draw, the baseline unit alone (one attempt, judged by the same check, times the
        # rest of the run) minus the tolerance. A baseline that is not a usable candidate falls back to the fixed rho.
        b = next((i for i, (m, s) in enumerate(usable) if unit_id(m, s) == str(baseline)), None)
        if b is not None:
            tol = float(constraints.get("reliability_tolerance") if constraints.get("reliability_tolerance")
                        is not None else cfg.reliability_tolerance)
            base_nodes = ladder.ladder_node_ok(p, alpha, beta, [b])
            if continuation is not None:
                base_nodes = base_nodes * continuation
            base_ok = (base_nodes * node_w).sum(axis=1)                                   # (S,)
            rho = np.clip(base_ok - tol, 0.0, 1.0)
            target_info = {"mode": "matched", "baseline": str(baseline), "tolerance": tol,
                           "baseline_p_ok": round(float(draw_w @ base_ok), 4),
                           "target": round(float(draw_w @ rho), 4)}
        else:
            target_info = {"mode": "fixed", "target": rho, "note": f"baseline {baseline} is not a usable candidate"}
    try:
        result = ladder.choose(
            p, eps_w, alpha, beta, cost_ok, cost_fail, cost_check=float(constraints.get("check_cost_usd") or 0.0),
            value=value, wrong_penalty=wrong_penalty, candidates=range(len(usable)), max_rungs=max_rungs,
            rho=rho,
            confidence=float(constraints.get("reliability_confidence") or cfg.reliability_confidence),
            attempts=attempts, rng=rng, continuation=continuation, node_weights_after=(node_w, draw_w),
            allow_repeats=bool(constraints.get("allow_retries", True)),
            max_repeats=int(constraints.get("max_repeats") or cfg.max_repeats),
            feasibility=str(constraints.get("feasibility") or cfg.feasibility),
            end_with=(next((i for i, (m, s) in enumerate(usable) if unit_id(m, s) == str(baseline)), None)
                      if baseline and constraints.get("end_with_baseline", True) else None),
            explore_first=_explore_unit(usable, local_obs, baseline, cost_ok, rng,
                                        int(constraints.get("explore_min_attempts", cfg.explore_min_attempts))),
            exclude_units=[col for a, col in attempts_cols if col < len(usable)] if not constraints.get(
                "allow_retries", True) else (),
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
    unit_stats = {}
    for u in range(len(usable)):                 # per-unit single-attempt success: mean and 90% interval over draws
        per_draw = (p[:, u, :] * node_w).sum(axis=1)
        q05, q95 = _weighted_quantiles(per_draw, result.draw_weights, (0.05, 0.95))
        mean = single[unit_id(*usable[u])]
        unit_stats[unit_id(*usable[u])] = {"p_ok_mean": round(mean, 4), "p_ok_q05": round(q05, 4),
                                           "p_ok_q95": round(q95, 4),
                                           "cost_mean": round(float(mean * cost_ok[u] + (1 - mean) * cost_fail[u]), 6)}
    recommendation_id = str(uuid.uuid4())
    response = {
        "status": "ok", "recommendation_id": recommendation_id, "instance_key": instance_key, "goal_id": goal_id,
        "procedure_id": procedure_id, "params_version": g.version, "as_of": g.meta.get("fitted_at"),
        "recommended": chosen, "meets_reliability_target": result.meets_target,
        "reliability_target": target_info["target"], "reliability": target_info,
        "alternatives": alternatives, "p_correct_single_attempt": single, "units": unit_stats,
        "value_usd": value, "wrong_penalty_usd": wrong_penalty, "check_kind": check_kind,
        "propensity": result.propensity, "excluded": excluded,
        "evidence": {"goal_observations": stored_goal["n_observations"] if stored_goal else 0,
                     "goal_posterior": (stored_goal or {}).get("method") or (
                         _case_label(virtual) if virtual is not None else "prior (parents + embedding)"),
                     **({"case": {k: virtual.get(k) for k in ("kind", "ref") if virtual.get(k)}}
                        if virtual is not None else {}),
                     **({"prior": "bundled public prior"} if g.meta.get("bundled") else {}),
                     "models": {unit_id(m, s): _model_basis(g, m, registry, cards) for m, s in usable},
                     **({"list_priced": list_priced} if list_priced else {}),
                     **({"local": local_evidence} if local_evidence else {})},
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
                            "previous_steps": len(previous_steps), "remaining_steps": len(later),
                            # counts only (unit, n, ok): report_result re-solves the next rung with them
                            **({"local_obs": [dict(o) for o in list(local_obs)[:cfg.max_candidates]]}
                               if local_obs else {}),
                            # carried into every re-decision of this instance (report_result)
                            **({VIRTUAL_KEY: dict(virtual)} if virtual is not None else {})},
            "visibility": goal["visibility"],
            "owner_id": store.routing_owner(goal["visibility"], goal["owner_id"], goal.get("tenant_id")),
            "step_order": current})
    return response


def _code_parent(g: predict.Globals, code: Any) -> Optional[np.ndarray]:
    """(S, D) draws of a semantic code's node in the fitted hierarchy: the code itself when the fit saw it, else its
    coarse group, else None. Only a fit built with codes (the bundle's `code_x`) has them."""
    if not isinstance(code, str) or "code_x" not in g.arrays:
        return None
    from app.routing import semantic_codes

    names = list(g.meta.get("code_nodes") or [])
    for name in (code, semantic_codes.coarse(code)):
        if name in names:
            return g.arrays["code_x"][:, names.index(name), :]
    return None


def _unit_tokens(token_meta: Mapping[str, Any], model: str, unit: str, cards: Mapping[str, Any]) -> Optional[dict]:
    """A unit's own token stats, else its MODEL's (calibrated from published per-task costs, any scaffold: a model
    that writes long answers does so in every harness), else None (the global level only)."""
    own = (token_meta.get("units") or {}).get(unit)
    if own:
        return own
    models = token_meta.get("models") or {}
    if model in models:
        return models[model]
    from app.routing.cards import resolve

    card = resolve(model, cards)
    return models.get(card.model_key) if card is not None else None


def _case_label(virtual: Mapping[str, Any]) -> str:
    parts = ["fix-size features" if virtual.get("features") else "population mean"]
    if virtual.get("code"):
        parts.append(f"Ways like it ({virtual['code']})")
    if virtual.get("parents"):
        parts.append("near global Goals")
    return f"case prior ({virtual.get('kind')}: {' + '.join(parts)})"


def local_obs_loglik(p: np.ndarray, eps_w: np.ndarray, alpha: np.ndarray, beta: np.ndarray,
                     local_cols: Sequence[tuple[int, int, int]]) -> np.ndarray:
    """(S,) log-likelihood, per aligned posterior draw, of the caller's own past outcomes on this Goal.

    Each OBS attempt was a separate instance of the Goal in the caller's repo: its difficulty eps is
    its own, so it is integrated out per attempt, and the attempt was accepted with probability
    E_eps[p (1 - beta) + (1 - p) alpha] (the same check model as `ladder.belief`). With `ok` of `n`
    accepted, the draw's likelihood is that probability to the power `ok` times its complement to the
    power `n - ok` (OBS keeps counts, not order, so this is the binomial kernel)."""
    ll = np.zeros(p.shape[0])
    for col, n, ok in local_cols:
        pu = p[:, col, :]
        p_acc = ((pu * (1 - beta)[:, None] + (1 - pu) * alpha[:, None]) * eps_w[None, :]).sum(axis=1)
        p_acc = np.clip(p_acc, 1e-12, 1 - 1e-12)
        ll += ok * np.log(p_acc) + (n - ok) * np.log1p(-p_acc)
    return ll


COST_PRIOR_ATTEMPTS = 2.0


def _explore_unit(usable: Sequence[Any], local_obs: Sequence[Mapping[str, Any]], baseline: Any,
                  cost_ok: np.ndarray, rng: np.random.Generator, k: int) -> Optional[int]:
    """The unit to try first for exploration, or None: among units cheaper than the baseline (the session's own
    model) with fewer than k attempts in this workspace, one picked at random weighted to the least-tried. Only with
    a baseline (so a failed exploratory attempt escalates to the session's model) and k > 0."""
    if not baseline or k <= 0:
        return None
    b = next((i for i, (m, s) in enumerate(usable) if unit_id(m, s) == str(baseline)), None)
    if b is None:
        return None
    tried = {str(o.get("unit")): int(o.get("n") or 0) for o in (local_obs or [])}
    pool = [i for i, (m, s) in enumerate(usable) if i != b and cost_ok[i] < cost_ok[b] and tried.get(unit_id(m, s), 0) < k]
    if not pool:
        return None
    least = min(tried.get(unit_id(*usable[i]), 0) for i in pool)
    pool = [i for i in pool if tried.get(unit_id(*usable[i]), 0) == least]
    return int(pool[int(rng.integers(len(pool)))])


def _pool_unit_stats(stats: Mapping[str, Mapping[str, list]]) -> dict[str, list[float]]:
    """The size an UNTRIED unit is priced at here: per outcome, the smallest (in, out) any unit has needed on this
    Goal -- optimism under uncertainty, so its first try is cheap to justify and its real cost gets learned. A unit's
    own record (goal_tokens[unit]) replaces this as soon as it has one. n is the pooled count (how much it weighs)."""
    out: dict[str, list[float]] = {}
    for by in stats.values():
        for outcome, row in by.items():
            if float(row[0]) <= 0:
                continue
            acc = out.setdefault(outcome, [0.0, float("inf"), float("inf"), float("inf")])
            acc[0] += float(row[0])
            for j in (1, 2, 3):
                acc[j] = min(acc[j], float(row[j]))
    return out


def shift_to_local_rates(p: np.ndarray, eps_w: np.ndarray, draw_w: np.ndarray, alpha: np.ndarray, beta: np.ndarray,
                         local_cols: Sequence[tuple[int, int, int]], strength: float) -> np.ndarray:
    """p with each locally observed unit's logit shifted so its posterior-mean ACCEPT rate becomes the Beta-binomial
    posterior mean (prior mean m counted as `strength` attempts, plus ok of n): (m*s + ok) / (s + n). The shift is
    the same for every draw and node, so the prior's spread and its difficulty structure are kept."""
    p = p.copy()
    lo = lambda x: np.log(np.clip(x, 1e-6, 1 - 1e-6) / (1 - np.clip(x, 1e-6, 1 - 1e-6)))  # noqa: E731
    for col, n, ok in local_cols:
        if n <= 0:
            continue
        def accept(pu: np.ndarray) -> float:
            per = ((pu * (1 - beta)[:, None] + (1 - pu) * alpha[:, None]) * eps_w[None, :]).sum(axis=1)
            return float(draw_w @ per)
        m = accept(p[:, col, :])
        target = (m * strength + ok) / (strength + n)
        shift, base = 0.0, lo(p[:, col, :])
        for _ in range(30):                      # solve accept(sigmoid(logit p + shift)) = target (monotone in shift)
            got = accept(1 / (1 + np.exp(-(base + shift))))
            if abs(got - target) < 1e-4:
                break
            shift += (lo(np.array(target)) - lo(np.array(got))) * 1.5
        p[:, col, :] = 1 / (1 + np.exp(-(base + shift)))
    return p


def reweight_by_local_obs(draw_w: np.ndarray, loglik: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Posterior draw weights given the caller's local outcomes: prior weights times their likelihood.

    This is the repo-specific posterior of plan §3.3 computed as importance reweighting of the stored,
    aligned joint draws rather than by `fit.local_refit` (NUTS, ~45 s per Goal, and it persists its
    posterior to the shared store -- wrong for counts that belong to one caller's repository and are
    used for one request). Same model, same latents, exact as the draw count grows; the effective
    sample size says how much the local evidence moved the posterior (a small ESS means the counts
    are far from what the global posterior expected)."""
    log_w = np.log(np.clip(draw_w, 1e-300, None)) + loglik
    w = np.exp(log_w - log_w.max())
    w = w / w.sum()
    ess = 1.0 / float((w ** 2).sum())
    return w, {"ess": round(ess, 1), "draws": int(w.shape[0])}


def _weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs: Sequence[float]) -> list[float]:
    order = np.argsort(values)
    cum = np.cumsum(np.asarray(weights, dtype=float)[order])
    cum = cum / cum[-1]
    return [float(values[order][min(int(np.searchsorted(cum, q)), len(values) - 1)]) for q in qs]


def _model_basis(g: predict.Globals, model_key: str, registry: Mapping[str, Mapping[str, Any]],
                 cards: Mapping[str, Any]) -> str:
    """Where a model's ability estimate comes from: 'fitted' (it has data), 'predecessor' (a fitted
    earlier version), 'card' (its public metadata) or 'population' (nothing known about it)."""
    from app.routing.cards import resolve

    if model_key in g.models:
        return "fitted"
    pred, hops = registry.get(model_key, {}).get("predecessor"), 0
    while pred and hops < 32:
        if pred in g.models:
            return "predecessor"
        pred, hops = registry.get(pred, {}).get("predecessor"), hops + 1
    return "card" if resolve(model_key, cards) is not None and "card_beta" in g.arrays else "population"


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
        if (obs.get("visibility") or "public") == "public":
            try:                                  # the cross-Goal model update is an addition: never fail a report
                await store.enqueue_model_update(pool)
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("could not queue the model update", exc_info=True)
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
