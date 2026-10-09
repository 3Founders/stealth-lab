"""Calling a unit through the connection that offers it, and offering units to the router.

`call_unit` is the single place a model or agent is actually invoked. Order of checks (each fails
closed, before any byte leaves):

  1. the caller can see a connection offering this unit (visibility, enabled);
  2. the request's data class is one the connection allows;
  3. a *platform-owned* credential also passes the platform egress policy (INV-07,
     services/provider_policy.py) -- our key, so our compliance rule applies. A customer-owned (BYOK)
     credential is the customer's own risk decision, recorded in allowed_data_classes;
  4. the worst-case cost fits max_cost_usd (a unit with no price cannot be capped, so it is refused
     when a cap is asked for);
  5. the endpoint is safe to call (url_guard) and the credential resolves.

Failover (providers/health.py): when several connections offer the same unit, an endpoint that is down is
skipped and the next one is tried; checks 2-5 run again for each connection.

Not here yet: per-user and per-org budgets, rate limits, and an audit row per call -- the call is
logged by the MCP layer's tracing only. Those must land before a shared key serves other people.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, Optional, Sequence

from app.providers import adapters, registry
from app.providers.health import HEALTH
from app.providers.secrets import resolve_secret
from app.providers.types import (CallRequest, CallResult, Connection, ProviderCallDenied, ProviderCallFailed, UnitSpec,
                                 worst_case_cost)
from app.providers.url_guard import check_endpoint
from app.services.access import AccessScope
from app.services.classification import DataClass

MAX_OUTPUT_TOKENS = 16000
MAX_PROMPT_CHARS = 400_000


class ProviderCandidates:
    """The routing seam (app/routing/plan.py CandidateProvider): every unit the caller may run."""

    name = "connections"

    def configured(self) -> bool:
        return registry.configured()

    async def candidates(self, pool: Any, *, scope: AccessScope, goal_id: str, constraints: Any) -> Sequence[str]:
        units: list[str] = []
        for conn in await registry.visible_connections(scope):
            units.extend(u.unit for u in conn.units if u.unit not in units)
        return units


async def find_unit(scope: AccessScope, unit: str) -> tuple[Connection, UnitSpec]:
    for conn in await registry.visible_connections(scope):
        spec = conn.spec_for(unit)
        if spec is not None:
            return conn, spec
    # one message for "does not exist" and "not yours": no enumeration of other orgs' connections
    raise ProviderCallDenied(f"no connection available to you offers {unit!r}")


def _validate(request: CallRequest) -> None:
    if not request.prompt or not request.prompt.strip():
        raise ProviderCallDenied("prompt is empty")
    if len(request.prompt) + len(request.system or "") > MAX_PROMPT_CHARS:
        raise ProviderCallDenied(f"prompt is longer than {MAX_PROMPT_CHARS} characters")
    if not 1 <= request.max_tokens <= MAX_OUTPUT_TOKENS:
        raise ProviderCallDenied(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}")
    if request.data_class not in {c.value for c in DataClass}:
        raise ProviderCallDenied(f"unknown data_class {request.data_class!r}")


def governing_org(conn: Connection, scope: AccessScope, requested_org: Optional[str], *, governed: bool) -> Optional[str]:
    """The ONE organisation whose policy, budget and ledger this call falls under, or None when none applies.

    governed=False is a single-operator deployment (DEPLOYMENT_MODE=single_user): the operator is the only principal,
    so there is no organisation to govern. In a shared deployment nothing is guessed: an org-owned connection is
    governed by its owner; otherwise the caller's organisation is used only when they have exactly one or name it
    explicitly, and a caller with none may use only a connection of their own."""
    if not governed:
        return None
    member_orgs = tuple(scope.org_ids)
    if conn.owner.startswith("org:"):
        owner = conn.owner[4:]
        if requested_org and requested_org != owner:
            raise ProviderCallDenied(f"connection {conn.connection_id!r} belongs to a different organization")
        return owner
    if requested_org:
        if requested_org not in member_orgs:
            raise ProviderCallDenied("you are not a member of that organization")
        return requested_org
    if len(member_orgs) == 1:
        return member_orgs[0]
    if len(member_orgs) > 1:
        raise ProviderCallDenied("you belong to several organizations; say which one pays with org_id")
    if conn.owner.startswith("user:"):
        return None
    raise ProviderCallDenied(f"connection {conn.connection_id!r} is billed to an organization and you belong to none")


async def offering_connections(scope: AccessScope, unit: str) -> list[tuple[Connection, UnitSpec]]:
    """Every connection the caller may use that offers `unit`, in configured order."""
    out = []
    for conn in await registry.visible_connections(scope):
        spec = conn.spec_for(unit)
        if spec is not None:
            out.append((conn, spec))
    return out


def _rotation(offers: Sequence[tuple[Connection, UnitSpec]]) -> list[tuple[Connection, UnitSpec]]:
    """Working endpoints first, in configured order; endpoints cooling down after an outage are not tried -- unless
    none is working, when the one that failed longest ago gets the call (never refuse only because of the breaker)."""
    working = [(c, s) for c, s in offers if HEALTH.available(c.connection_id, s.unit)]
    if working:
        known = [m for m in (HEALTH.latency_ms(c.connection_id, s.unit) for c, s in working) if m is not None]
        best = min(known) if known else None
        # stable: configured order within "fast" and within "slow"
        return sorted(working, key=lambda cs: HEALTH.is_slow(cs[0].connection_id, cs[1].unit, slow_ms=cs[0].slow_ms,
                                                             best_ms=best))
    return sorted(offers, key=lambda cs: HEALTH.last_failure_at(cs[0].connection_id, cs[1].unit))[:1]


async def unit_availability(scope: AccessScope, units: Sequence[str]) -> dict[str, dict[str, Any]]:
    """For each unit: {"status": "available" | "unavailable" | "not_served"} (+ "retry_in_s" when unavailable).
    "not_served": no connection of this deployment offers it (the caller runs it itself) -- its health is unknown
    here, so it is never marked down."""
    connections = await registry.visible_connections(scope)
    out: dict[str, dict[str, Any]] = {}
    for unit in units:
        offers = [(c, c.spec_for(unit)) for c in connections if c.spec_for(unit) is not None]
        if not offers:
            out[unit] = {"status": "not_served"}
        elif any(HEALTH.available(c.connection_id, s.unit) for c, s in offers):
            working = [(c, s) for c, s in offers if HEALTH.available(c.connection_id, s.unit)]
            lat = [HEALTH.latency_ms(c.connection_id, s.unit) for c, s in working]
            slow = [c.slow_ms is not None and m is not None and m > c.slow_ms for (c, _), m in zip(working, lat)]
            if all(slow):        # every working endpoint is over its own slow_ms: it answers, but slowly
                out[unit] = {"status": "slow", "latency_ms": round(min(m for m in lat if m is not None))}
            else:
                out[unit] = {"status": "available"}
        else:
            waits = [(HEALTH.describe(c.connection_id, s.unit) or {}).get("retry_in_s", 0) for c, s in offers]
            out[unit] = {"status": "unavailable", "retry_in_s": min(waits) if waits else 0}
    return out


async def call_unit(pool: Any, scope: AccessScope, unit: str, request: CallRequest, *,
                    actor: Optional[str] = None, tenant_id: Optional[str] = None,
                    max_cost_usd: Optional[float] = None, org_id: Optional[str] = None, governed: bool = False,
                    tool: str = "call_model", instance_key: Optional[str] = None,
                    max_latency_ms: Optional[int] = None) -> CallResult:
    """Run `unit` on the first connection that offers it, passes every check and answers. An endpoint that is
    down (providers/health.py: transport error, timeout, 408/425/429/5xx, account refusal) is recorded, skipped
    for its cool-down, and the next connection offering the SAME unit is tried; a failure that says the request
    itself was bad stops at once. A connection whose policy refuses the call is skipped too, so another connection
    approved for the data class can serve it. The result's `extra["failover"]` lists the endpoints that failed."""
    _validate(request)
    offers = await offering_connections(scope, unit)
    if not offers:
        # one message for "does not exist" and "not yours": no enumeration of other orgs' connections
        raise ProviderCallDenied(f"no connection available to you offers {unit!r}")
    attempts: list[dict[str, Any]] = []
    first_denial: Optional[ProviderCallDenied] = None
    for conn, spec in _rotation(offers):
        try:
            result = await _call_one(pool, scope, unit, conn, spec, request, actor=actor, tenant_id=tenant_id,
                                     max_cost_usd=max_cost_usd, org_id=org_id, governed=governed, tool=tool,
                                     instance_key=instance_key, max_latency_ms=max_latency_ms)
        except ProviderCallDenied as exc:
            first_denial = first_denial or exc
            attempts.append({"connection_id": conn.connection_id, "refused": str(exc)})
            continue
        except ProviderCallFailed as exc:
            if not exc.outage:
                raise                                   # the request itself failed: another endpoint would too
            if not exc.budget_timeout:                  # the caller's own budget is not the endpoint's outage
                HEALTH.record_failure(conn.connection_id, spec.unit, status=exc.status, transport=exc.transport,
                                      error=str(exc))
            attempts.append({"connection_id": conn.connection_id, "error": str(exc)})
            continue
        HEALTH.record_success(conn.connection_id, spec.unit)
        failed = [a for a in attempts if "error" in a]
        return replace(result, extra={**dict(result.extra), "failover": failed}) if failed else result
    if first_denial is not None and not any("error" in a for a in attempts):
        raise first_denial                              # every connection refused by policy: say why
    raise ProviderCallFailed(
        f"every endpoint offering {unit!r} failed: " + "; ".join(a.get("error") or a.get("refused", "") for a in attempts),
        transport=True, attempts=attempts)


