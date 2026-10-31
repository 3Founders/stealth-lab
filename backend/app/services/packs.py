"""Additional kell credits: fixed packs of usage a person can add on top of a plan.

A pack is a rupee price and a fixed number of credit-dollars (the unit the ledger is kept in). The dollars are fixed in this table,
not worked out from the exchange-rate and margin settings the free-form top-up uses, so the price list is simple to publish and
to test. Every pack costs MORE per credit-dollar than the best plan (Plus), so a plan is always the better way to buy usage and the
packs are the overflow when a plan's included usage runs out (a test pins this).

Rules:
  * Packs are for people with an ACTIVE plan (KELL_ADDON_REQUIRES_PLAN, on by default), which removes the arbitrage of buying usage
    in packs instead of a plan. Set it to "off" to let anyone buy packs.
  * The server works out the amount from the pack id. The browser never sends a price.
  * Credits bought in a pack do not expire, like any wallet credit, and are spendable only on kell (closed loop).
  * Activation is the same signed-webhook path as every other payment (wallet.credit_payment, idempotent on the payment id).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction
from app.services.plans import PlanError

# pack id -> (price in rupees before GST, credit dollars). A larger pack gives a little more per rupee.
PACKS: dict[str, tuple[int, str]] = {
    "credits_500": (500, "2.50"),       # Rs 200.0 per dollar
    "credits_1000": (1000, "5.25"),     # Rs 190.5
    "credits_2500": (2500, "13.50"),    # Rs 185.2
    "credits_5000": (5000, "28.00"),    # Rs 178.6
}


class PackNeedsPlan(PlanError):
    status = 403


@dataclass(frozen=True)
class PackOrder:
    pack: str
    amount_paise: int
    usd: Decimal


def requires_plan(env: Optional[Mapping[str, str]] = None) -> bool:
    return ((os.environ if env is None else env).get("KELL_ADDON_REQUIRES_PLAN") or "on").strip().lower() not in ("off", "0", "false", "no")


def listing() -> list[dict[str, Any]]:
    """The price list, for the website: id, price, credit dollars and rupees per credit-dollar."""
    return [{"id": k, "price_inr": inr, "credit_usd": usd, "inr_per_usd": f"{inr / float(usd):.1f}"}
            for k, (inr, usd) in PACKS.items()]


def parse_pack_order(data: Any) -> PackOrder:
    if not isinstance(data, dict):
        raise PlanError("the request body must be a JSON object")
    pack = data.get("pack")
    if not isinstance(pack, str) or pack not in PACKS:
        raise PlanError("pack must be one of: " + ", ".join(PACKS))
    inr, usd = PACKS[pack]
    return PackOrder(pack, inr * 100, Decimal(usd))


async def has_active_plan(pool: Any, buyer_user_id: str) -> bool:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM plan_entitlements WHERE buyer_user_id = $1::uuid AND ends_at > now() LIMIT 1", buyer_user_id))


async def check_allowed(pool: Any, buyer_user_id: str, env: Optional[Mapping[str, str]] = None) -> None:
    if requires_plan(env) and not await has_active_plan(pool, buyer_user_id):
        raise PackNeedsPlan("additional credits are for people with an active plan; buy a plan first")
