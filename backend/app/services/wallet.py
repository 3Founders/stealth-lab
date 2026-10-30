"""Prepaid wallet: money in (a verified Razorpay payment), the balance, and the conversion between rupees and the dollars
the provider ledger is kept in.

The debit side is not stored here. provider_call_ledger already records every held or settled call, and the check in
org_governance.reserve_call refuses a call when (credits - ledger) < the call's worst case, for organizations whose policy
sets wallet_required. So the balance is derived, and cannot drift from what was spent.

Pricing rule (a founder decision, set by environment and shown to the buyer on the buy page, not hidden in code):
  credit_usd = (paise / 100) / KELL_INR_PER_USD / (1 + KELL_MARGIN)
i.e. the buyer pays the provider's dollar cost converted to rupees, plus the margin. Defaults are placeholders, not advice.

Webhook safety: the signature is Razorpay's HMAC-SHA256 of the raw body with the webhook secret; an order is matched to an
organization by the billing_orders row WE wrote when creating it, never by a field in the webhook; payment_ref is unique,
so a repeated delivery credits once.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from decimal import ROUND_DOWN, Decimal
from typing import Any, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

MIN_TOPUP_INR = 100
MAX_TOPUP_INR = 50_000


class WalletError(Exception):
    status = 400


def rates(env: Optional[Mapping[str, str]] = None) -> tuple[Decimal, Decimal]:
    """(rupees per dollar, margin). Invalid or non-positive values fall back to the defaults."""
    e = os.environ if env is None else env

    def num(name: str, default: str) -> Decimal:
        try:
            v = Decimal(str(e.get(name) or default))
            return v if v > 0 else Decimal(default)
        except Exception:  # noqa: BLE001
            return Decimal(default)

    return num("KELL_INR_PER_USD", "90"), num("KELL_MARGIN", "0.5")


def usd_for_paise(paise: int, env: Optional[Mapping[str, str]] = None) -> Decimal:
    inr_per_usd, margin = rates(env)
    return (Decimal(paise) / 100 / inr_per_usd / (1 + margin)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)


def verify_signature(raw_body: bytes, signature: Optional[str], secret: Optional[str]) -> bool:
    """Razorpay webhook signature: hex HMAC-SHA256 of the raw request body. False when anything is missing."""
    if not raw_body or not signature or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def _scope(org_id: str) -> TenantScope:
    return TenantScope.for_tenant(str(org_id))


async def balance(pool: Any, org_id: str) -> dict[str, Any]:
    """Purchased, spent (settled or held) and remaining dollars for an organization, plus its credit history."""
    async with tenant_transaction(pool, _scope(org_id)) as conn:
        bought = await conn.fetchval("SELECT COALESCE(sum(amount_usd), 0) FROM org_wallet_credits WHERE "
                                     "organization_id = $1::uuid", str(org_id))
        spent = await conn.fetchval(
            "SELECT COALESCE(sum(CASE WHEN status = 'settled' THEN cost_usd WHEN status = 'reserved' THEN reserved_usd "
            "ELSE 0 END), 0) FROM provider_call_ledger WHERE organization_id = $1::uuid", str(org_id))
        rows = await conn.fetch("SELECT payment_ref, source, amount_paise, amount_usd, created_at FROM org_wallet_credits "
                                "WHERE organization_id = $1::uuid ORDER BY created_at DESC LIMIT 50", str(org_id))
    b, s = Decimal(str(bought)), Decimal(str(spent))
    return {"purchased_usd": str(b), "spent_usd": str(s), "remaining_usd": str(b - s),
            "credits": [{"payment_ref": r["payment_ref"], "source": r["source"], "amount_paise": int(r["amount_paise"]),
                         "amount_usd": str(r["amount_usd"]), "at": r["created_at"].isoformat()} for r in rows]}


async def record_order(pool: Any, *, org_id: str, order_id: str, amount_paise: int, created_by: str) -> None:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute("INSERT INTO billing_orders (order_id, organization_id, amount_paise, created_by) "
                           "VALUES ($1, $2::uuid, $3, $4) ON CONFLICT (order_id) DO NOTHING",
                           order_id, str(org_id), amount_paise, created_by)


async def find_order(pool: Any, order_id: str) -> Optional[dict[str, Any]]:
    """The organization and amount we recorded for a gateway order (billing_orders is not row-secured: a webhook has no
    tenant, and an order id is unguessable and only maps to an organization id)."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT order_id, organization_id::text AS organization_id, amount_paise FROM "
                                  "billing_orders WHERE order_id = $1", order_id)
    return dict(row) if row else None


async def credit_payment(pool: Any, *, org_id: str, payment_ref: str, amount_paise: int, created_by: str,
                         source: str = "razorpay", env: Optional[Mapping[str, str]] = None, order_id: Optional[str] = None) -> bool:
    """Add a verified payment to the wallet. True when credited now, False when this payment was already credited."""
    if amount_paise <= 0:
        raise WalletError("a payment must be a positive amount")
    inr_per_usd, margin = rates(env)
    usd = usd_for_paise(amount_paise, env)
    if usd <= 0:
        raise WalletError("that amount is too small to credit")
    base = ("INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, inr_per_usd, margin, created_by{cols}) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8{vals}) ON CONFLICT (payment_ref) DO NOTHING RETURNING 1")
    args = (str(org_id), payment_ref, source, amount_paise, usd, inr_per_usd, margin, created_by)
    try:
        async with tenant_transaction(pool, _scope(org_id)) as conn:
            got = await conn.fetchval(base.format(cols=", order_id", vals=", $9"), *args, order_id)
    except Exception as exc:  # noqa: BLE001
        # The order_id column comes with migration 161. If this code is running before that migration, still credit the payment:
        # a customer's money must never wait on bookkeeping. (Reconciliation then shows the credit without an order.)
        if "order_id" not in str(exc):
            raise
        log.warning("credit_payment: order_id column missing (apply migration 161); crediting without it")
        async with tenant_transaction(pool, _scope(org_id)) as conn:
            got = await conn.fetchval(base.format(cols="", vals=""), *args)
    return bool(got)