async def _call_one(pool: Any, scope: AccessScope, unit: str, conn: Connection, spec: UnitSpec,
                    request: CallRequest, *, actor: Optional[str], tenant_id: Optional[str],
                    max_cost_usd: Optional[float], org_id: Optional[str], governed: bool, tool: str,
                    instance_key: Optional[str], max_latency_ms: Optional[int] = None) -> CallResult:
    """Every check, then the call, on ONE connection (checks 2-5 of the module docstring)."""
    started = time.monotonic()
    adapter = await pre_checks(pool, unit, conn, spec, request, actor=actor, tenant_id=tenant_id,
                               max_cost_usd=max_cost_usd)

    async def run() -> CallResult:
        return await _call_with_keys(adapter, conn, spec, request, max_latency_ms)

    reservation, gate_ms = await hold_budget(pool, scope, unit, conn, spec, request, actor=actor, org_id=org_id,
                                             governed=governed, tool=tool, instance_key=instance_key, started=started)
    if reservation is None:
        return await run()
    try:
        result = await run()
    except BaseException as exc:
        await release_hold(pool, reservation, exc)
        raise
    await settle_hold(pool, reservation, spec, tokens_in=result.tokens_in, tokens_cache_read=result.tokens_cache_read,
                      tokens_cache_write=result.tokens_cache_write, tokens_out=result.tokens_out,
                      cost_usd=result.cost_usd, cost_source=result.cost_source, latency_ms=result.latency_ms,
                      gate_ms=gate_ms)
    return result


