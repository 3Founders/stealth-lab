"""The plans a person can buy with Razorpay: Starter, Pro and Plus (30 days each) and the Pilot (30 days, 1 to 10 seats; a one-seat Pilot is the individual pilot).

Prices are the founders' (kell_decks/4_kell_router_and_coding_agents.html, slides 7-8; docs/billing.md "Plans"), in rupees
before GST. The browser never sends a price: it sends a plan name, a seat count and a team name, and the server works out
the amount, records the order, and activates the plan only from a payment Razorpay signed for an order WE recorded.

Since 2026-11-02 the numbers live in billing_plan_rows (db/167) and are edited in the admin dashboard's Billing page
(app/api/admin_billing.py). A changed price applies to orders made after it: the order row keeps the amount an order was
made with, never the table's current one. TIERS below is the same numbers in code - the seed the migration inserts and
what the server falls back to when the table is not there yet (a deploy that has not run the migration cannot activate
moneys). Retired: the old KELL_STARTER_INCLUDED_USD / KELL_PRO_INCLUDED_USD / KELL_PLUS_INCLUDED_USD /
KELL_PILOT_SEAT_USD overrides; the dashboard is the one knob.

What a plan is, in money terms: the rupees buy the plan for a period, and the plan comes with included usage credited to
a wallet once, at purchase (Starter $1, Pro $6, Plus $12; Pilot $4 per seat). That credit is ordinary wallet credit: it
is spent by calls and does not expire when the period ends. Margin plays no part in it (the credit is a fixed dollar
amount, not paise run through KELL_MARGIN), so the two numbers are set independently.

A one-seat Pilot is the individual pilot, and it is bought online only with an active referral code
(services/referrals.py): the request carries "referral_code", the route checks it and the order row records it. Two or
more seats is the team pilot and needs no code.

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
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Collection, Mapping, NamedTuple, Optional

from app.services import accounts, referrals
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

OWNER_ROLE_ID = "10000000-0000-0000-0000-000000000001"      # db/28_identity.sql

# The tier ladder (rupees, before GST). Starter anchors the low end, Pro is the step up, Plus is the one the page recommends:
# it gives twice Pro's usage for 800 rupees more. Every tier is a real, honestly described option; the page may not invent
# urgency or strike-through prices. Per-rupee usage: Starter and Pro 0.005 dollars, Plus 0.006. These are ALSO the numbers
# seeded into billing_plan_rows (db/167); the editable copy of them is the dashboard's, not an environment variable.
TIERS = {
    "starter": {"inr": 200, "days": 30, "usd": "1", "rank": 1},
    "pro":     {"inr": 1200, "days": 30, "usd": "6", "rank": 2},
    "plus":    {"inr": 2000, "days": 30, "usd": "12", "rank": 3},
}
PRO_INR = TIERS["pro"]["inr"]
PILOT_SEAT_INR = 649
PRO_DAYS = 30
PILOT_DAYS = 30
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
    plan: str                    # 'starter' | 'pro' | 'plus' | 'pilot'
    seats: int
    team_name: Optional[str]     # set only for a pilot of two or more seats
    amount_paise: int
    period_days: int
    included_usd: Decimal
    referral_code: Optional[str] = None   # the code a one-seat pilot was bought with, or the one a team order carried

    @property
    def creates_team(self) -> bool:
        return self.plan == "pilot" and self.seats >= 2


class TierRow(NamedTuple):
    """One row of billing_plan_rows, as the server computes an order or the dashboard edits it. The Pilot's price and
    usage are PER SEAT. `active` false means the plan is not being sold (an order already made for it still activates).
    `max_redemptions` caps how many people may ever buy it (None = no limit)."""
    plan: str
    price_paise: int
    period_days: int
    included_usd: Decimal
    active: bool = True
    max_redemptions: Optional[int] = None


def _defaults() -> dict[str, TierRow]:
    d = {p: TierRow(p, t["inr"] * 100, t["days"], Decimal(t["usd"])) for p, t in TIERS.items()}
    d["pilot"] = TierRow("pilot", PILOT_SEAT_INR * 100, PILOT_DAYS, Decimal("4"), max_redemptions=101)
    return d


DEFAULT_TIERS: dict[str, TierRow] = _defaults()


async def _rows(pool: Any) -> Optional[Mapping[str, TierRow]]:
    """Every plan row, or None when the table is not there yet (a deploy before migration 167): never a crashed request."""
    try:
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            rows = await conn.fetch("SELECT plan, price_paise, period_days, included_usd, active, max_redemptions FROM billing_plan_rows")
    except Exception:  # noqa: BLE001
        log.exception("billing: reading plan rows failed; using the built-in tiers")
        return None
    if not rows:
        return None
    return {r["plan"]: TierRow(r["plan"], int(r["price_paise"]), int(r["period_days"]), Decimal(str(r["included_usd"])),
                               bool(r["active"]),
                               int(r["max_redemptions"]) if r["max_redemptions"] is not None else None) for r in rows}


async def active_map(pool: Any) -> Mapping[str, TierRow]:
    """The plans being sold now. An empty result when the dashboard turned them all off; the built-in defaults only when
    the table is missing or empty (not migrated yet)."""
    rows = await _rows(pool)
    if rows is None:
        return DEFAULT_TIERS
    return {plan: row for plan, row in rows.items() if row.active}


async def every_map(pool: Any) -> Mapping[str, TierRow]:
    """Every row including inactive ones: an activation must price the plan the order was made for, even if the
    dashboard turned that plan off between the order and the payment."""
    rows = await _rows(pool)
    return DEFAULT_TIERS if rows is None else rows


async def all_rows(pool: Any) -> list[dict[str, Any]]:
    """Every plan row including inactive ones, with how many people have it, for the dashboard."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        rows = await conn.fetch(
            "SELECT r.plan, r.price_paise, r.period_days, r.included_usd, r.active, r.max_redemptions, r.updated_by, r.updated_at, "
            "(SELECT count(*) FROM plan_entitlements e WHERE e.plan = r.plan AND e.seats = 1) AS sold "
            "FROM billing_plan_rows r ORDER BY r.plan")
    return [{**dict(r), "included_usd": str(r["included_usd"]), "sold": int(r["sold"]),
             "updated_at": r["updated_at"].isoformat()} for r in rows]


