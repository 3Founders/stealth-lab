"""Auto-renew with Razorpay Subscriptions (docs/autopay.md).

What is built:
  - an opt-in on a ladder plan's card creates a Razorpay Subscription with 30-day cycles (period `daily`, interval 30;
    Razorpay's minimum daily interval is 7) and `customer_notify` on, so Razorpay sends the mandate messages;
  - the webhook activates the plan from the subscription row on the first and every later charge, checking the currency
    and that the amount is exactly what the row recorded; a mismatch is logged for manual review, never credited;
  - the buyer cancels from /account/billing, which cancels the Razorpay subscription (no future charges).
The one-person First Trial is NOT offered with auto-renew: it is one per account, so a second charge would be refused
by the entitlement index and need manual review. Not built: refunds (manual, as today), plan changes on a running
mandate, proration.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.services import plans
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

RAZORPAY_API = "https://api.razorpay.com/v1"
# 120 cycles of 30 days is about 9.9 years, inside Razorpay's 10-year ceiling. Cancel ends it earlier.
TOTAL_COUNT = 120
AUTOPAY_PLANS = ("starter", "pro", "plus")


class SubscriptionError(Exception):
    status = 400


def _auth() -> tuple[str, str]:
    return os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    from app.providers.adapters import http_client

    async with http_client(timeout=30.0) as client:
        r = await client.post(f"{RAZORPAY_API}{path}", auth=_auth(), json=body)
    if r.status_code >= 300:
        log.warning("razorpay %s refused with %s", path, r.status_code)
        raise SubscriptionError("auto-renew is not switched on with the payment provider yet; buy the plan once instead")
    return r.json()


async def _row(pool: Any, razorpay_subscription_id: str) -> Optional[dict[str, Any]]:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT buyer_user_id::text AS buyer_user_id, plan, seats, amount_paise, period_days, status "
            "FROM billing_subscriptions WHERE razorpay_subscription_id = $1", razorpay_subscription_id)
    return dict(row) if row else None


async def _razorpay_plan(pool: Any, plan: str, tier: plans.TierRow) -> str:
    """The Razorpay Plan for this row, made once per (amount, period); a dashboard edit to either makes a new one."""
    if tier.period_days < 7:
        raise SubscriptionError("this plan's period is too short for auto-renew")
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT rzp_plan_id, rzp_plan_paise, rzp_plan_days FROM billing_plan_rows WHERE plan = $1", plan)
    if row and row["rzp_plan_id"] and int(row["rzp_plan_paise"] or 0) == tier.price_paise and int(row["rzp_plan_days"] or 0) == tier.period_days:
        return str(row["rzp_plan_id"])
    made = await _post("/plans", {
        "period": "daily", "interval": tier.period_days,
        "item": {"name": f"kell {plan} ({tier.period_days} days)", "amount": tier.price_paise, "currency": "INR",
                 "description": f"kell plan, charged every {tier.period_days} days until cancelled"},
    })
    plan_id = str(made.get("id") or "")
    if not plan_id:
        raise SubscriptionError("auto-renew is not switched on with the payment provider yet; buy the plan once instead")
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute(
            "UPDATE billing_plan_rows SET rzp_plan_id = $2, rzp_plan_paise = $3, rzp_plan_days = $4 WHERE plan = $1",
            plan, plan_id, tier.price_paise, tier.period_days)
    return plan_id


async def create(pool: Any, *, buyer_user_id: str, plan: str, seats: int, referral_code: Optional[str]) -> dict[str, Any]:
    """Start an opt-in auto-renew: the Razorpay Subscription the Checkout then authorises. Nothing is charged here."""
    if plan not in AUTOPAY_PLANS:
        raise SubscriptionError("this plan is a one-time purchase; auto-renew is not offered")
    tier = (await plans.active_map(pool)).get(plan)
    if tier is None:
        raise SubscriptionError("this plan is not open for buying right now; contact us")
    rzp_plan = await _razorpay_plan(pool, plan, tier)
    made = await _post("/subscriptions", {
        "plan_id": rzp_plan, "total_count": TOTAL_COUNT, "quantity": seats, "customer_notify": True,
        "notes": {"kell_plan": plan, "buyer": str(buyer_user_id)},
    })
    subscription_id = str(made.get("id") or "")
    if not subscription_id:
        raise SubscriptionError("auto-renew could not be started; buy the plan once instead")
    amount = tier.price_paise * seats
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute(
            "INSERT INTO billing_subscriptions (buyer_user_id, plan, seats, referral_code, amount_paise, period_days, "
            "razorpay_subscription_id, status) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, 'created')",
            str(buyer_user_id), plan, seats, referral_code, amount, tier.period_days, subscription_id)
    return {"subscription_id": subscription_id, "plan": plan, "seats": seats, "period_days": tier.period_days,
            "amount_paise": amount}


async def by_buyer(pool: Any, buyer_user_id: str) -> Optional[dict[str, Any]]:
    """The account's running auto-renew, for /account/billing. None when there is none."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT plan, seats, status, current_end, amount_paise, period_days FROM billing_subscriptions "
            "WHERE buyer_user_id = $1::uuid AND status NOT IN ('cancelled', 'completed', 'expired') "
            "ORDER BY created_at DESC LIMIT 1", str(buyer_user_id))
    if row is None:
        return None
    return {"plan": row["plan"], "seats": int(row["seats"]), "status": row["status"],
            "current_end": row["current_end"].isoformat() if row["current_end"] else None,
            "amount_paise": int(row["amount_paise"]), "period_days": int(row["period_days"])}