# The pieces of a governed call, shared with providers/chat.py (the chat-completions passthrough), which must run the
# SAME checks and ledger steps around a request that is not a prompt/answer pair.

async def pre_checks(pool: Any, unit: str, conn: Connection, spec: UnitSpec, request: CallRequest, *,
                     actor: Optional[str], tenant_id: Optional[str], max_cost_usd: Optional[float]) -> Any:
    """Checks 2-5 of the module docstring on one connection. Returns the adapter for the connection's kind."""
    if request.data_class not in conn.allowed_data_classes:
        raise ProviderCallDenied(f"connection {conn.connection_id!r} is not approved for {request.data_class} data "
                                 f"(allowed: {', '.join(conn.allowed_data_classes) or 'none'})")
    if conn.credential_owner == "platform":
        if pool is None:
            raise ProviderCallDenied("a platform credential needs the egress policy database, which is not available")
        from app.services.provider_policy import ProviderPolicyDenied, guard_send

        try:
            await guard_send(pool, data_classification=request.data_class, provider=conn.provider, model=spec.model,
                             tenant_id=tenant_id, actor_subject=actor)
        except ProviderPolicyDenied as exc:
            raise ProviderCallDenied(str(exc)) from exc
    if max_cost_usd is not None:
        worst = worst_case_cost(spec, request)
        if worst is None:
            raise ProviderCallDenied(f"{unit!r} has no price, so max_cost_usd cannot be enforced")
        if worst > max_cost_usd:
            raise ProviderCallDenied(f"worst-case cost ${worst:.4f} exceeds max_cost_usd ${max_cost_usd:.4f}; "
                                     "lower max_tokens or raise the cap")
    adapter = adapters.ADAPTERS.get(conn.kind)
    if adapter is None:
        raise ProviderCallDenied(f"connection {conn.connection_id!r} has unsupported kind {conn.kind!r}")
    await check_endpoint(conn.base_url, allow_http_loopback=conn.allow_http_loopback)
    return adapter


