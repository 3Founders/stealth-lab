"""The reward for sharing a referral code (db/212): the referrer is paid usage credit once the person they referred
has PAID.

What the briefing allows (docs/referral_legality_2026-11.md) and this module keeps to:

  * ONE level. The referrer of the referrer is never paid; there is no chain here, and the query below never walks one.
  * Paid on a completed sale. `record_paid_order` is called from the payment webhook, for an order the gateway paid and
    that we ourselves recorded. Applying a code on the pricing page (`referrals.active_codes`) pays nothing.
  * Credit only. The reward is credit-dollars in the referrer's own prepaid wallet, via a row in org_wallet_credits
    with source 'referral_reward'. It is not cash, not transferable, and not withdrawable.

Individual accounts only. The credit always goes to the REFERRER'S OWN wallet -- the account whose id is their user id
(services/accounts.py), never a team or organization wallet. A team order can carry a code (for attribution, as it
already did) but pays nobody: the code must be a personal one, and the amount is added to a person.

The allowance. One account may earn on 30 paid referrals (`MAX_REFERRALS_PER_REFERRER`); a 31st is recorded as 'void'
with a reason rather than paid, so the count and the refusal are both visible in the table instead of in a log line.

Who is never paid: a founder/issued code (kind 'issued') pays nobody -- those codes are handed to partners and
communities and are not somebody's to earn from. A free-credit or team code (grant_usd / grant_org_id set) is not a
referral key at all. A code whose owner is missing, or is the buyer, pays nobody (no self-referral).

Idempotency is the database's job, not this module's: uq_referral_reward_referred, ..._order and ..._payment in db/212
mean a redelivered webhook finds the row it made and returns it. Every write below is ON CONFLICT DO NOTHING + a read,
which is the same shape wallet.credit_payment and free_credits.redeem use.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

# One account may earn on this many paid referrals. The founder's number: 30 per account.
MAX_REFERRALS_PER_REFERRER = 30

# The reward, in credit-dollars, one per paid referral. 0 switches the programme off (no row is written at all), which
# is the safe default for a deployment that has not decided to run a referral programme yet.
DEFAULT_REWARD_USD = "1.00"


def reward_usd(env: Optional[Mapping[str, str]] = None) -> Decimal:
    """The credit-dollars one paid referral is worth. Zero off, unset off, an unreadable value off -- a referral
    programme that pays nothing is better than one that pays a number nobody chose."""
    raw = (os.environ if env is None else env).get("KEL_REFERRAL_REWARD_USD")
    if raw is None:
        return Decimal("0")
    try:
        v = Decimal(str(raw).strip())
    except Exception:  # noqa: BLE001
        return Decimal("0")
    return v if v > 0 else Decimal("0")


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return reward_usd(env) > 0


async def _code_owner(conn: Any, code: str) -> Optional[dict[str, Any]]:
    """The personal code's owner, or None when this code may not pay a referrer. One query, one place: the rules for
    "is this a code somebody can earn from" live here and nowhere else."""
    row = await conn.fetchrow(
        "SELECT owner_user_id::text AS owner, kind, grant_usd, grant_org_id "
        "FROM billing_referral_codes WHERE code = $1 AND active", code)
    if row is None:
        return None
    if str(row["kind"]) != "personal":                       # founder/issued codes pay nobody
        return None
    if row["grant_usd"] is not None or row["grant_org_id"] is not None:   # a free-credit or team code, not a key
        return None
    if not row["owner"]:
        return None
    return {"owner": str(row["owner"])}


async def record_paid_order(pool: Any, *, order: Mapping[str, Any], payment_id: str,
                            env: Optional[Mapping[str, str]] = None) -> Optional[dict[str, Any]]:
    """A plan order that was paid for carried a referral code: record (and, when allowed, pay) the referrer's reward.

    Called from the payment webhook AFTER the plan is activated, so the money is real and the order is one we wrote.
    Returns the referral_rewards row (created, or the one a redelivery already made), or None when this order earns
    nobody anything: no code, a code that is not a personal one, the buyer's own code, self-referral, the programme
    switched off, or a team order (which never pays into an organization wallet)."""
    code = (order.get("referral_code") or "").strip().upper()
    if not code:
        return None
    amount = reward_usd(env)
    if amount <= 0:
        return None
    buyer = str(order.get("buyer_user_id") or "").strip()
    if not buyer:
        return None
    order_id = str(order.get("order_id") or "").strip()
    if not order_id:
        return None
    amount_paise = int(order.get("amount_paise") or 0)

    # A redelivery of a payment already recorded: hand back the row, do nothing else.
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        made = await conn.fetchrow("SELECT * FROM referral_rewards WHERE payment_id = $1", str(payment_id))
        if made is not None:
            return dict(made)
        owner = await _code_owner(conn, code)

    if owner is None or owner["owner"] == buyer:
        return None

    referrer = owner["owner"]
    # The referee's own wallet must exist before we promise them anything (a person's account id is their user id).
    from app.services import accounts

    await accounts.ensure_account(pool, user_id=buyer, env={"KELL_WALLETS": "on"})

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize on the referrer: two simultaneous payments from two of their referrals must not both see
            # "29 so far" and both pass the 30th slot.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"referral:referrer:{referrer}")

            already = await conn.fetchrow("SELECT * FROM referral_rewards WHERE payment_id = $1", str(payment_id))
            if already is not None:
                return dict(already)
            if await conn.fetchval("SELECT 1 FROM referral_rewards WHERE referred_user_id = $1::uuid", buyer):
                return None                                       # this account was already referred once

            released = int(await conn.fetchval(
                "SELECT count(*) FROM referral_rewards WHERE referrer_user_id = $1::uuid AND status = 'released'",
                referrer))
            status, reason = ("void", "this account has already earned for 30 paid referrals") \
                if released >= MAX_REFERRALS_PER_REFERRER else ("pending", None)

            row = await conn.fetchrow(
                "INSERT INTO referral_rewards (code, referrer_user_id, referred_user_id, order_id, payment_id, "
                "amount_paise, reward_usd, status, reason) "
                "VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9) "
                "ON CONFLICT DO NOTHING "
                "RETURNING *",
                code, referrer, buyer, order_id, str(payment_id), amount_paise, amount, status, reason)
            if row is None:
                # A concurrent delivery of the same payment won the insert; return what it made.
                row = await conn.fetchrow("SELECT * FROM referral_rewards WHERE payment_id = $1", str(payment_id))
                return dict(row) if row else None
    return dict(row) if row else None


async def release(pool: Any, *, referral_id: str) -> Optional[dict[str, Any]]:
    """Add a pending referral's reward to the referrer's own wallet. Idempotent: the wallet's unique payment_ref
    ('referral:<id>') means a second call credits once, and the status update happens in the same transaction, so a
    reward is never paid without the row saying so.

    The money moves through org_wallet_credits exactly as every other credit does (0 paise paid, credit-dollars), so
    the balance, the spend checks and the history are the same machinery paid credit uses."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT * FROM referral_rewards WHERE id = $1::uuid", str(referral_id))
        if row is None or str(row["status"]) != "pending":
            return dict(row) if row else None
        referrer = str(row["referrer_user_id"])
        reward = row["reward_usd"]

    # The credit lands in the referrer's OWN wallet account, bound as that tenant. `credited` is None when this
    # referral's payment_ref is already in the wallet, which is the crash-recovery case: the process died after crediting
    # but before the status update below, so the row is still 'pending'. Paying again would double-credit, so stop here.
    async with tenant_transaction(pool, TenantScope.for_tenant(referrer)) as conn:
        credited = await conn.fetchval(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "VALUES ($1::uuid, $2, 'referral_reward', 0, $3, 'referral') ON CONFLICT (payment_ref) DO NOTHING RETURNING 1",
            referrer, f"referral:{referral_id}", reward)
    if not credited:
        log.info("referral %s was already credited to %s; nothing to release", referral_id, referrer)
        return None
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        done = await conn.fetchrow(
            "UPDATE referral_rewards SET status = 'released', released_at = now() "
            "WHERE id = $1::uuid AND status = 'pending' RETURNING *", str(referral_id))
    return dict(done) if done else None


