"""The plans a person can buy with Razorpay: Pro (30 days) and the Pilot (14 days, 1 to 10 seats).

Prices are the founders' (kell_decks/4_kell_router_and_coding_agents.html, slides 7-8; docs/billing.md "Plans"), in rupees
before GST. The browser never sends a price: it sends a plan name, a seat count and a team name, and the server works out
the amount, records the order, and activates the plan only from a payment Razorpay signed for an order WE recorded.

What a plan is, in money terms: the rupees buy the plan for a period, and the plan comes with included usage credited to a
wallet once, at purchase (Pro: $5; Pilot: $4 per seat). That credit is ordinary wallet credit: it is spent by calls and
does not expire when the period ends. Margin plays no part in it (the credit is a fixed dollar amount, not paise run
through KELL_MARGIN), which is why the two numbers can be set independently (KELL_PRO_INCLUDED_USD,
KELL_PILOT_SEAT_USD).

Who holds the wallet: a Pro, and a one-seat Pilot, credit the buyer's own account (their user id, services/accounts.py). A
Pilot of two or more seats creates a real team organization named by the buyer, with the buyer as its owner, and credits
that; the other seats join it through the organization invites. Nothing is hidden.

Activation is a series of idempotent steps with the entitlement row written LAST, so a webhook that fails halfway is
retried by Razorpay and completes without duplicating anything: the team slug is derived from the payment id, the included
credit has its own unique payment_ref, and the entitlement's payment_ref is unique.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.services import accounts
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

OWNER_ROLE_ID = "10000000-0000-0000-0000-000000000001"      # db/28_identity.sql

PRO_INR = 999
PILOT_SEAT_INR = 649
PRO_DAYS = 30
PILOT_DAYS = 14
PILOT_MIN_SEATS = 1
PILOT_MAX_SEATS = 10
TEAM_NAME_MIN, TEAM_NAME_MAX = 2, 60

# a team's wallet is the limiter; these only stop a runaway bug
TEAM_MONTHLY_USD = "5000.00"
TEAM_PER_USER_DAILY_USD = "1000.00"


class PlanError(Exception):
    status = 400


class PlanConflict(PlanError):
    status = 409


@dataclass(frozen=True)
class PlanOrder:
    plan: str                    # 'pro' | 'pilot'
    seats: int
    team_name: Optional[str]     # set only for a pilot of two or more seats
    amount_paise: int
    period_days: int
    included_usd: Decimal

    @property
    def creates_team(self) -> bool:
        return self.plan == "pilot" and self.seats >= 2


def _usd(name: str, default: str, env: Optional[Mapping[str, str]]) -> Decimal:
    import os
    raw = (os.environ if env is None else env).get(name)
    try:
        v = Decimal(str(raw)) if raw not in (None, "") else Decimal(default)
        return v if v > 0 else Decimal(default)
    except Exception:  # noqa: BLE001
        return Decimal(default)


def included_usd(plan: str, seats: int, env: Optional[Mapping[str, str]] = None) -> Decimal:
    """Usage that comes with a plan, in dollars: Pro $5; Pilot $4 per seat (founders' starting figures)."""
    if plan == "pro":
        return _usd("KELL_PRO_INCLUDED_USD", "5", env)
    return _usd("KELL_PILOT_SEAT_USD", "4", env) * seats


def clean_team_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not TEAM_NAME_MIN <= len(name) <= TEAM_NAME_MAX:
        raise PlanError(f"team_name must be {TEAM_NAME_MIN} to {TEAM_NAME_MAX} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise PlanError("team_name has characters that are not allowed")
    return name


def parse_plan_order(data: Any, env: Optional[Mapping[str, str]] = None) -> PlanOrder:
    """The request body -> the order, with the amount worked out here. Raises PlanError for anything else."""
    if not isinstance(data, dict):
        raise PlanError("the request body must be a JSON object")
    plan = data.get("plan")
    if plan == "pro":
        return PlanOrder("pro", 1, None, PRO_INR * 100, PRO_DAYS, included_usd("pro", 1, env))
    if plan == "pilot":
        raw = data.get("seats")
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise PlanError("seats must be a whole number")
        try:
            seats = int(raw)
        except ValueError:
            raise PlanError("seats must be a whole number")
        if not PILOT_MIN_SEATS <= seats <= PILOT_MAX_SEATS:
            raise PlanError(f"a pilot has {PILOT_MIN_SEATS} to {PILOT_MAX_SEATS} seats")
        team = clean_team_name(data.get("team_name")) if seats >= 2 else None
        return PlanOrder("pilot", seats, team, PILOT_SEAT_INR * seats * 100, PILOT_DAYS, included_usd("pilot", seats, env))
    raise PlanError("plan must be 'pro' or 'pilot'")


def team_slug(payment_id: str) -> str:
    """Derived from the payment id, so a retried webhook finds the team it already made."""
    return "team-" + hashlib.sha1(payment_id.encode("utf-8")).hexdigest()[:16]


def period_end(now: datetime, days: int, current_end: Optional[datetime]) -> tuple[datetime, datetime]:
    """(starts, ends). A renewal starts when the current period ends, so a person never loses days by renewing early."""
    start = current_end if current_end and current_end > now else now
    return start, start + timedelta(days=days)


async def has_pilot(pool: Any, buyer_user_id: str) -> bool:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM plan_entitlements WHERE buyer_user_id = $1::uuid AND plan = 'pilot' LIMIT 1", buyer_user_id))


async def current(pool: Any, buyer_user_id: str) -> Optional[dict[str, Any]]:
    """The buyer's plan with the latest end date (an ended plan is returned with a past end, so the page can say it ended)."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT plan, seats, team_name, ends_at FROM plan_entitlements WHERE buyer_user_id = $1::uuid "
            "ORDER BY ends_at DESC LIMIT 1", buyer_user_id)
    if row is None:
        return None
    return {"plan": row["plan"], "seats": int(row["seats"]), "team_name": row["team_name"],
            "active_until": row["ends_at"].isoformat()}


async def _ensure_team(pool: Any, *, payment_id: str, team_name: str, buyer_user_id: str) -> str:
    """The team organization for this payment, made once: the organization, the buyer as owner, and a wallet-required policy."""
    slug = team_slug(payment_id)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        org_id = await conn.fetchval("SELECT id::text FROM organizations WHERE slug = $1 AND t_expired IS NULL", slug)
        if not org_id:
            org_id = await conn.fetchval(
                "INSERT INTO organizations (name, slug) VALUES ($1, $2) ON CONFLICT (slug) DO NOTHING RETURNING id::text",
                team_name, slug)
        if not org_id:
            org_id = await conn.fetchval("SELECT id::text FROM organizations WHERE slug = $1", slug)
    async with tenant_transaction(pool, TenantScope.for_tenant(org_id)) as conn:
        await conn.execute(
            "INSERT INTO org_policies (organization_id, allowed_providers, allowed_models, allowed_tools, "
            "allowed_data_classes, monthly_budget_usd, per_user_daily_budget_usd, wallet_required, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6::numeric, $7::numeric, TRUE, 'pilot_purchase') ON CONFLICT DO NOTHING",
            org_id, list(accounts.PROVIDERS), list(accounts.MODELS), list(accounts.TOOLS), list(accounts.DATA_CLASSES),
            TEAM_MONTHLY_USD, TEAM_PER_USER_DAILY_USD)
        await conn.execute(
            "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) "
            "ON CONFLICT DO NOTHING", org_id, buyer_user_id, OWNER_ROLE_ID)
    return org_id


async def activate(pool: Any, *, order: Mapping[str, Any], payment_id: str, amount_paise: int,
                   now: Optional[datetime] = None, env: Optional[Mapping[str, str]] = None) -> bool:
    """Start the plan a verified payment bought. True when activated now, False when this payment was already processed.

    `order` is the billing_orders row we recorded (never the webhook body). Every step is idempotent and the entitlement
    row is written last, so a retry after a failure finishes the job."""
    plan, seats = order.get("plan"), int(order.get("seats") or 1)
    buyer = str(order["buyer_user_id"])
    now = now or datetime.now(timezone.utc)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        if await conn.fetchval("SELECT 1 FROM plan_entitlements WHERE payment_ref = $1", payment_id):
            return False
        end_row = await conn.fetchval(
            "SELECT max(ends_at) FROM plan_entitlements WHERE buyer_user_id = $1::uuid AND plan = $2", buyer, plan)
    starts, ends = period_end(now, int(order["period_days"]), end_row)

    if plan == "pilot" and seats >= 2:
        account = await _ensure_team(pool, payment_id=payment_id, team_name=str(order["team_name"]), buyer_user_id=buyer)
    else:
        account = buyer
        await accounts.ensure_account(pool, user_id=buyer, env={"KELL_WALLETS": "on"})
    usd = included_usd(plan, seats, env)

    async with tenant_transaction(pool, TenantScope.for_tenant(account)) as conn:
        await conn.execute(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "VALUES ($1::uuid, $2, 'plan_included', 0, $3, 'plan') ON CONFLICT (payment_ref) DO NOTHING",
            account, f"{payment_id}:included", usd)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        done = await conn.fetchval(
            "INSERT INTO plan_entitlements (buyer_user_id, account_id, plan, seats, team_name, starts_at, ends_at, "
            "payment_ref, order_id, amount_paise) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10) "
            "ON CONFLICT DO NOTHING RETURNING 1",
            buyer, account, plan, seats, order.get("team_name") if seats >= 2 else None, starts, ends, payment_id,
            order.get("order_id"), amount_paise)
        if not done and not await conn.fetchval("SELECT 1 FROM plan_entitlements WHERE payment_ref = $1", payment_id):
            # the one-pilot-per-buyer index refused a SECOND pilot paid for at the same time as the first (the order route
            # blocks a second order, so only two open checkouts can do this). The money is taken and no plan was started:
            # a person must refund or extend it. Loud on purpose.
            log.error("plan activation refused for payment %s (buyer %s, plan %s): needs manual review", payment_id, buyer, plan)
    return bool(done)
