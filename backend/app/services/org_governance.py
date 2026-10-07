"""What an organisation's admins control, and what every provider call costs it (db/136).

NO FALLBACK, anywhere in this module:
  * an organisation with no policy row is DENIED (there is no default policy);
  * an empty allowlist allows nothing; there is no "allow all" value, and no wildcard;
  * a budget of 0 is zero spend; "unlimited" cannot be expressed;
  * a call whose worst-case cost is unknown cannot be budgeted, so it is refused;
  * if the policy read, the reservation or the settlement cannot be written, the call fails loudly -- it is never
    sent unrecorded, and a recorded-but-unsettled call is never silently dropped;
  * an admin change and its audit row are one transaction: both happen or neither does.

A provider call is reserved BEFORE it is sent (worst case held against the monthly and per-user daily budgets, under a
row lock on the organisation's policy so concurrent calls cannot overspend together) and settled once AFTER (the ledger
trigger allows reserved -> settled|failed exactly once). The kill switch and the allowlists are re-read under that same
lock, so flipping them takes effect on the very next call.

Roles are checked here as well as at the REST layer, so a caller that forgets cannot skip them.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Collection, Iterable, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction

ADMIN_ROLES = frozenset({"owner", "admin"})
OWNER_ROLES = frozenset({"owner"})
MAX_EXPORT_ROWS = 10_000

POLICY_LIST_FIELDS = ("allowed_providers", "allowed_models", "allowed_tools", "allowed_data_classes")
POLICY_MONEY_FIELDS = ("monthly_budget_usd", "per_user_daily_budget_usd")
POLICY_FIELDS = POLICY_LIST_FIELDS + POLICY_MONEY_FIELDS

COST_SOURCES = ("provider", "declared", "upper_bound")
# the reasons a refusal can have; the same list is a CHECK constraint on org_denials (db/139)
DENIAL_CODES = ("kill_switch", "no_policy", "provider_not_allowed", "model_not_allowed", "tool_not_allowed",
                "data_class_not_allowed", "unpriced_unit", "monthly_budget_zero", "user_daily_budget_zero",
                "monthly_budget_exceeded", "user_daily_budget_exceeded")


class GovernanceError(Exception):
    """Base class; every message is safe to show the caller. `code` is a machine-readable reason when there is one
    (for a refusal it is one of DENIAL_CODES)."""

    status_code = 400

    def __init__(self, message: str = "", code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class NotAuthorized(GovernanceError):
    status_code = 403


class PolicyMissing(GovernanceError):
    status_code = 409


class PolicyDenied(GovernanceError):
    status_code = 403


class BudgetExceeded(PolicyDenied):
    pass


class Conflict(GovernanceError):
    status_code = 409


class NotFound(GovernanceError):
    status_code = 404


class Invalid(GovernanceError):
    status_code = 422


def _scope(org_id: str) -> TenantScope:
    return TenantScope.for_tenant(str(org_id))


def _require(roles: Collection[str], allowed: Collection[str], what: str) -> None:
    if not set(roles) & set(allowed):
        raise NotAuthorized(f"{what} needs the role {' or '.join(sorted(allowed))} in this organization")


# ------------------------------------------------------------------ policy

@dataclass(frozen=True)
class OrgPolicy:
    organization_id: str
    kill_switch: bool
    kill_switch_reason: Optional[str]
    allowed_providers: tuple[str, ...]
    allowed_models: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    allowed_data_classes: tuple[str, ...]
    monthly_budget_usd: Decimal
    per_user_daily_budget_usd: Decimal
    version: int

    def as_dict(self) -> dict[str, Any]:
        return {"organization_id": self.organization_id, "kill_switch": self.kill_switch,
                "kill_switch_reason": self.kill_switch_reason,
                "allowed_providers": list(self.allowed_providers), "allowed_models": list(self.allowed_models),
                "allowed_tools": list(self.allowed_tools), "allowed_data_classes": list(self.allowed_data_classes),
                "monthly_budget_usd": str(self.monthly_budget_usd),
                "per_user_daily_budget_usd": str(self.per_user_daily_budget_usd), "version": self.version}


def _policy_from_row(row: Mapping[str, Any]) -> OrgPolicy:
    return OrgPolicy(
        organization_id=str(row["organization_id"]), kill_switch=bool(row["kill_switch"]),
        kill_switch_reason=row["kill_switch_reason"], allowed_providers=tuple(row["allowed_providers"] or ()),
        allowed_models=tuple(row["allowed_models"] or ()), allowed_tools=tuple(row["allowed_tools"] or ()),
        allowed_data_classes=tuple(row["allowed_data_classes"] or ()),
        monthly_budget_usd=Decimal(str(row["monthly_budget_usd"])),
        per_user_daily_budget_usd=Decimal(str(row["per_user_daily_budget_usd"])), version=int(row["version"]))


def evaluate(policy: OrgPolicy, *, provider: str, model: str, tool: str, data_class: str) -> None:
    """Raise PolicyDenied unless every dimension is explicitly allowed. Pure."""
    if policy.kill_switch:
        raise PolicyDenied(f"this organization is stopped by its admins: {policy.kill_switch_reason}",
                           code="kill_switch")
    for label, code, allowed, value in (("provider", "provider_not_allowed", policy.allowed_providers, provider),
                                        ("model", "model_not_allowed", policy.allowed_models, model),
                                        ("tool", "tool_not_allowed", policy.allowed_tools, tool),
                                        ("data class", "data_class_not_allowed", policy.allowed_data_classes,
                                         data_class)):
        if value not in allowed:
            raise PolicyDenied(f"{label} {value!r} is not on this organization's allowlist", code=code)


async def load_policy(pool: Any, org_id: str) -> OrgPolicy:
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid", str(org_id))
    if row is None:
        raise PolicyMissing("this organization has no policy configured, so provider calls are denied until an "
                            "admin sets one")
    return _policy_from_row(row)


def _clean_list(name: str, value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise Invalid(f"{name} must be a list of non-empty strings")
    out = [v.strip() for v in value]
    if len(set(out)) != len(out):
        raise Invalid(f"{name} has duplicates")
    if any(v == "*" for v in out):
        raise Invalid(f"{name} cannot contain a wildcard: list what is allowed")
    return out


def _clean_money(name: str, value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise Invalid(f"{name} must be a number") from exc
    if not amount.is_finite() or amount < 0:
        raise Invalid(f"{name} must be zero or more")
    return amount


async def _audit(conn: Any, *, actor_subject: str, actor_user_id: Optional[str], action: str, object_type: str,
                 object_id: str, org_id: str, details: Mapping[str, Any]) -> None:
    """Same row the central writer (services/audit.py) writes, on THIS transaction's connection so the change and its
    evidence commit together. A failure raises and rolls the change back."""
    row = await conn.fetchrow(
        "INSERT INTO audit_events (actor_subject, actor_user_id, action, object_type, object_id, tenant_id, details) "
        "VALUES ($1, $2::uuid, $3, $4, $5, $6::uuid, $7::jsonb) RETURNING id",
        actor_subject, actor_user_id, action, object_type, object_id, str(org_id), dict(details))
    if row is None:
        raise GovernanceError(f"audit write returned no row for {action}")


async def put_policy(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                     org_id: str, changes: Mapping[str, Any], expected_version: Optional[int]) -> OrgPolicy:
    """Create (expected_version None) or update (expected_version = the version the admin was looking at) the policy.
    Only the policy fields are accepted; the kill switch has its own call."""
    _require(actor_roles, ADMIN_ROLES, "changing the policy")
    unknown = sorted(set(changes) - set(POLICY_FIELDS))
    if unknown:
        raise Invalid(f"unknown policy field(s): {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    for name in POLICY_LIST_FIELDS:
        if name in changes:
            clean[name] = _clean_list(name, changes[name])
    for name in POLICY_MONEY_FIELDS:
        if name in changes:
            clean[name] = _clean_money(name, changes[name])
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid FOR UPDATE", str(org_id))
        if row is None:
            if expected_version is not None:
                raise Conflict("there is no policy yet; send no expected_version to create it")
            missing = [f for f in POLICY_FIELDS if f not in clean]
            if missing:
                raise Invalid(f"a new policy must set every field explicitly (missing: {', '.join(missing)}); "
                              "there are no defaults to fall back on")
            await conn.execute(
                "INSERT INTO org_policies (organization_id, allowed_providers, allowed_models, allowed_tools, "
                "allowed_data_classes, monthly_budget_usd, per_user_daily_budget_usd, updated_by) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)",
                str(org_id), clean["allowed_providers"], clean["allowed_models"], clean["allowed_tools"],
                clean["allowed_data_classes"], clean["monthly_budget_usd"], clean["per_user_daily_budget_usd"],
                actor_subject)
            before: dict[str, Any] = {}
        else:
            current = _policy_from_row(row)
            if expected_version != current.version:
                raise Conflict(f"the policy is at version {current.version}, not {expected_version}; reload it")
            if not clean:
                raise Invalid("nothing to change")
            before = {k: (list(getattr(current, k)) if k in POLICY_LIST_FIELDS else str(getattr(current, k)))
                      for k in clean}
            sets, args = [], [str(org_id)]
            for name, value in clean.items():
                args.append(value)
                sets.append(f"{name} = ${len(args)}")
            args.append(actor_subject)
            await conn.execute(
                f"UPDATE org_policies SET {', '.join(sets)}, version = version + 1, updated_by = ${len(args)}, "
                "updated_at = now() WHERE organization_id = $1::uuid", *args)
        after = {k: (list(v) if isinstance(v, list) else str(v)) for k, v in clean.items()}
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_policy.put",
                     object_type="org_policy", object_id=str(org_id), org_id=org_id,
                     details={"before": before, "after": after})
        fresh = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid", str(org_id))
    return _policy_from_row(fresh)


async def set_kill_switch(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                          org_id: str, on: bool, reason: str) -> OrgPolicy:
    _require(actor_roles, ADMIN_ROLES, "the kill switch")
    if not isinstance(reason, str) or not reason.strip():
        raise Invalid("a reason is required")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid FOR UPDATE", str(org_id))
        if row is None:
            raise PolicyMissing("this organization has no policy yet; there is nothing to stop or resume")
        await conn.execute(
            "UPDATE org_policies SET kill_switch = $2, kill_switch_reason = $3, version = version + 1, "
            "updated_by = $4, updated_at = now() WHERE organization_id = $1::uuid",
            str(org_id), bool(on), reason.strip() if on else None, actor_subject)
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id,
                     action="org_policy.kill_switch_on" if on else "org_policy.kill_switch_off",
                     object_type="org_policy", object_id=str(org_id), org_id=org_id, details={"reason": reason.strip()})
        fresh = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid", str(org_id))
    return _policy_from_row(fresh)


# ------------------------------------------------------------------ the ledger: reserve, send, settle

@dataclass(frozen=True)
class Reservation:
    ledger_id: str
    organization_id: str
    reserved_usd: Decimal


async def reserve_call(pool: Any, *, org_id: str, actor_subject: str, tool: str, unit: str, connection_id: str,
                       provider: str, model: str, scaffold: str, data_class: str, instance_key: Optional[str],
                       worst_case_usd: Optional[float]) -> Reservation:
    """Evaluate the policy and hold the worst case against the budgets, atomically. Raises PolicyMissing,
    PolicyDenied or BudgetExceeded; on success the call may be sent."""
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid FOR UPDATE", str(org_id))
        if row is None:
            raise PolicyMissing("this organization has no policy configured, so provider calls are denied until an "
                                "admin sets one", code="no_policy")
        policy = _policy_from_row(row)
        evaluate(policy, provider=provider, model=model, tool=tool, data_class=data_class)
        if worst_case_usd is None:
            raise PolicyDenied(f"{unit!r} has no price, so its cost cannot be held against the budget",
                               code="unpriced_unit")
        worst = Decimal(str(worst_case_usd))
        if policy.monthly_budget_usd <= 0:
            raise BudgetExceeded("this organization's monthly budget is 0: no spend is allowed",
                                 code="monthly_budget_zero")
        if policy.per_user_daily_budget_usd <= 0:
            raise BudgetExceeded("this organization's per-user daily budget is 0: no spend is allowed",
                                 code="user_daily_budget_zero")
        held = ("COALESCE(sum(CASE WHEN status = 'settled' THEN cost_usd WHEN status = 'reserved' THEN reserved_usd "
                "ELSE 0 END), 0)")
        month = await conn.fetchval(
            f"SELECT {held} FROM provider_call_ledger WHERE organization_id = $1::uuid AND created_at >= "
            "date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'", str(org_id))
        if Decimal(str(month)) + worst > policy.monthly_budget_usd:
            raise BudgetExceeded(f"this call could take the organization past its monthly budget of "
                                 f"${policy.monthly_budget_usd} (${Decimal(str(month))} already used or held)",
                                 code="monthly_budget_exceeded")
        day = await conn.fetchval(
            f"SELECT {held} FROM provider_call_ledger WHERE organization_id = $1::uuid AND actor_subject = $2 AND "
            "created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'", str(org_id), actor_subject)
        if Decimal(str(day)) + worst > policy.per_user_daily_budget_usd:
            raise BudgetExceeded(f"this call could take you past your daily budget of "
                                 f"${policy.per_user_daily_budget_usd} (${Decimal(str(day))} already used or held)",
                                 code="user_daily_budget_exceeded")
        ledger_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO provider_call_ledger (id, organization_id, actor_subject, tool, unit, connection_id, provider, "
            "model, scaffold, data_class, instance_key, status, reserved_usd) "
            "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'reserved', $12)",
            ledger_id, str(org_id), actor_subject, tool, unit, connection_id, provider, model, scaffold, data_class,
            instance_key, worst)
    return Reservation(ledger_id, str(org_id), worst)


_MILLION = Decimal(1_000_000)
_MICRO = Decimal("0.000001")


def cost_components(prices: Optional[Mapping[str, Optional[float]]], *, tokens_input_fresh: Optional[int],
                    tokens_cache_read: Optional[int], tokens_cache_write: Optional[int],
                    tokens_output: Optional[int]) -> Optional[dict[str, Decimal]]:
    """Dollars per component at the prices in force for this call, or None when there are no declared token prices
    (a flat per-call price has no split). Cache reads and writes with no declared price are charged at the input
    price, the same over-estimate providers.types.tokens_cost uses. A component with no tokens costs 0."""
    if not prices or prices.get("input") is None or prices.get("output") is None:
        return None

    def part(tokens: Optional[int], price: Optional[float]) -> Decimal:
        return (Decimal(tokens or 0) * Decimal(str(price)) / _MILLION).quantize(_MICRO)
    read = prices.get("cache_read") if prices.get("cache_read") is not None else prices["input"]
    write = prices.get("cache_write") if prices.get("cache_write") is not None else prices["input"]
    return {"input": part(tokens_input_fresh, prices["input"]), "output": part(tokens_output, prices["output"]),
            "cache_read": part(tokens_cache_read, read), "cache_write": part(tokens_cache_write, write)}


async def settle_call(pool: Any, reservation: Reservation, *, tokens_input_fresh: Optional[int],
                      tokens_cache_read: Optional[int], tokens_cache_write: Optional[int],
                      tokens_output: Optional[int], cost_usd: Optional[float], cost_source: Optional[str],
                      latency_ms: Optional[int], prices: Optional[Mapping[str, Optional[float]]] = None,
                      gate_ms: Optional[int] = None, tier: Optional[str] = None) -> None:
    """Record what the call cost. With no usable cost the RESERVED WORST CASE is recorded and marked upper_bound:
    unknown never becomes zero. `prices` (USD per million tokens: input, output, cache_read, cache_write) are stored with
    the row; the per-component split is computed from them ONLY when the cost is a declared-price cost, so the
    components describe what was actually charged and a provider-reported cost is never given an invented split."""
    if cost_usd is None or cost_source is None:
        amount, source = reservation.reserved_usd, "upper_bound"
    else:
        if cost_source not in COST_SOURCES[:2]:
            raise Invalid(f"cost_source must be one of {COST_SOURCES[:2]}")
        amount, source = Decimal(str(cost_usd)), cost_source
    snapshot = prices or {}
    parts = (cost_components(prices, tokens_input_fresh=tokens_input_fresh, tokens_cache_read=tokens_cache_read,
                             tokens_cache_write=tokens_cache_write, tokens_output=tokens_output)
             if source == "declared" else None) or {}
    async with tenant_transaction(pool, _scope(reservation.organization_id)) as conn:
        status = await conn.execute(
            "UPDATE provider_call_ledger SET status = 'settled', tokens_input_fresh = $2, tokens_cache_read = $3, "
            "tokens_cache_write = $4, tokens_output = $5, cost_usd = $6, cost_source = $7, latency_ms = $8, "
            "price_input_per_mtok = $9, price_output_per_mtok = $10, price_cache_read_per_mtok = $11, "
            "price_cache_write_per_mtok = $12, cost_input_usd = $13, cost_output_usd = $14, "
            "cost_cache_read_usd = $15, cost_cache_write_usd = $16, gate_ms = $17, tier = $18, settled_at = now() "
            "WHERE id = $1::uuid AND status = 'reserved'",
            reservation.ledger_id, tokens_input_fresh, tokens_cache_read, tokens_cache_write, tokens_output, amount,
            source, latency_ms, _price(snapshot, "input"), _price(snapshot, "output"), _price(snapshot, "cache_read"),
            _price(snapshot, "cache_write"), parts.get("input"), parts.get("output"), parts.get("cache_read"),
            parts.get("cache_write"), gate_ms, tier)
    if status != "UPDATE 1":
        raise GovernanceError(f"ledger row {reservation.ledger_id} was not reserved, so it could not be settled")


def _price(snapshot: Mapping[str, Optional[float]], key: str) -> Optional[Decimal]:
    value = snapshot.get(key)
    return None if value is None else Decimal(str(value))


async def fail_call(pool: Any, reservation: Reservation, *, error_type: str, latency_ms: Optional[int] = None) -> None:
    """The call did not complete: release the hold. The cost of a failed call is recorded as unknown (NULL), not zero;
    spend sums count only settled cost and live holds."""
    async with tenant_transaction(pool, _scope(reservation.organization_id)) as conn:
        status = await conn.execute(
            "UPDATE provider_call_ledger SET status = 'failed', error_type = $2, latency_ms = $3, settled_at = now() "
            "WHERE id = $1::uuid AND status = 'reserved'", reservation.ledger_id, error_type[:120], latency_ms)
    if status != "UPDATE 1":
        raise GovernanceError(f"ledger row {reservation.ledger_id} was not reserved, so it could not be released")


# ------------------------------------------------------------------ reading: usage and audit export

async def usage_daily(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime,
                      until: datetime) -> list[dict[str, Any]]:
    _require(actor_roles, ADMIN_ROLES, "viewing usage")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        rows = await conn.fetch(
            "SELECT * FROM v_org_usage_daily WHERE organization_id = $1::uuid AND day >= $2::date AND day < $3::date "
            "ORDER BY day, provider, model, tool", str(org_id), since, until)
    return [dict(r) for r in rows]


async def performance_daily(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime,
                            until: datetime) -> list[dict[str, Any]]:
    """Latency percentiles (provider and our own gate time), our share of total time, and cache hit rates per day,
    provider, model, tool and tier. A rate of None means the provider did not report cache tokens: not 0%."""
    _require(actor_roles, ADMIN_ROLES, "viewing performance")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        rows = await conn.fetch(
            "SELECT * FROM v_org_performance_daily WHERE organization_id = $1::uuid AND day >= $2::date AND "
            "day < $3::date ORDER BY day, provider, model, tool, tier", str(org_id), since, until)
    return [dict(r) for r in rows]


async def audit_export(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime, until: datetime,
                       after_id: int = 0, limit: int = 1000) -> dict[str, Any]:
    """The organisation's own audit events, oldest first, a page at a time (`after_id` is the cursor), with the
    result of verifying the hash chain (the query itself lives in services/audit.py)."""
    _require(actor_roles, ADMIN_ROLES, "exporting the audit log")
    if not 1 <= limit <= MAX_EXPORT_ROWS:
        raise Invalid(f"limit must be between 1 and {MAX_EXPORT_ROWS}")
    from app.services.audit import export_tenant_events

    return await export_tenant_events(pool, tenant_id=org_id, since=since, until=until, after_id=after_id, limit=limit)


async def record_denial(pool: Any, *, org_id: str, actor_subject: str, tool: str, unit: str, provider: str, model: str,
                        data_class: str, error: GovernanceError) -> None:
    """Keep a refusal. Only a refusal with a known reason code is recorded; anything else is not a policy denial and is
    raised instead, so a bug never turns into a made-up denial. The message is stored as the caller saw it (no content)."""
    if error.code not in DENIAL_CODES:
        raise GovernanceError(f"not a recordable denial: {error.code!r}")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        await conn.execute(
            "INSERT INTO org_denials (organization_id, actor_subject, tool, unit, provider, model, data_class, "
            "reason_code, detail) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)",
            str(org_id), actor_subject, tool, unit, provider, model, data_class, error.code, str(error)[:500])



# ------------------------------------------------------------------ reading: people, calls, denials, budgets, ranges

CALL_STATUSES = ("reserved", "settled", "failed")
MAX_RANGE_DAYS = 366
PERFORMANCE_GROUPS = {"total": None, "model": "model", "provider": "provider", "tool": "tool", "tier": "tier"}


def _check_range(since: datetime, until: datetime) -> None:
    if until <= since:
        raise Invalid("until must be after since")
    if (until - since).days > MAX_RANGE_DAYS:
        raise Invalid(f"the range is limited to {MAX_RANGE_DAYS} days")


def _cursor(after: Optional[str]) -> Optional[tuple[datetime, str]]:
    """`<iso timestamp>|<uuid>`: the (created_at, id) of the last row of the previous page."""
    if after is None or after == "":
        return None
    stamp, sep, ident = after.partition("|")
    try:
        return datetime.fromisoformat(stamp), str(uuid.UUID(ident))
    except ValueError as exc:
        raise Invalid("after must be the next_after value from the previous page") from exc


def _next_cursor(rows: list[dict[str, Any]], limit: int) -> Optional[str]:
    return f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}" if len(rows) == limit else None


async def list_members(pool: Any, *, actor_roles: Collection[str], org_id: str) -> list[dict[str, Any]]:
    """The organisation's current members with their roles. `subject` is the identity provider's user id: it is the value
    that appears as `user_id` in the usage, call and denial data, so a dashboard can show a name for it. Admin/owner only.
    Inviting, changing a role and removing a member are not built (they need an invitation and notification design)."""
    _require(actor_roles, ADMIN_ROLES, "listing members")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT u.id::text AS user_id, u.external_subject AS subject, u.display_name, u.email, u.is_active, "
            "array_agg(r.name ORDER BY CASE r.name WHEN 'owner' THEN 1 WHEN 'admin' THEN 2 WHEN 'member' THEN 3 "
            "ELSE 4 END) AS roles, min(m.t_created) AS member_since "
            "FROM org_memberships m JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id "
            "WHERE m.organization_id = $1::uuid AND m.t_expired IS NULL GROUP BY u.id "
            "ORDER BY lower(COALESCE(u.display_name, u.email, u.external_subject))", str(org_id))
    return [dict(r) for r in rows]


async def usage_by_user(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime,
                        until: datetime) -> list[dict[str, Any]]:
    """Spend and tokens per person per day. `actor_subject` is the identity provider's user id, not an email."""
    _require(actor_roles, ADMIN_ROLES, "viewing per-user usage")
    _check_range(since, until)
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        rows = await conn.fetch(
            "SELECT * FROM v_org_usage_by_user_daily WHERE organization_id = $1::uuid AND day >= $2::date AND "
            "day < $3::date ORDER BY day, actor_subject", str(org_id), since, until)
    return [dict(r) for r in rows]


