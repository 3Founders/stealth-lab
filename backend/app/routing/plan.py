"""The agent-facing flow around the recommender: one plan up front, one report per attempt.

Why this exists: `recommend_models` + `report_model_run` ask the agent to assemble a candidate
list, copy ids between calls and carry `previous_attempts` itself. Agents drop that. Here the
server holds the state:

  model_plan(...)    -> find_ways attaches it; the first ladder, with an `instance_key`.
  load_instance(...) / report_result(...) -> one call per attempt; the reply names the NEXT
                        model (or says stop), rebuilt from what the server stored.

Candidates are deliberately NOT a built-in list. Who may run which model is a product decision
(BYOK keys, an enterprise contract, later an A2A-style agent registry), so this module only
defines the seam: a `CandidateProvider` registered with `register_candidate_provider`, plus
whatever the caller passes explicitly. With neither, the plan says `no_candidates` -- it does not
guess a model list.

instance_key = "<goal_id>.<random>": the Goal is the shard key of the routing logs
(store._goal_log_pool), so the key alone is enough to find an instance's decisions and attempts
without a fan-out. Keys from `recommend_models` do not have this shape; report those with
`report_model_run`.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence

from app.routing import service, store
from app.routing.config import DEFAULTS, RoutingDefaults, split_unit, unit_id
from app.services.access import AccessScope

RoutingError = service.RoutingError

# Keys recommend() adds to the stored constraints; they are bookkeeping, not caller input.
_BOOKKEEPING = ("check_kind", "previous_attempts", "previous_steps", "remaining_steps", "local_obs")
# Who the instance was issued to, stored with its decision. The MCP spec's "state handle hijacking" rule: a
# handle is bound to the authenticated caller server-side, so knowing the key (or seeing the Goal) is not enough.
# Not in _BOOKKEEPING on purpose: it is carried into every later re-decision of the same instance.
CALLER_KEY = "_caller"


# ------------------------------------------------------------------ candidate providers

class CandidateProvider(Protocol):
    """A source of runnable units for one request. Return 'model|scaffold' strings or
    {"model", "scaffold", "version"?} mappings (the shapes `recommend` already accepts)."""

    name: str

    async def candidates(self, pool: Any, *, scope: AccessScope, goal_id: str,
                         constraints: Mapping[str, Any]) -> Sequence[Any]: ...


_PROVIDERS: list[CandidateProvider] = []


def register_candidate_provider(provider: CandidateProvider) -> None:
    """Add a provider; one with the same `name` is replaced, so re-registering is safe."""
    unregister_candidate_provider(provider.name)
    _PROVIDERS.append(provider)


def unregister_candidate_provider(name: str) -> None:
    _PROVIDERS[:] = [p for p in _PROVIDERS if p.name != name]


def registered_candidate_providers() -> tuple[CandidateProvider, ...]:
    return tuple(_PROVIDERS)


DEFAULT_MODELS_ENV = "STEALTH_DEFAULT_MODELS"


class PublicCatalogCandidates:
    """Units the DEPLOYMENT can run for anyone (e.g. through its own OpenRouter or vLLM key), declared as
    STEALTH_DEFAULT_MODELS="model|scaffold,model|scaffold". A bare model name means the direct scaffold.
    Each needs a price (routing_prices) or a model card with a list price, or the recommender excludes it."""

    name = "catalog"

    def __init__(self, env: Optional[Mapping[str, str]] = None):
        self._env = env

    def units(self) -> list[str]:
        import os

        raw = (self._env if self._env is not None else os.environ).get(DEFAULT_MODELS_ENV, "")
        out: list[str] = []
        for item in raw.split(","):
            item = item.strip()
            if item:
                unit = item if "|" in item else f"{item}|direct"
                if unit not in out:
                    out.append(unit)
        return out

    def configured(self) -> bool:
        return bool(self.units())

    async def candidates(self, pool: Any, *, scope: AccessScope, goal_id: str,
                         constraints: Mapping[str, Any]) -> Sequence[str]:
        return self.units()


def caller_unit(model: Optional[str], client: Optional[str] = None) -> Optional[str]:
    """The caller's OWN model as a unit -- always runnable, by definition (CallerModelCandidates, plan §2.4).
    `model` is what the agent says it is ("claude-sonnet-4-5", or a full "model|scaffold"); the scaffold
    is its MCP client ("claude-code", "cursor", ...) because the harness changes the success rate."""
    if not model or not str(model).strip():
        return None
    model = str(model).strip()
    if "|" in model:
        return model
    from app.routing.cards import canonical_key

    scaffold = canonical_key(client) if client else "direct"
    return f"{model}|{scaffold or 'direct'}"


async def gather_candidates(pool: Any, *, scope: AccessScope, goal_id: str, constraints: Mapping[str, Any],
                            explicit: Sequence[Any] = ()) -> tuple[list[Any], list[dict[str, str]]]:
    """Explicit candidates first, then every provider's. A provider that raises is skipped and
    reported (a registry outage must not take find_ways down)."""
    units: list[Any] = list(explicit)
    problems: list[dict[str, str]] = []
    for provider in tuple(_PROVIDERS):
        try:
            units.extend(await provider.candidates(pool, scope=scope, goal_id=goal_id, constraints=constraints))
        except Exception as exc:  # noqa: BLE001 -- see docstring
            problems.append({"provider": provider.name, "error": f"{type(exc).__name__}: {exc}"})
    return units, problems


# ------------------------------------------------------------------ instance keys

def new_instance_key(goal_id: str) -> str:
    return f"{goal_id}.{uuid.uuid4().hex[:16]}"


def goal_of_instance(instance_key: str) -> Optional[str]:
    head, sep, tail = str(instance_key).partition(".")
    if not sep or not tail:
        return None
    try:
        return str(uuid.UUID(head))
    except ValueError:
        return None


# ------------------------------------------------------------------ the plan (find_ways)

def wants_plan(explicit: Sequence[Any] | None) -> bool:
    """A plan is attached only when someone can supply candidates; otherwise find_ways is unchanged.
    A provider may expose `configured()` to say it has nothing to offer (e.g. no connections set up)."""
    return bool(explicit) or any(getattr(p, "configured", lambda: True)() for p in _PROVIDERS)


async def model_plan(pool: Any, **kwargs: Any) -> dict[str, Any]:
    """The plan, plus `plan_ms`: how long computing it took. That is the router's overhead on a find_ways call; it is
    reported on every status (ok, no_candidates, unavailable) so a slow or failing router is visible, not hidden."""
    started = time.monotonic()
    out = await _model_plan(pool, **kwargs)
    return {**out, "plan_ms": int((time.monotonic() - started) * 1000)}


async def _model_plan(pool: Any, *, scope: AccessScope, goal_id: str, procedure_id: Optional[str] = None,
                      candidates: Sequence[Any] = (), check_kind: Optional[str] = None,
                      constraints: Optional[Mapping[str, Any]] = None,
                      cfg: RoutingDefaults = DEFAULTS,
                      local_obs: Sequence[Mapping[str, Any]] = (),
                      virtual: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """`local_obs`: the caller's own attempt counts on this Goal ([{unit, n, ok}], from the OBS lines of
    `.stealth/routing.md`); they condition the plan on what happened in the caller's repository.
    `virtual`: the task has no global Goal; `goal_id` is its virtual key (service.virtual_goal_id) and this says
    which case it is ({kind, ref?, features?, parents?}), which sets its prior (service.prior_draws_for_case)."""
    constraints = {k: v for k, v in dict(constraints or {}).items() if k != CALLER_KEY}
    units, problems = await gather_candidates(pool, scope=scope, goal_id=goal_id, constraints=constraints,
                                              explicit=candidates)
    extra = {"provider_errors": problems} if problems else {}
    if not units:
        return {"status": "no_candidates", **extra,
                "reason": "no model is registered for this caller; pass `candidates` (\"model|scaffold\") "
                          "or register a candidate provider"}
    try:
        rec = await service.recommend(
            pool, goal_id=goal_id, candidates=units, access_scope=scope, procedure_id=procedure_id,
            check_kind=check_kind, instance_key=new_instance_key(goal_id),
            constraints={**constraints, CALLER_KEY: scope.viewer_id}, cfg=cfg,
            **({"local_obs": list(local_obs)} if local_obs else {}),
            **({"virtual": dict(virtual)} if virtual else {}))
    except RoutingError as exc:
        return {"status": "unavailable", "reason": str(exc), **extra}
    return {**_compact(rec), **extra}


def _compact(rec: Mapping[str, Any]) -> dict[str, Any]:
    if rec.get("status") != "ok":
        return {k: rec[k] for k in ("status", "reason", "excluded") if k in rec}
    ladder = list((rec.get("recommended") or {}).get("ladder") or [])
    evidence = rec.get("evidence") or {}
    units = rec.get("units") or {}
    # plan §5.2: what the reply rests on, and the ladder with per-rung uncertainty (routing.md is written from it)
    # the caller's own local outcomes (route_obs, feat/library) also make it a posterior
    basis = "posterior" if (evidence.get("goal_observations") or 0) > 0 or evidence.get("local") or any(
        b == "fitted" for b in (evidence.get("models") or {}).values()) else "prior"
    step = rec.get("step") or {}
    return {
        "status": "ok", "basis": basis, "fit_id": rec.get("params_version"), "as_of": rec.get("as_of"),
        "steps": [{"step": step.get("step_order") if step else "*",
                   "ladder": [{"unit": u, **units.get(u, {})} for u in ladder]}],
        "model_basis": evidence.get("models") or {},
        "instance_key": rec["instance_key"], "recommendation_id": rec["recommendation_id"],
        "goal_id": rec.get("goal_id"),
        **({"case": evidence["case"]} if evidence.get("case") else {}),
        "ladder": ladder, "meets_reliability_target": rec.get("meets_reliability_target"),
        "reliability_target": rec.get("reliability_target"), "check_kind": rec.get("check_kind"),
        **({"reliability": rec["reliability"]} if rec.get("reliability") else {}),
        "recommended": rec.get("recommended"), "alternatives": rec.get("alternatives") or [],
        "excluded": rec.get("excluded") or [],
        **({"local_evidence": rec["evidence"]["local"]} if (rec.get("evidence") or {}).get("local") else {}),
        "next": (f"Run {ladder[0]!r} on the task, check the result ({rec.get('check_kind')}), then call "
                 "report_result(instance_key, accepted). If it failed the reply names the next model."
                 if ladder else "No rung is recommended; use the model you would have used."),
    }


# ------------------------------------------------------------------ reporting (report_result)

@dataclass(frozen=True)
class Instance:
    instance_key: str
    goal: Mapping[str, Any]
    decision: Mapping[str, Any]
    attempts: Sequence[Mapping[str, Any]]

    @property
    def procedure_id(self) -> Optional[str]:
        return self.decision.get("procedure_id")


async def load_instance(pool: Any, scope: AccessScope, instance_key: str) -> Instance:
    """`_load_instance` under the caller's routing row-level-security scope (migration 150)."""
    with store.routing_scope(scope):
        return await _load_instance(pool, scope, instance_key)