def row_fields(body: Any) -> dict[str, Any]:
    """The fields an admin PUT may set (price_paise, period_days, included_usd, active), checked: raises PlanError for a
    bad value. Only these four; the plan id and the sentence copy on the cards are code, not dashboard data."""
    if not isinstance(body, dict):
        raise PlanError("the request body must be a JSON object")
    out: dict[str, Any] = {}
    if "price_paise" in body:
        p = body["price_paise"]
        if isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= 500_000 or p % 100:
            raise PlanError("price_paise must be a whole number of rupees: paise from 100 to 500000")
        out["price_paise"] = p
    if "period_days" in body:
        d = body["period_days"]
        if isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 365:
            raise PlanError("period_days must be a whole number of days from 1 to 365")
        out["period_days"] = d
    if "included_usd" in body:
        try:
            u = Decimal(str(body["included_usd"]))
        except Exception:  # noqa: BLE001
            raise PlanError("included_usd must be a number like 4 or 6.5")
        if not u > 0 or u > 10_000:
            raise PlanError("included_usd must be a positive number of dollars, at most 10000")
        out["included_usd"] = u
    if "active" in body:
        if not isinstance(body["active"], bool):
            raise PlanError("active must be true or false")
        out["active"] = body["active"]
    if "max_redemptions" in body:
        v = body["max_redemptions"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            raise PlanError("max_redemptions must be a whole number of people, or null for no limit")
        out["max_redemptions"] = v
    return out


async def save_row(pool: Any, plan: str, fields: Mapping[str, Any], updated_by: str) -> Optional[TierRow]:
    """Apply a dashboard edit to one plan row. None when there is no such row (the dashboard gets a 404)."""
    if not fields:
        raise PlanError("send what changes: price_paise, period_days, included_usd, active, max_redemptions")
    known = ("price_paise", "period_days", "included_usd", "active", "max_redemptions")
    if [k for k in fields if k not in known]:
        raise PlanError("only price_paise, period_days, included_usd, active and max_redemptions can be edited")
    sets = ["updated_by = $2", "updated_at = now()"]
    args: list[Any] = [plan, updated_by]
    for name in known:
        if name in fields:
            args.append(fields[name])
            sets.append(f"{name} = ${len(args)}")
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            f"UPDATE billing_plan_rows SET {', '.join(sets)} WHERE plan = $1 "
            "RETURNING plan, price_paise, period_days, included_usd, active, max_redemptions", *args)
    if row is None:
        return None
    return TierRow(row["plan"], int(row["price_paise"]), int(row["period_days"]), Decimal(str(row["included_usd"])),
                   bool(row["active"]),
                   int(row["max_redemptions"]) if row["max_redemptions"] is not None else None)