async def list_calls(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime, until: datetime,
                     after: Optional[str] = None, limit: int = 200, model: Optional[str] = None,
                     tool: Optional[str] = None, status: Optional[str] = None,
                     user: Optional[str] = None) -> dict[str, Any]:
    """Metadata for each call, oldest first, a page at a time. There is no prompt or reply anywhere in this data; an
    error is described by its type only. Every row here was ALLOWED (refusals are in list_denials)."""
    _require(actor_roles, ADMIN_ROLES, "viewing calls")
    _check_range(since, until)
    if not 1 <= limit <= 1000:
        raise Invalid("limit must be between 1 and 1000")
    if status is not None and status not in CALL_STATUSES:
        raise Invalid(f"status must be one of {CALL_STATUSES}")
    conds, params = ["organization_id = $1::uuid", "created_at >= $2", "created_at < $3"], [str(org_id), since, until]
    for column, value in (("model", model), ("tool", tool), ("status", status), ("actor_subject", user)):
        if value is not None:
            params.append(value)
            conds.append(f"{column} = ${len(params)}")
    cursor = _cursor(after)
    if cursor is not None:
        params += [cursor[0], cursor[1]]
        conds.append(f"(created_at, id) > (${len(params) - 1}, ${len(params)}::uuid)")
    params.append(limit)
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, created_at, actor_subject AS user_id, provider, model, scaffold, tool, tier, status, "
            "data_class, tokens_input_fresh, tokens_cache_read, tokens_cache_write, tokens_output, cost_usd, "
            "cost_source, latency_ms AS provider_ms, gate_ms, error_type, instance_key, 'allowed' AS policy_decision "
            f"FROM provider_call_ledger WHERE {' AND '.join(conds)} ORDER BY created_at, id LIMIT ${len(params)}",
            *params)
    calls = [dict(r) for r in rows]
    return {"calls": calls, "next_after": _next_cursor(calls, limit)}