async def hold_budget(pool: Any, scope: AccessScope, unit: str, conn: Connection, spec: UnitSpec,
                      request: CallRequest, *, actor: Optional[str], org_id: Optional[str], governed: bool, tool: str,
                      instance_key: Optional[str], started: float) -> tuple[Any, Optional[int]]:
    """Reserve the worst case against the owning organisation's policy and budgets. Returns (reservation, gate_ms), or
    (None, None) when no organisation governs the call (single-operator deployment)."""
    org = governing_org(conn, scope, org_id, governed=governed)
    if org is None:
        return None, None
    if not actor:
        raise ProviderCallDenied("a governed call needs an identified caller")
    from app.services import org_governance as gov

    try:
        reservation = await gov.reserve_call(
            pool, org_id=org, actor_subject=actor, tool=tool, unit=unit, connection_id=conn.connection_id,
            provider=conn.provider, model=spec.model, scaffold=spec.scaffold, data_class=request.data_class,
            instance_key=instance_key, worst_case_usd=worst_case_cost(spec, request))
    except gov.GovernanceError as exc:
        if exc.code in gov.DENIAL_CODES:             # a policy refusal: keep it for the organisation's admins
            try:
                await gov.record_denial(pool, org_id=org, actor_subject=actor, tool=tool, unit=unit,
                                        provider=conn.provider, model=spec.model, data_class=request.data_class,
                                        error=exc)
            except gov.GovernanceError as record_error:
                raise ProviderCallFailed(f"the call was refused ({exc}) and the refusal could not be recorded: "
                                         f"{record_error}") from exc
        raise ProviderCallDenied(str(exc)) from exc
    gate_ms = int((time.monotonic() - started) * 1000)      # everything OUR code did before the provider was called
    return reservation, gate_ms


async def release_hold(pool: Any, reservation: Any, exc: BaseException) -> None:
    """The call did not complete: release its hold (the cost is recorded as unknown, not zero)."""
    from app.services import org_governance as gov

    try:
        await gov.fail_call(pool, reservation, error_type=type(exc).__name__)
    except gov.GovernanceError as release_error:
        raise ProviderCallFailed(f"the call failed and its budget hold could not be released: {release_error}") from exc


async def settle_hold(pool: Any, reservation: Any, spec: UnitSpec, *, tokens_in: Optional[int],
                      tokens_cache_read: Optional[int], tokens_cache_write: Optional[int], tokens_out: Optional[int],
                      cost_usd: Optional[float], cost_source: Optional[str], latency_ms: Optional[int],
                      gate_ms: Optional[int]) -> None:
    """Record what the call cost. With no usable cost the reserved worst case is recorded (org_governance.settle_call)."""
    from app.services import org_governance as gov

    try:
        await gov.settle_call(
            pool, reservation, tokens_input_fresh=tokens_in, tokens_cache_read=tokens_cache_read,
            tokens_cache_write=tokens_cache_write, tokens_output=tokens_out, cost_usd=cost_usd,
            cost_source=cost_source, latency_ms=latency_ms,
            gate_ms=gate_ms, tier=spec.tier,
            prices={"input": spec.input_per_mtok, "output": spec.output_per_mtok,
                    "cache_read": spec.cached_input_per_mtok, "cache_write": spec.cache_write_input_per_mtok})
    except gov.GovernanceError as exc:
        raise ProviderCallFailed(f"the call completed but its cost could not be recorded, so the result is withheld: "
                                 f"{exc}") from exc