async def redeemed(pool: Any, plan: str) -> int:
    """People who have the one-person plan plus one-person orders started in the last hour that are not activated yet:
    a slot is held while Checkout is open, and an abandoned order frees its slot after the hour. Team pilots (two or
    more seats, invoiced directly) are a separate sale and are not counted against the launch cap."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM plan_entitlements WHERE plan = $1 AND seats = 1) + "
            "(SELECT count(*) FROM billing_orders o WHERE o.plan = $1 AND o.seats = 1 "
            "AND o.created_at > now() - interval '1 hour' "
            "AND NOT EXISTS (SELECT 1 FROM plan_entitlements e WHERE e.order_id = o.order_id)) AS n", plan)
    return int(row["n"]) if row else 0


async def cap_reached(pool: Any, plan: str, tier: Optional[TierRow]) -> bool:
    """True when the plan's redemption limit has no room left. No limit (or no row) is never reached."""
    if tier is None or tier.max_redemptions is None:
        return False
    return await redeemed(pool, plan) >= tier.max_redemptions


async def user_by_email(pool: Any, email: str) -> Optional[dict[str, Any]]:
    """The account with this email, or None. Only people who have signed in at least once exist here; a grant cannot
    create an account."""
    addr = str(email or "").strip()
    if "@" not in addr or len(addr) > 254:
        raise PlanError("enter the email they signed up with")
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT id::text AS user_id, display_name, email FROM users WHERE lower(email) = lower($1) AND is_active "
            "ORDER BY t_created DESC LIMIT 1", addr)
    return dict(row) if row else None