async def list_denials(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime, until: datetime,
                       after: Optional[str] = None, limit: int = 200) -> dict[str, Any]:
    """What the organisation's policy and budgets refused: counts by reason over the whole range, plus the events a page
    at a time. Only provider-call refusals by the policy are recorded here (the other guardrails are a plan item)."""
    _require(actor_roles, ADMIN_ROLES, "viewing denials")
    _check_range(since, until)
    if not 1 <= limit <= 1000:
        raise Invalid("limit must be between 1 and 1000")
    cursor = _cursor(after)
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        counts = await conn.fetch(
            "SELECT reason_code, count(*) AS count FROM org_denials WHERE organization_id = $1::uuid AND "
            "created_at >= $2 AND created_at < $3 GROUP BY reason_code ORDER BY count DESC, reason_code",
            str(org_id), since, until)
        if cursor is None:
            rows = await conn.fetch(
                "SELECT id::text AS id, created_at, actor_subject AS user_id, tool, unit, provider, model, data_class, "
                "reason_code, detail FROM org_denials WHERE organization_id = $1::uuid AND created_at >= $2 AND "
                "created_at < $3 ORDER BY created_at, id LIMIT $4", str(org_id), since, until, limit)
        else:
            rows = await conn.fetch(
                "SELECT id::text AS id, created_at, actor_subject AS user_id, tool, unit, provider, model, data_class, "
                "reason_code, detail FROM org_denials WHERE organization_id = $1::uuid AND created_at >= $2 AND "
                "created_at < $3 AND (created_at, id) > ($5, $6::uuid) ORDER BY created_at, id LIMIT $4",
                str(org_id), since, until, limit, cursor[0], cursor[1])
    events = [dict(r) for r in rows]
    return {"counts_by_reason": [dict(r) for r in counts], "events": events, "next_after": _next_cursor(events, limit)}