async def reverse(pool: Any, *, referral_id: str, reason: str) -> Optional[dict[str, Any]]:
    """A refund or chargeback took the payment back: close the credit with a negative wallet row (money out is a row
    of its own here, as in db/156's comment: the wallet is append-only, so a refund is never an edit) and mark the
    referral reversed. Idempotent on the negative row's payment_ref."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT * FROM referral_rewards WHERE id = $1::uuid", str(referral_id))
        if row is None or str(row["status"]) not in ("released", "pending"):
            return dict(row) if row else None
        referrer = str(row["referrer_user_id"])
        reward = row["reward_usd"]
    async with tenant_transaction(pool, TenantScope.for_tenant(referrer)) as conn:
        await conn.execute(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "VALUES ($1::uuid, $2, 'referral_reward', 0, $3, 'referral_reversal') ON CONFLICT (payment_ref) DO NOTHING",
            referrer, f"referral-reversal:{referral_id}", -Decimal(str(reward)))
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        done = await conn.fetchrow(
            "UPDATE referral_rewards SET status = 'reversed', reason = $2 "
            "WHERE id = $1::uuid AND status IN ('pending', 'released') RETURNING *", str(referral_id), reason)
    return dict(done) if done else None


async def summary(pool: Any, *, referrer_user_id: str) -> dict[str, Any]:
    """What the referrer's own page shows: how many of their 30 are used, what is pending, what was earned, and one
    line per referral.

    There is deliberately NO name, email or user id of the referred person in this answer. The briefing's data
    protection point (DPDP): a referrer is told "someone used your code" and the credit, never who. The rows carry an
    opaque id and a date, nothing more."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, status, reward_usd, created_at, released_at, reason "
            "FROM referral_rewards WHERE referrer_user_id = $1::uuid ORDER BY created_at DESC LIMIT 100",
            str(referrer_user_id))
    out = []
    earned = Decimal("0")
    pending = Decimal("0")
    used = 0
    for r in rows:
        if str(r["status"]) == "released":
            used += 1
            earned += Decimal(str(r["reward_usd"]))
        elif str(r["status"]) == "pending":
            pending += Decimal(str(r["reward_usd"]))
        out.append({"id": r["id"], "status": r["status"], "reward_usd": str(r["reward_usd"]),
                    "at": r["created_at"].isoformat(),
                    "released_at": r["released_at"].isoformat() if r["released_at"] else None,
                    "reason": r["reason"]})
    return {
        "limit": MAX_REFERRALS_PER_REFERRER,
        "used": used,
        "remaining": max(0, MAX_REFERRALS_PER_REFERRER - used),
        "earned_usd": str(earned),
        "pending_usd": str(pending),
        "reward_usd": str(reward_usd()),
        "enabled": enabled(),
        "referrals": out,
    }