async def holders(pool: Any, plan: str) -> list[dict[str, Any]]:
    """Who has the one-person plan, newest first: bought or added by the founders, and until when."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        rows = await conn.fetch(
            "SELECT e.buyer_user_id::text AS user_id, u.display_name, u.email, e.starts_at, e.ends_at, e.payment_ref "
            "FROM plan_entitlements e LEFT JOIN users u ON u.id = e.buyer_user_id "
            "WHERE e.plan = $1 AND e.seats = 1 ORDER BY e.starts_at DESC LIMIT 200", plan)
    return [{"user_id": r["user_id"], "display_name": r["display_name"], "email": r["email"],
             "since": r["starts_at"].isoformat(), "until": r["ends_at"].isoformat(),
             "source": "grant" if str(r["payment_ref"] or "").startswith("grant:") else "paid"} for r in rows]


async def grant(pool: Any, *, plan: str, email: str, granted_by: str) -> dict[str, Any]:
    """Put a person on a plan for one period, at no charge, with the included usage. The dashboard states exactly what
    this does before confirming; the audit log records who did it. It uses one place in a capped launch."""
    tier = (await every_map(pool)).get(plan)
    if tier is None:
        raise PlanError("no such plan")
    if await cap_reached(pool, plan, tier):
        raise PlanError("this launch is full; raise the limit first")
    user = await user_by_email(pool, email)
    if user is None:
        raise PlanError("no account with that email yet; they need to sign in once first")
    buyer = str(user["user_id"])
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        already = await conn.fetchval(
            "SELECT 1 FROM plan_entitlements WHERE buyer_user_id = $1::uuid AND plan = $2 AND ends_at > now() LIMIT 1",
            buyer, plan)
    if already or (plan == "pilot" and await has_pilot(pool, buyer)):
        raise PlanError("they have already had this plan")
    if not await accounts.ensure_account(pool, user_id=buyer, env={"KELL_WALLETS": "on"}):
        raise PlanError("their wallet account could not be set up; try again in a moment")
    now = datetime.now(timezone.utc)
    ends = now + timedelta(days=int(tier.period_days))
    ref = "grant:" + uuid.uuid4().hex
    async with tenant_transaction(pool, TenantScope.for_tenant(buyer)) as conn:
        await conn.execute(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "VALUES ($1::uuid, $2, 'grant_included', 0, $3, $4)",
            buyer, f"{ref}:included", tier.included_usd, granted_by)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute(
            "INSERT INTO plan_entitlements (buyer_user_id, account_id, plan, seats, team_name, starts_at, ends_at, "
            "payment_ref, order_id, amount_paise) VALUES ($1::uuid, $1::uuid, $2, 1, NULL, $3, $4, $5, NULL, 0)",
            buyer, plan, now, ends, ref)
    return {"user_id": buyer, "display_name": user["display_name"], "email": user["email"], "plan": plan,
            "since": now.isoformat(), "until": ends.isoformat(), "included_usd": str(tier.included_usd)}


def _ti(tiers: Optional[Mapping[str, TierRow]]) -> Mapping[str, TierRow]:
    return DEFAULT_TIERS if tiers is None else tiers


def included_usd(plan: str, seats: int, tiers: Optional[Mapping[str, TierRow]] = None) -> Decimal:
    """Usage that comes with a plan, in dollars: Starter $1, Pro $6, Plus $12; Pilot $4 per seat (from the tier rows;
    the built-in defaults when the table is not there yet). An unknown plan is an error, never a silent $0."""
    row = _ti(tiers).get(plan)
    if row is None:
        raise PlanError(f"unknown plan {plan!r}")
    return row.included_usd * seats


def clean_team_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not TEAM_NAME_MIN <= len(name) <= TEAM_NAME_MAX:
        raise PlanError(f"team_name must be {TEAM_NAME_MIN} to {TEAM_NAME_MAX} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise PlanError("team_name has characters that are not allowed")
    return name


def parse_plan_order(data: Any, tiers: Optional[Mapping[str, TierRow]] = None, codes: Collection[str] = ()) -> PlanOrder:
    """The request body -> the order, with the amount worked out here from the tier rows and never taken from the request.
    `tiers` is the active map from billing_plan_rows (plans.active_map; the defaults when the table is not there yet) and
    `codes` the referral codes that unlock the one-seat pilot (referrals.active_codes). Raises PlanError for anything else."""
    if not isinstance(data, dict):
        raise PlanError("the request body must be a JSON object")
    t = _ti(tiers)
    plan = data.get("plan")
    if plan in TIERS:
        tier = t.get(plan)
        if tier is None:
            raise PlanError(f"{plan} is not open for buying right now; contact us")
        return PlanOrder(plan, 1, None, tier.price_paise, tier.period_days, tier.included_usd)
    if plan == "pilot":
        raw = data.get("seats", 1)          # the one-person plan is one seat; the page need not send it
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise PlanError("seats must be a whole number")
        try:
            seats = int(raw)
        except ValueError:
            raise PlanError("seats must be a whole number")
        if not PILOT_MIN_SEATS <= seats <= PILOT_MAX_SEATS:
            raise PlanError(f"a team plan has {PILOT_MIN_SEATS} to {PILOT_MAX_SEATS} seats")
        tier = t.get("pilot")
        if tier is None:
            raise PlanError("this plan is not open for buying right now; contact us")
        given = data.get("referral_code")
        if seats == 1:
            why = referrals.failure_sentence(given, codes)
            if why:
                raise PlanError(why)
        team = clean_team_name(data.get("team_name")) if seats >= 2 else None
        code = given.strip().upper() if isinstance(given, str) and given.strip() else None
        return PlanOrder("pilot", seats, team, tier.price_paise * seats, tier.period_days,
                         tier.included_usd * seats, code)
    raise PlanError("unknown plan")


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
    """The plan to show for a buyer: an active plan before an ended one, the higher tier before the lower, then the later end.
    An ended plan comes back with a past end, so the page can say it ended."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT plan, seats, team_name, ends_at FROM plan_entitlements WHERE buyer_user_id = $1::uuid "
            "ORDER BY (ends_at > now()) DESC, CASE plan WHEN 'plus' THEN 3 WHEN 'pro' THEN 2 WHEN 'starter' THEN 1 ELSE 0 END DESC, "
            "ends_at DESC LIMIT 1", buyer_user_id)
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
            await referrals.ensure_personal(pool, buyer)   # a repeat delivery still owes the buyer their code
            return False
        end_row = await conn.fetchval(
            "SELECT max(ends_at) FROM plan_entitlements WHERE buyer_user_id = $1::uuid AND plan = $2", buyer, plan)
    starts, ends = period_end(now, int(order["period_days"]), end_row)

    if plan == "pilot" and seats >= 2:
        account = await _ensure_team(pool, payment_id=payment_id, team_name=str(order["team_name"]), buyer_user_id=buyer)
    else:
        account = buyer
        await accounts.ensure_account(pool, user_id=buyer, env={"KELL_WALLETS": "on"})
    usd = included_usd(plan, seats, await every_map(pool))

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
    if done:
        await referrals.ensure_personal(pool, buyer)        # a paying customer gets their shareable code; rewards are not built
    return bool(done)