async def budget_status(pool: Any, *, actor_roles: Collection[str], org_id: str) -> dict[str, Any]:
    """Where the organisation and each person stand against the policy's budgets right now: settled spend plus live holds,
    this UTC month and this UTC day. Raises PolicyMissing when there is no policy to measure against."""
    _require(actor_roles, ADMIN_ROLES, "viewing budgets")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow("SELECT * FROM org_policies WHERE organization_id = $1::uuid", str(org_id))
        if row is None:
            raise PolicyMissing("this organization has no policy, so there is no budget to measure against")
        policy = _policy_from_row(row)
        month_start = await conn.fetchval("SELECT date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
        day_start = await conn.fetchval("SELECT date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
        month = await conn.fetchrow(
            "SELECT COALESCE(sum(cost_usd) FILTER (WHERE status = 'settled'), 0) AS used, "
            "COALESCE(sum(reserved_usd) FILTER (WHERE status = 'reserved'), 0) AS held "
            "FROM provider_call_ledger WHERE organization_id = $1::uuid AND created_at >= $2", str(org_id), month_start)
        users = await conn.fetch(
            "SELECT actor_subject AS user_id, COALESCE(sum(cost_usd) FILTER (WHERE status = 'settled'), 0) AS used, "
            "COALESCE(sum(reserved_usd) FILTER (WHERE status = 'reserved'), 0) AS held FROM provider_call_ledger "
            "WHERE organization_id = $1::uuid AND created_at >= $2 GROUP BY actor_subject ORDER BY used DESC, held DESC",
            str(org_id), day_start)
    used, held = Decimal(str(month["used"])), Decimal(str(month["held"]))
    daily = policy.per_user_daily_budget_usd
    return {
        "policy_version": policy.version, "kill_switch": policy.kill_switch,
        "month_start": month_start, "day_start": day_start,
        "monthly": {"budget_usd": policy.monthly_budget_usd, "used_usd": used, "held_usd": held,
                    "remaining_usd": policy.monthly_budget_usd - used - held},
        "per_user_daily_budget_usd": daily,
        "users_today": [{"user_id": u["user_id"], "used_usd": Decimal(str(u["used"])),
                         "held_usd": Decimal(str(u["held"])),
                         "remaining_usd": daily - Decimal(str(u["used"])) - Decimal(str(u["held"]))} for u in users]}


async def performance_summary(pool: Any, *, actor_roles: Collection[str], org_id: str, since: datetime,
                              until: datetime, group_by: str = "total") -> list[dict[str, Any]]:
    """TRUE percentiles over the whole range (computed from the raw calls, never an average of daily percentiles), per
    model, provider, tool or tier, or for everything at once. Cache rates are ratios of sums over calls whose provider
    reported cache tokens; None means not reported."""
    _require(actor_roles, ADMIN_ROLES, "viewing performance")
    _check_range(since, until)
    if group_by not in PERFORMANCE_GROUPS:
        raise Invalid(f"group_by must be one of {sorted(PERFORMANCE_GROUPS)}")
    column = PERFORMANCE_GROUPS[group_by]                   # a fixed column name from the table above, never user text
    key = "'all'::text" if column is None else column
    group_sql, order_sql = ("", "") if column is None else (f"GROUP BY {column}", "ORDER BY key")
    ok = "status = 'settled'"
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        rows = await conn.fetch(
            f"SELECT {key} AS key, count(*) FILTER (WHERE {ok}) AS calls, "
            f"percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE {ok} AND latency_ms IS NOT NULL) AS provider_p50_ms, "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE {ok} AND latency_ms IS NOT NULL) AS provider_p95_ms, "
            f"percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE {ok} AND latency_ms IS NOT NULL) AS provider_p99_ms, "
            f"percentile_cont(0.50) WITHIN GROUP (ORDER BY gate_ms) FILTER (WHERE {ok} AND gate_ms IS NOT NULL) AS gate_p50_ms, "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY gate_ms) FILTER (WHERE {ok} AND gate_ms IS NOT NULL) AS gate_p95_ms, "
            f"percentile_cont(0.99) WITHIN GROUP (ORDER BY gate_ms) FILTER (WHERE {ok} AND gate_ms IS NOT NULL) AS gate_p99_ms, "
            f"(sum(gate_ms) FILTER (WHERE {ok} AND gate_ms IS NOT NULL AND latency_ms IS NOT NULL))::numeric / "
            f"NULLIF(sum(gate_ms + latency_ms) FILTER (WHERE {ok} AND gate_ms IS NOT NULL AND latency_ms IS NOT NULL), 0) AS gate_time_share, "
            f"count(*) FILTER (WHERE {ok} AND tokens_cache_read IS NOT NULL) AS calls_reporting_cache, "
            f"(count(*) FILTER (WHERE {ok} AND tokens_cache_read > 0))::numeric / "
            f"NULLIF(count(*) FILTER (WHERE {ok} AND tokens_cache_read IS NOT NULL), 0) AS cache_hit_rate_requests, "
            f"(sum(tokens_cache_read) FILTER (WHERE {ok} AND tokens_cache_read IS NOT NULL))::numeric / "
            f"NULLIF(sum(COALESCE(tokens_input_fresh, 0) + tokens_cache_read + COALESCE(tokens_cache_write, 0)) "
            f"FILTER (WHERE {ok} AND tokens_cache_read IS NOT NULL), 0) AS cache_hit_rate_tokens "
            f"FROM provider_call_ledger WHERE organization_id = $1::uuid AND created_at >= $2 AND created_at < $3 "
            f"{group_sql} {order_sql}", str(org_id), since, until)
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ legal holds and erasure