def _key_id(conn: Connection, index: int) -> str:
    return f"{conn.connection_id}#key{index}"


async def _call_with_keys(adapter: Any, conn: Connection, spec: UnitSpec, request: CallRequest,
                          max_latency_ms: Optional[int]) -> CallResult:
    """One call on one endpoint, trying its keys in turn. A key that is rate-limited (429) or refused (401/402/403)
    is rested (providers/health.py) and the next key is used; any other failure is the endpoint's, and is raised.
    Keys are tried in configured order, rested ones last. The latency of a successful call is recorded."""
    async def attempt(secret: Optional[str]) -> CallResult:
        return await _within_budget(adapter.call(conn, spec, request, secret), max_latency_ms, conn)

    return await with_keys(conn, spec, attempt)


async def with_keys(conn: Connection, spec: UnitSpec, attempt: Any) -> Any:
    """Run `attempt(secret)` on one endpoint, trying the connection's keys in turn (see _call_with_keys). Shared with
    providers/chat.py, whose request is not a one-shot prompt."""
    keys = list(enumerate(conn.keys))
    multi = len(keys) > 1
    if multi:
        fresh = [(i, r) for i, r in keys if HEALTH.available(_key_id(conn, i))]
        rested = sorted([(i, r) for i, r in keys if (i, r) not in fresh],
                        key=lambda ir: HEALTH.last_failure_at(_key_id(conn, ir[0])))
        keys = fresh + rested
    last: Optional[BaseException] = None
    for n, (index, ref) in enumerate(keys):
        try:
            secret = await resolve_secret(ref)
        except ProviderCallDenied as exc:
            last = exc
            if multi:
                continue                                # this key is not set: the next one may be
            raise
        started = time.monotonic()
        try:
            result = await attempt(secret)
        except ProviderCallFailed as exc:
            key_problem = exc.status == 429 or exc.status in (401, 402, 403)
            if multi and key_problem and n < len(keys) - 1:
                HEALTH.record_failure(_key_id(conn, index), None, status=exc.status, error=str(exc))
                last = exc
                continue
            raise
        reported_ms = getattr(result, "latency_ms", None)
        HEALTH.record_latency(conn.connection_id, spec.unit,
                              reported_ms if reported_ms is not None else (time.monotonic() - started) * 1000)
        if multi:
            HEALTH.record_success(_key_id(conn, index))
        return result
    if isinstance(last, ProviderCallDenied):
        raise ProviderCallDenied(f"none of the credentials of connection {conn.connection_id!r} is set") from last
    raise last if last else ProviderCallDenied(f"connection {conn.connection_id!r} has no credential")


async def _within_budget(call: Any, max_latency_ms: Optional[int], conn: Connection) -> CallResult:
    """The caller's latency budget: past it the call is abandoned and the next endpoint (or model) is tried."""
    if not max_latency_ms:
        return await call
    try:
        return await asyncio.wait_for(call, timeout=max_latency_ms / 1000.0)
    except asyncio.TimeoutError as exc:
        raise ProviderCallFailed(f"{conn.connection_id}: no answer within max_latency_ms={max_latency_ms}",
                                 transport=True, budget_timeout=True) from exc


async def sync_prices(pool: Any, connections: Sequence[Connection], *, apply: bool = True) -> list[dict[str, Any]]:
    """Make routing_prices agree with the connections' declared prices (the ladder trades off cost, so a
    wrong price gives a wrong ladder). Inserts a new price row only where it is missing or different."""
    from app.routing import store

    changes: list[dict[str, Any]] = []
    priced = {u.model: u for c in connections for u in c.units if u.input_per_mtok is not None}
    current = await store.current_prices(pool, list(priced))
    for model, spec in sorted(priced.items()):
        have = current.get(model)
        if have is not None and (have.input_per_mtok, have.output_per_mtok) == (spec.input_per_mtok,
                                                                                 spec.output_per_mtok):
            continue
        changes.append({"model": model, "input": spec.input_per_mtok, "output": spec.output_per_mtok,
                        "was": None if have is None else [have.input_per_mtok, have.output_per_mtok]})
        if apply:
            await store.set_price(pool, model, input_per_mtok=spec.input_per_mtok,
                                  output_per_mtok=spec.output_per_mtok)
    return changes
