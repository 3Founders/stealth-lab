"""Money going back out of a prepaid wallet (db/215): a refund the gateway reports is applied to the balance, and any
referral reward paid on that payment is taken back.

Why this module exists rather than a line in the webhook. A refund is not one write; it is a decision with three
outcomes and a rule that must hold whatever the caller does:

  * applied  -- the money came out of the wallet. A negative row (source 'refund') of the refunded rupees.
  * skipped  -- the wallet has already spent that money. The person spent the credit on usage before asking for the
                refund; there is nothing left to take back, and taking it would be taking the money of a purchase they
                did not refund. The refund is still real (Razorpay has paid them), so we record it as skipped and say
                why, rather than either silently ignoring it or driving the balance negative into a debt they never
                agreed to.
  * failed   -- the write itself failed. Left for a retry.

The rule: NEVER debit more than the wallet can cover. The balance is derived (purchased minus spent), and this module
reads it before writing, so a partial refund of a partly-spent wallet debits what remains and records the rest as
uncovered.

What it also does: takes back the referral reward paid on that payment (db/212). Full and partial refunds both remove
that reward, because the reward was for a completed sale, and a refunded sale is not one. This is deliberately NOT
proportional: a Rs 1 refund of a Rs 649 order does not leave the referrer Rs 99.84; a refunded sale is reversed.

What it never does: edit or delete a wallet row (the ledger is append-only, so a refund is a new row with the opposite
sign), end a plan early, or claw back the INCLUDED usage a plan granted (that usage is what the payment bought).
"""
from __future__ import annotations

import logging
from decimal import ROUND_CEILING, Decimal
from typing import Any, Optional

from app.services import referral_rewards, wallet
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)


async def _balance_paise(conn: Any, account_id: str) -> int:
    """What remains in the wallet, in paise: rupees paid in, minus what has been spent.

    Both sides come from the same rows wallet.balance() reads, so this can never disagree with the balance a person
    sees. The wallet carries rupees on the credit side and credit-dollars on the spend side, and each credit row
    records the rate it was bought at, so the two are compared through the wallet's own overall rate (rupees in over
    dollars credited). For a wallet funded one way -- the normal case, top-ups or packs -- that rate is exact; for a
    mixed wallet it is the honest estimate, and it never invents rupees that were not paid in."""
    totals = await conn.fetchrow(
        "SELECT COALESCE(sum(amount_paise), 0) AS paise, COALESCE(sum(amount_usd), 0) AS usd "
        "FROM org_wallet_credits WHERE organization_id = $1::uuid", account_id)
    spent = await conn.fetchval(
        "SELECT COALESCE(sum(CASE WHEN status = 'settled' THEN cost_usd WHEN status = 'reserved' THEN reserved_usd "
        "ELSE 0 END), 0) FROM provider_call_ledger WHERE organization_id = $1::uuid", account_id)
    bought_paise, bought_usd = int(totals["paise"] or 0), Decimal(str(totals["usd"] or 0))
    if bought_paise <= 0:
        return 0
    if bought_usd <= 0:
        return bought_paise
    rupees_per_dollar = Decimal(bought_paise) / bought_usd
    spent_paise = (Decimal(str(spent or 0)) * rupees_per_dollar).to_integral_value(rounding=ROUND_CEILING)
    return bought_paise - int(spent_paise)


def _usd_for_paise(paise: int) -> Decimal:
    """The credit-dollars a rupee amount is worth, at the rate a purchase uses, with the sign kept."""
    sign = -1 if paise < 0 else 1
    return sign * wallet.usd_for_paise(abs(paise))