async def place_legal_hold(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                           org_id: str, reason: str) -> str:
    _require(actor_roles, OWNER_ROLES, "placing a legal hold")
    if not reason or not reason.strip():
        raise Invalid("a reason is required")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        hold_id = str(await conn.fetchval(
            "INSERT INTO org_legal_holds (organization_id, reason, placed_by) VALUES ($1::uuid, $2, $3) RETURNING id",
            str(org_id), reason.strip(), actor_subject))
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_legal_hold.placed",
                     object_type="org_legal_hold", object_id=hold_id, org_id=org_id, details={"reason": reason.strip()})
    return hold_id


async def release_legal_hold(pool: Any, *, actor_subject: str, actor_user_id: Optional[str],
                             actor_roles: Collection[str], org_id: str, hold_id: str) -> None:
    _require(actor_roles, OWNER_ROLES, "releasing a legal hold")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        status = await conn.execute(
            "UPDATE org_legal_holds SET released_by = $3, released_at = now() "
            "WHERE id = $2::uuid AND organization_id = $1::uuid AND released_at IS NULL", str(org_id), hold_id,
            actor_subject)
        if status != "UPDATE 1":
            raise NotFound("no active hold with that id in this organization")
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_legal_hold.released",
                     object_type="org_legal_hold", object_id=hold_id, org_id=org_id, details={})