async def _load_instance(pool: Any, scope: AccessScope, instance_key: str) -> Instance:
    """What the server stored for an instance_key issued by `model_plan` / `report_result`.
    An unknown key and one the caller may not see fail identically (no enumeration)."""
    unknown = RoutingError("unknown instance_key (use the one find_ways or report_result returned)")
    goal_id = goal_of_instance(instance_key)
    if goal_id is None:
        raise RoutingError("this instance_key did not come from find_ways / report_result; for keys from "
                           "recommend_models use report_model_run")
    decision = await store.instance_decision(pool, goal_id, instance_key)
    if decision is None:
        raise unknown
    # a virtual key (no stored Goal) is reachable only through its own decision, which the RLS scope already
    # limited to the caller; a real Goal must also still be visible
    if (decision.get("constraints") or {}).get(service.VIRTUAL_KEY):
        goal = {"visibility": decision.get("visibility"), "owner_id": decision.get("owner_id")}
    else:
        goal = await store.visible_goal(pool, goal_id, scope)
    if goal is None:
        raise unknown
    # The binding is the EARLIEST decision's caller, so a later decision made under the same key (recommend_models
    # lets a caller reuse a key) can neither take the instance over nor clear its owner.
    owner = await store.instance_issuer(pool, goal_id, instance_key)
    # securityp1.md §5.1 item 1. An instance issued to a named caller is that caller's. One issued to NOBODY (a
    # decision written before the binding existed, or a plan made for an unidentified caller -- the single-user
    # server's shared-token posture) stays with unidentified callers only: a signed-in user can never take it over.
    # (In shared mode an unidentified caller cannot call a write tool at all, so this opens nothing there.)
    # Same answer as a missing key in every refused case.
    if not scope.is_unrestricted and owner != (str(scope.viewer_id) if scope.viewer_id is not None else None):
        raise unknown
    return Instance(instance_key, goal, decision, await store.instance_attempts(pool, goal_id, instance_key))


