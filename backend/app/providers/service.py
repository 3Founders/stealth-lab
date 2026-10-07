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

Not here yet: per-user and per-org budgets, rate limits, and an audit row per call -- the call is
logged by the MCP layer's tracing only. Those must land before a shared key serves other people.
"""
from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from app.providers import adapters, registry
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


async def call_unit(pool: Any, scope: AccessScope, unit: str, request: CallRequest, *,
                    actor: Optional[str] = None, tenant_id: Optional[str] = None,
                    max_cost_usd: Optional[float] = None, org_id: Optional[str] = None, governed: bool = False,
                    tool: str = "call_model", instance_key: Optional[str] = None) -> CallResult:
    started = time.monotonic()
    _validate(request)
    conn, spec = await find_unit(scope, unit)
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
    secret = await resolve_secret(conn.credential_ref)

    org = governing_org(conn, scope, org_id, governed=governed)
    if org is None:
        return await adapter.call(conn, spec, request, secret)
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
    try:
        result = await adapter.call(conn, spec, request, secret)
    except BaseException as exc:
        try:
            await gov.fail_call(pool, reservation, error_type=type(exc).__name__)
        except gov.GovernanceError as release_error:
            raise ProviderCallFailed(f"the call failed and its budget hold could not be released: {release_error}") from exc
        raise
    try:
        await gov.settle_call(
            pool, reservation, tokens_input_fresh=result.tokens_in, tokens_cache_read=result.tokens_cache_read,
            tokens_cache_write=result.tokens_cache_write, tokens_output=result.tokens_out, cost_usd=result.cost_usd,
            cost_source=result.cost_source, latency_ms=result.latency_ms,
            gate_ms=gate_ms, tier=spec.tier,
            prices={"input": spec.input_per_mtok, "output": spec.output_per_mtok,
                    "cache_read": spec.cached_input_per_mtok, "cache_write": spec.cache_write_input_per_mtok})
    except gov.GovernanceError as exc:
        raise ProviderCallFailed(f"the call completed but its cost could not be recorded, so the result is withheld: "
                                 f"{exc}") from exc
    return result


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