async def request_erasure(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                          org_id: str, reason: str) -> str:
    _require(actor_roles, OWNER_ROLES, "requesting erasure")
    if not reason or not reason.strip():
        raise Invalid("a reason is required")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        open_ = await conn.fetchval(
            "SELECT count(*) FROM org_erasure_requests WHERE organization_id = $1::uuid AND status IN "
            "('requested', 'approved', 'executing')", str(org_id))
        if open_:
            raise Conflict("this organization already has an open erasure request")
        request_id = str(await conn.fetchval(
            "INSERT INTO org_erasure_requests (organization_id, requested_by, reason) VALUES ($1::uuid, $2, $3) "
            "RETURNING id", str(org_id), actor_subject, reason.strip()))
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id,
                     action="org_erasure.requested", object_type="org_erasure_request", object_id=request_id,
                     org_id=org_id, details={"reason": reason.strip()})
    return request_id


async def approve_erasure(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                          org_id: str, request_id: str) -> None:
    """Approval must come from a DIFFERENT owner than the requester (also enforced by a CHECK in the table)."""
    _require(actor_roles, OWNER_ROLES, "approving erasure")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow(
            "SELECT status, requested_by FROM org_erasure_requests WHERE id = $2::uuid AND organization_id = $1::uuid "
            "FOR UPDATE", str(org_id), request_id)
        if row is None:
            raise NotFound("no such erasure request in this organization")
        if row["status"] != "requested":
            raise Conflict(f"the request is {row['status']}, not waiting for approval")
        if row["requested_by"] == actor_subject:
            raise NotAuthorized("a different owner than the requester must approve")
        holds = await conn.fetchval("SELECT count(*) FROM org_legal_holds WHERE organization_id = $1::uuid AND "
                                    "released_at IS NULL", str(org_id))
        if holds:
            raise Conflict("this organization is under an active legal hold; release it before erasing")
        await conn.execute("UPDATE org_erasure_requests SET status = 'approved', approved_by = $2, approved_at = now() "
                           "WHERE id = $1::uuid", request_id, actor_subject)
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_erasure.approved",
                     object_type="org_erasure_request", object_id=request_id, org_id=org_id, details={})