def _default_unit(instance: Instance) -> str:
    """The rung the agent most likely ran: the decision's ladder starts after the attempts that
    existed when it was made (stored as constraints.previous_attempts)."""
    ladder = list(instance.decision.get("ladder") or [])
    consumed = int((instance.decision.get("constraints") or {}).get("previous_attempts") or 0)
    index = len(instance.attempts) - consumed
    if 0 <= index < len(ladder):
        return ladder[index]
    raise RoutingError("every rung of the recommended ladder has been reported; pass `unit` "
                       "(\"model|scaffold\") for the model you actually ran")


async def report_result(pool: Any, *, scope: AccessScope, **kwargs: Any) -> dict[str, Any]:
    """`_report_result` under the caller's routing row-level-security scope (migration 150)."""
    with store.routing_scope(scope):
        return await _report_result(pool, scope=scope, **kwargs)


async def _report_result(pool: Any, *, scope: AccessScope, instance: Instance, accepted: bool,
                        unit: Optional[str] = None, check_kind: Optional[str] = None,
                        tokens_in: Optional[int] = None, tokens_out: Optional[int] = None,
                        tokens_cached: Optional[int] = None, cost_usd: Optional[float] = None,
                        latency_ms: Optional[int] = None, pass_fraction: Optional[float] = None,
                        reporter: Optional[str] = None, visibility: Optional[str] = None,
                        owner_id: Optional[str] = None, cfg: RoutingDefaults = DEFAULTS) -> dict[str, Any]:
    """Record one attempt, then say what to do next. A bad `unit`/`check_kind` raises RoutingError
    before anything is written."""
    decision = instance.decision
    stored = dict(decision.get("constraints") or {})
    check = check_kind or stored.get("check_kind") or cfg.default_check_kind
    try:
        model_key, scaffold = split_unit(unit or _default_unit(instance))
    except ValueError as exc:
        raise RoutingError(str(exc)) from exc
    n = len(instance.attempts)
    try:
        await service.record_observation(pool, {
            "source": "live", "goal_id": decision["goal_id"],
            "procedure_id": instance.procedure_id, "model_key": model_key, "scaffold": scaffold,
            "instance_key": instance.instance_key, "attempt_index": n, "check_kind": check, "accepted": bool(accepted),
            "pass_fraction": pass_fraction, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "tokens_cached": tokens_cached, "cost_usd": cost_usd, "latency_ms": latency_ms, "reporter": reporter,
            "recommendation_id": decision.get("id"),
            "visibility": visibility or decision.get("visibility") or "public",
            "owner_id": owner_id if owner_id is not None else decision.get("owner_id")})
    except store.ObservationRejected as exc:
        raise RoutingError(str(exc)) from exc

    base = {"instance_key": instance.instance_key, "recorded": True, "attempts": n + 1}
    if accepted:
        return {**base, "status": "accepted", "next": "Stop: this attempt passed its check."}

    previous = [{"unit": unit_id(a["model_key"], a["scaffold"]), "accepted": bool(a["accepted"]),
                 "check_kind": a.get("check_kind") or check} for a in instance.attempts]
    previous.append({"unit": unit_id(model_key, scaffold), "accepted": False, "check_kind": check})
    constraints = {k: v for k, v in stored.items() if k not in _BOOKKEEPING}
    try:
        rec = await service.recommend(
            pool, goal_id=decision["goal_id"], candidates=list(decision.get("candidates") or []),
            access_scope=scope, procedure_id=instance.procedure_id, check_kind=check,
            instance_key=instance.instance_key, previous_attempts=previous, constraints=constraints, cfg=cfg,
            **({"local_obs": stored["local_obs"]} if stored.get("local_obs") else {}))
    except RoutingError as exc:
        return {**base, "status": "rejected", "next": f"No next model could be recommended ({exc})."}
    compact = _compact(rec)
    ladder = compact.get("ladder") or []
    if compact.get("status") != "ok" or not ladder:
        return {**base, "status": "rejected", "ladder": [],
                "next": "Rejected, and no further rung is recommended: escalate to a stronger model or a person."}
    return {**base, "status": "rejected", "next_model": ladder[0], "ladder": ladder,
            "meets_reliability_target": compact.get("meets_reliability_target"),
            "recommended": compact.get("recommended"),
            "next": f"Rejected. Run {ladder[0]!r} next, check it, then call report_result again."}


def dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, default=str)