async def cancel(pool: Any, buyer_user_id: str) -> dict[str, Any]:
    """Stop future charges. Razorpay cancels the mandate; the plan already paid for keeps running to its end."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT razorpay_subscription_id FROM billing_subscriptions WHERE buyer_user_id = $1::uuid "
            "AND status NOT IN ('cancelled', 'completed', 'expired') ORDER BY created_at DESC LIMIT 1", str(buyer_user_id))
    if row is None:
        raise SubscriptionError("there is no auto-renew to cancel")
    sid = str(row["razorpay_subscription_id"])
    await _post(f"/subscriptions/{sid}/cancel", {"cancel_at_cycle_end": False})
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute("UPDATE billing_subscriptions SET status = 'cancelled', cancelled_at = now() "
                           "WHERE razorpay_subscription_id = $1", sid)
    return {"cancelled": True}


async def on_event(pool: Any, event: str, body: dict[str, Any]) -> None:
    """A subscription webhook. Activation is idempotent by payment id, so an authenticated+charged pair credits once."""
    payload = body.get("payload") or {}
    sub = ((payload.get("subscription") or {}).get("entity")) or {}
    sid = sub.get("id")
    if not sid:
        return
    sid = str(sid)
    if event in ("subscription.authenticated", "subscription.charged"):
        pay = ((payload.get("payment") or {}).get("entity")) or {}
        await _activate_charge(pool, sid, pay)
        if event == "subscription.authenticated":
            await _mark(pool, sid, "authenticated")
        return
    status = {"subscription.activated": "active", "subscription.pending": "pending", "subscription.halted": "halted",
              "subscription.cancelled": "cancelled", "subscription.completed": "completed",
              "subscription.paused": "paused", "subscription.resumed": "active"}.get(event)
    if status:
        await _mark(pool, sid, status)


async def _mark(pool: Any, sid: str, status: str) -> None:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute(
            "UPDATE billing_subscriptions SET status = $2, cancelled_at = CASE WHEN $2 = 'cancelled' THEN now() "
            "ELSE cancelled_at END WHERE razorpay_subscription_id = $1", sid, status)


async def _activate_charge(pool: Any, sid: str, pay: dict[str, Any]) -> None:
    payment_id = str(pay.get("id") or "")
    if not payment_id:
        log.warning("billing: subscription %s event carried no payment", sid)
        return
    row = await _row(pool, sid)
    if row is None:
        log.warning("billing: subscription %s is not one we recorded", sid)
        return
    amount = int(pay.get("amount") or 0)
    if pay.get("currency", "INR") != "INR" or amount != int(row["amount_paise"]):
        log.error("billing: subscription %s charge %s (%s paise) does not match the recorded %s paise; manual review",
                  sid, payment_id, amount, row["amount_paise"])
        return
    done = await plans.activate(pool, order={
        "plan": row["plan"], "seats": int(row["seats"]), "team_name": None,
        "buyer_user_id": str(row["buyer_user_id"]), "period_days": int(row["period_days"]), "order_id": payment_id,
    }, payment_id=payment_id, amount_paise=amount)
    if done:
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            await conn.execute(
                "UPDATE billing_subscriptions SET status = 'active', "
                "current_end = now() + make_interval(days => $2) WHERE razorpay_subscription_id = $1",
                sid, int(row["period_days"]))