# What an approved erasure can and cannot remove TODAY. "ready" entries are deleted by execute_erasure and verified;
# "blocked" entries name the exact obstacle. An erasure request only reaches `completed` when no entry is blocked, so a
# blocked registry can never produce a false "your data is deleted".
ERASURE_REGISTRY: tuple[Mapping[str, str], ...] = (
    {"table": "provider_call_ledger", "state": "ready", "note": "deleted under sl_erasure_authorized()"},
    {"table": "org_denials", "state": "ready", "note": "deleted under sl_erasure_authorized()"},
    {"table": "org_policies", "state": "ready", "note": "deleted"},
    {"table": "procedures", "state": "blocked",
     "note": "tg_procedures_refuse_delete; versioned rows in sharded knowledge stores need the shard fan-out"},
    {"table": "knowledge_nodes", "state": "blocked", "note": "claims live on knowledge shards; needs the shard fan-out"},
    {"table": "evidence", "state": "blocked", "note": "tg_evidence_append_only has no erasure branch yet"},
    {"table": "change_sets", "state": "blocked", "note": "tg_change_sets_append_only has no erasure branch yet"},
    {"table": "executions", "state": "blocked", "note": "tg_executions_frozen has no erasure branch yet"},
    {"table": "routing_observations", "state": "blocked",
     "note": "keyed by owner_id (a user), in per-Goal search-group pools; needs the user-to-org mapping and fan-out"},
    {"table": "goals", "state": "blocked", "note": "keyed by owner_id; shared Goals need an ownership decision first"},
)


