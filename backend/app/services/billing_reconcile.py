"""Catch the payments whose webhook never arrived.

Razorpay webhooks are at-least-once but not guaranteed (a webhook that fails for 24 hours is disabled, and a server that was
down loses nothing only if somebody asks again). Razorpay's own guidance is to fall back to fetching the order's payments. This
job does that for the orders we recorded in the last few days: for each, it asks Razorpay for the payments on the order and, for
a payment that is captured, INR and exactly the amount we recorded, runs the SAME activation the webhook runs (plans.activate
for a plan order, wallet.credit_payment for a credit order). Both are idempotent on the payment id, so running this next to the
webhook, or twice, changes nothing the second time.

Run it by hand or from a scheduler:   python -m app.services.billing_reconcile [--apply] [--minutes 10] [--days 3]
Without --apply it only reports what it would activate. It never touches an order younger than --minutes, which the webhook is
probably still handling.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from app.services import plans, wallet
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

FetchPayments = Callable[[str], Awaitable[list[dict[str, Any]]]]


async def razorpay_order_payments(order_id: str) -> list[dict[str, Any]]:
    """GET /v1/orders/{id}/payments -> the payment entities on that order."""
    from app.providers.adapters import http_client

    auth = (os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
    async with http_client(timeout=30.0) as client:
        r = await client.get(f"https://api.razorpay.com/v1/orders/{order_id}/payments", auth=auth)
    if r.status_code >= 300:
        raise RuntimeError(f"Razorpay answered {r.status_code} for order {order_id}")
    return list((r.json() or {}).get("items") or [])


async def recent_orders(pool: Any, *, older_than_minutes: int, within_days: int, limit: int) -> list[dict[str, Any]]:
    """Orders old enough that the webhook should have finished, new enough to be worth asking about, whose plan (if any) is not
    already active. billing_orders and plan_entitlements are not row-secured, so no tenant is needed."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        rows = await conn.fetch(
            "SELECT o.order_id, o.organization_id::text AS organization_id, o.amount_paise, o.plan, o.seats, o.team_name, "
            "o.buyer_user_id::text AS buyer_user_id, o.period_days FROM billing_orders o "
            "WHERE o.created_at < now() - make_interval(mins => $1) AND o.created_at > now() - make_interval(days => $2) "
            "AND NOT EXISTS (SELECT 1 FROM plan_entitlements e WHERE e.order_id = o.order_id) "
            "ORDER BY o.created_at DESC LIMIT $3", int(older_than_minutes), int(within_days), int(limit))
    return [dict(r) for r in rows]


async def reconcile(pool: Any, *, fetch_payments: FetchPayments = razorpay_order_payments, apply: bool = False,
                    older_than_minutes: int = 10, within_days: int = 3, limit: int = 200) -> dict[str, Any]:
    """Returns {"checked", "found", "activated", "errors"}. `found` counts matching captured payments, `activated` those that
    changed anything now (apply only)."""
    out: dict[str, Any] = {"checked": 0, "found": 0, "activated": 0, "errors": 0}
    for order in await recent_orders(pool, older_than_minutes=older_than_minutes, within_days=within_days, limit=limit):
        out["checked"] += 1
        try:
            payments = await fetch_payments(order["order_id"])
        except Exception:  # noqa: BLE001 - one bad order must not stop the sweep
            log.warning("reconcile: could not fetch payments for order %s", order["order_id"], exc_info=True)
            out["errors"] += 1
            continue
        for pay in payments:
            if (pay.get("status") != "captured" or pay.get("currency", "INR") != "INR"
                    or int(pay.get("amount") or 0) != int(order["amount_paise"]) or not pay.get("id")):
                continue
            out["found"] += 1
            if not apply:
                continue
            try:
                if order.get("plan"):
                    done = await plans.activate(pool, order=order, payment_id=str(pay["id"]), amount_paise=int(pay["amount"]))
                else:
                    done = await wallet.credit_payment(pool, org_id=order["organization_id"], payment_ref=str(pay["id"]),
                                                       amount_paise=int(pay["amount"]), created_by="reconcile",
                                                       order_id=order["order_id"])
                out["activated"] += 1 if done else 0
            except Exception:  # noqa: BLE001
                log.exception("reconcile: activating payment %s failed", pay.get("id"))
                out["errors"] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Activate captured Razorpay payments whose webhook never arrived.")
    ap.add_argument("--apply", action="store_true", help="activate them (default: report only)")
    ap.add_argument("--minutes", type=int, default=10, help="ignore orders younger than this")
    ap.add_argument("--days", type=int, default=3, help="look back this far")
    a = ap.parse_args()

    async def run() -> dict[str, Any]:
        from app.db.session import create_pool

        pool = await create_pool()
        try:
            return await reconcile(pool, apply=a.apply, older_than_minutes=a.minutes, within_days=a.days)
        finally:
            await pool.close()

    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