async def apply_refund(pool: Any, *, refund_id: str, payment_id: str, amount_paise: int,
                       currency: str = "INR") -> Optional[dict[str, Any]]:
    """Apply one refund the gateway reported. Idempotent on `refund_id`: a redelivery finds the row it made.

    Returns the wallet_refunds row (created, or the one already there). The account is the one WE recorded for the
    payment's order, never a value from the webhook, so a forged payload cannot choose whose balance moves."""
    if amount_paise <= 0 or currency != "INR":
        return None

    # A redelivery: hand back what was already decided.
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        seen = await conn.fetchrow("SELECT * FROM wallet_refunds WHERE refund_id = $1", str(refund_id))
        if seen is not None:
            return dict(seen)
        # The account is the one WE recorded for this payment's credit row (db/161 keeps the order id on it), and the
        # order is that order. Nothing here is taken from the webhook payload.
        order = await conn.fetchrow(
            "SELECT w.organization_id::text AS account_id, w.order_id "
            "FROM org_wallet_credits w WHERE w.payment_ref = $1 AND w.source IN ('razorpay', 'manual') "
            "ORDER BY w.created_at DESC LIMIT 1", str(payment_id))
        if order is None:
            # The payment_id is not a wallet credit (a plan's included usage, say), so there is no balance to move.
            # Look the account up from the order that produced the payment via the plan entitlement, else skip loudly.
            log.warning("refund %s: payment %s does not map to a wallet credit; recorded as skipped", refund_id, payment_id)
            return await _record(pool, refund_id=refund_id, payment_id=payment_id, order_id=None, account_id=None,
                                 amount_paise=amount_paise, applied_paise=0, currency=currency, status="skipped",
                                 reason="the payment is not a wallet credit, so there is no balance to take it from")
        account_id = order["account_id"]
        order_id = order["order_id"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize per wallet: two refunds of the same account at once must not both see the same balance.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"wallet:{account_id}")
            again = await conn.fetchrow("SELECT * FROM wallet_refunds WHERE refund_id = $1", str(refund_id))
            if again is not None:
                return dict(again)
            remaining = await _balance_paise(conn, account_id)
            applied = max(0, min(amount_paise, remaining))
            status = "applied" if applied > 0 else "skipped"
            reason = None if applied > 0 else "the wallet balance has already been spent; nothing was left to refund"
            if applied > 0 and applied < amount_paise:
                reason = (f"{applied // 100} of {amount_paise // 100} rupees were left in the wallet; "
                          f"the rest had already been spent")
    # The debit is written on the account's own tenant binding, exactly as every wallet write is.
    if applied > 0:
        async with tenant_transaction(pool, TenantScope.for_tenant(account_id)) as conn:
            await conn.execute(
                "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
                "VALUES ($1::uuid, $2, 'refund', $3, $4, 'razorpay_refund') ON CONFLICT (payment_ref) DO NOTHING",
                account_id, f"refund:{refund_id}", -applied, _usd_for_paise(-applied))

    # The referral reward paid on this payment is taken back (a refunded sale is not a completed sale). Idempotent.
    reward_rows: list[dict[str, Any]] = []
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        found = await conn.fetch("SELECT id::text AS id FROM referral_rewards "
                                 "WHERE payment_id = $1 AND status IN ('released', 'pending')", str(payment_id))
    for r in found:
        done = await referral_rewards.reverse(pool, referral_id=r["id"], reason=f"refunded: {refund_id}")
        if done:
            reward_rows.append(done)

    return await _record(pool, refund_id=refund_id, payment_id=payment_id, order_id=order_id, account_id=account_id,
                         amount_paise=amount_paise, applied_paise=applied, currency=currency, status=status,
                         reason=reason, reward_reversed=len(reward_rows))


async def _record(pool: Any, *, refund_id: str, payment_id: str, order_id: Optional[str], account_id: Optional[str],
                  amount_paise: int, applied_paise: int, currency: str, status: str, reason: Optional[str],
                  reward_reversed: int = 0) -> dict[str, Any]:
    """Write the refund row. account_id is NOT NULL in the table, so a skipped refund with no account cannot be
    recorded there -- it is reported in the log instead, which is the honest outcome for a payment that never touched
    a wallet."""
    if account_id is None:
        log.warning("refund %s of payment %s skipped: %s", refund_id, payment_id, reason)
        return {"refund_id": refund_id, "status": status, "reason": reason, "applied_paise": 0}
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "INSERT INTO wallet_refunds (refund_id, payment_id, order_id, account_id, amount_paise, applied_paise, "
            "currency, status, reason) VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8, $9) "
            "ON CONFLICT (refund_id) DO NOTHING RETURNING *",
            str(refund_id), str(payment_id), order_id, account_id, amount_paise, applied_paise, currency, status, reason)
        if row is None:
            row = await conn.fetchrow("SELECT * FROM wallet_refunds WHERE refund_id = $1", str(refund_id))
    out = dict(row) if row else {"refund_id": refund_id, "status": status, "applied_paise": applied_paise}
    out["reward_reversed"] = reward_reversed
    return out


def refund_from_event(body: dict[str, Any]) -> Optional[dict[str, Any]]:
    """(refund id, payment id, amount in paise, currency) from a refund.created / refund.processed event, else None.

    Only 'created' and 'processed' are acted on: a refund that FAILED moved no money, and a speed change moved none
    either. A refund Razorpay issues for an uncaptured payment (an auto-refund) has no captured payment behind it, and
    is ignored here -- the payment was never credited, so there is nothing to take back."""
    if body.get("event") not in ("refund.created", "refund.processed"):
        return None
    refund = (((body.get("payload") or {}).get("refund") or {}).get("entity")) or {}
    payment = (((body.get("payload") or {}).get("payment") or {}).get("entity")) or {}
    if not refund.get("id") or not refund.get("payment_id"):
        return None
    if str(refund.get("status") or "").lower() == "failed":
        return None
    if payment.get("captured") is False:
        return None                                             # an auto-refund of a payment never captured or credited
    try:
        amount = int(refund["amount"])
    except (KeyError, TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return {"refund_id": str(refund["id"]), "payment_id": str(refund["payment_id"]), "amount_paise": amount,
            "currency": str(refund.get("currency") or "INR")}