async def execute_erasure(pool: Any, *, actor_subject: str, actor_user_id: Optional[str],
                          actor_roles: Collection[str], org_id: str, request_id: str) -> dict[str, Any]:
    """Delete what the registry says is ready, verify it is gone, and record a manifest. Completes the request ONLY if
    nothing is blocked; otherwise the request stays `executing` and the manifest lists every obstacle."""
    _require(actor_roles, OWNER_ROLES, "executing erasure")
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        row = await conn.fetchrow(
            "SELECT status FROM org_erasure_requests WHERE id = $2::uuid AND organization_id = $1::uuid FOR UPDATE",
            str(org_id), request_id)
        if row is None:
            raise NotFound("no such erasure request in this organization")
        if row["status"] not in ("approved", "executing"):
            raise Conflict(f"the request is {row['status']}; it must be approved first")
        holds = await conn.fetchval("SELECT count(*) FROM org_legal_holds WHERE organization_id = $1::uuid AND "
                                    "released_at IS NULL", str(org_id))
        if holds:                                     # checked again here: a hold can be placed after approval
            raise Conflict("this organization is under an active legal hold; release it before erasing")
        await conn.execute("UPDATE org_erasure_requests SET status = 'executing' WHERE id = $1::uuid", request_id)
        await conn.execute("SELECT set_config('app.erasure_request', $1, TRUE)", request_id)
        manifest: dict[str, Any] = {}
        for entry in ERASURE_REGISTRY:
            table = entry["table"]
            if entry["state"] != "ready":
                manifest[table] = {"blocked": entry["note"]}
                continue
            column = "organization_id"
            deleted = await conn.fetchval(
                f"WITH d AS (DELETE FROM {table} WHERE {column} = $1::uuid RETURNING 1) SELECT count(*) FROM d",
                str(org_id))
            remaining = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {column} = $1::uuid", str(org_id))
            manifest[table] = {"deleted": int(deleted), "remaining": int(remaining)}
        blocked = [t for t, m in manifest.items() if "blocked" in m]
        unverified = [t for t, m in manifest.items() if m.get("remaining")]
        complete = not blocked and not unverified
        await conn.execute(
            "UPDATE org_erasure_requests SET manifest = $2, status = $3, completed_at = CASE WHEN $4 THEN now() END "
            "WHERE id = $1::uuid", request_id, manifest, "completed" if complete else "executing", complete)
        await _audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id,
                     action="org_erasure.completed" if complete else "org_erasure.partial",
                     object_type="org_erasure_request", object_id=request_id, org_id=org_id,
                     details={"manifest": manifest})
    return {"request_id": request_id, "completed": complete, "manifest": manifest, "blocked": blocked}
