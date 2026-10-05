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
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence

from app.routing import service, store
from app.routing.config import DEFAULTS, RoutingDefaults, split_unit, unit_id
from app.services.access import AccessScope

RoutingError = service.RoutingError

# Keys recommend() adds to the stored constraints; they are bookkeeping, not caller input.
_BOOKKEEPING = ("check_kind", "previous_attempts", "previous_steps", "remaining_steps")


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


async def model_plan(pool: Any, *, scope: AccessScope, goal_id: str, procedure_id: Optional[str] = None,
                     candidates: Sequence[Any] = (), check_kind: Optional[str] = None,
                     constraints: Optional[Mapping[str, Any]] = None,
                     cfg: RoutingDefaults = DEFAULTS) -> dict[str, Any]:
    constraints = dict(constraints or {})
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
            check_kind=check_kind, instance_key=new_instance_key(goal_id), constraints=constraints, cfg=cfg)
    except RoutingError as exc:
        return {"status": "unavailable", "reason": str(exc), **extra}
    return {**_compact(rec), **extra}


def _compact(rec: Mapping[str, Any]) -> dict[str, Any]:
    if rec.get("status") != "ok":
        return {k: rec[k] for k in ("status", "reason", "excluded") if k in rec}
    ladder = list((rec.get("recommended") or {}).get("ladder") or [])
    return {
        "status": "ok", "instance_key": rec["instance_key"], "recommendation_id": rec["recommendation_id"],
        "ladder": ladder, "meets_reliability_target": rec.get("meets_reliability_target"),
        "reliability_target": rec.get("reliability_target"), "check_kind": rec.get("check_kind"),
        "recommended": rec.get("recommended"), "alternatives": rec.get("alternatives") or [],
        "excluded": rec.get("excluded") or [],
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
    """What the server stored for an instance_key issued by `model_plan` / `report_result`.
    An unknown key and one the caller may not see fail identically (no enumeration)."""
    unknown = RoutingError("unknown instance_key (use the one find_ways or report_result returned)")
    goal_id = goal_of_instance(instance_key)
    if goal_id is None:
        raise RoutingError("this instance_key did not come from find_ways / report_result; for keys from "
                           "recommend_models use report_model_run")
    goal = await store.visible_goal(pool, goal_id, scope)
    if goal is None:
        raise unknown
    decision = await store.instance_decision(pool, goal_id, instance_key)
    if decision is None:
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


async def report_result(pool: Any, *, scope: AccessScope, instance: Instance, accepted: bool,
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
            instance_key=instance.instance_key, previous_attempts=previous, constraints=constraints, cfg=cfg)
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
