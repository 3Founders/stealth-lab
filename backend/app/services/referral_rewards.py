"""The reward for sharing a referral code (db/212): the referrer is paid usage credit, in RUPEES, once the person they
referred has PAID.

What the briefing allows (docs/referral_legality_2026-11.md) and this module keeps to:

  * ONE level. The referrer of the referrer is never paid; there is no chain here, and the query below never walks one.
  * Paid on a completed sale. `record_paid_order` is called from the payment webhook and from the reconciler, for an
    order the gateway paid and that we ourselves recorded. Applying a code on the pricing page pays nothing.
  * Credit only. The reward is usage credit in the referrer's own prepaid wallet, via a row in org_wallet_credits with
    source 'referral_reward'. It is not cash, not transferable, and not withdrawable.

RUPEES, not dollars. The reward is a rupee amount (`KEL_REFERRAL_REWARD_INR`, 100 by default) and it is held in the
wallet the way every rupee the wallet holds is: org_wallet_credits.amount_paise carries the rupees, and the
credit-dollars they are worth are worked out at the SAME rate a purchase uses (wallet.usd_for_paise, i.e. the
KELL_INR_PER_USD rate and the KELL_MARGIN), so Rs 100 of reward buys exactly what Rs 100 of top-up buys. The dollar
figure is written alongside for the balance and the spend checks; the rupee figure is what was promised and what the
person sees.

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

from app.services import wallet
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

# One account may earn on this many paid referrals. The founder's number: 30 per account.
MAX_REFERRALS_PER_REFERRER = 30

# The reward, in RUPEES, one per paid referral. 0 switches the programme off. ON by default: the founder asked for the
# programme to start, so an unset variable pays the default rather than nothing.
DEFAULT_REWARD_INR = "100"

# A runaway value is not a reward. Rs 100,000 is far above anything the scheme would promise and well under the
# lifetime ceiling the tax note asks for; anything above it is treated as a mistake in configuration, not an amount.
MAX_REWARD_INR = Decimal("100000")


def reward_inr(env: Optional[Mapping[str, str]] = None) -> Decimal:
    """The rupees one paid referral is worth. Whole rupees only (the wallet is kept in paise). 0 or a negative value
    switches the programme off; an unreadable or absurd value falls back to the default rather than paying a number
    nobody chose."""
    raw = (os.environ if env is None else env).get("KEL_REFERRAL_REWARD_INR")
    if raw is None or str(raw).strip() == "":
        return Decimal(DEFAULT_REWARD_INR)
    try:
        v = Decimal(str(raw).strip())
    except Exception:  # noqa: BLE001
        log.warning("KEL_REFERRAL_REWARD_INR=%r is not a number; using the default", raw)
        return Decimal(DEFAULT_REWARD_INR)
    if v <= 0:
        return Decimal("0")
    if v > MAX_REWARD_INR:
        log.warning("KEL_REFERRAL_REWARD_INR=%s is above the ceiling; using the default", raw)
        return Decimal(DEFAULT_REWARD_INR)
    return v.quantize(Decimal("1"))


def reward_paise(env: Optional[Mapping[str, str]] = None) -> int:
    """The reward in paise, which is how the wallet records rupees."""
    return int(reward_inr(env) * 100)


def reward_usd(env: Optional[Mapping[str, str]] = None) -> Decimal:
    """The credit-dollars those rupees are worth, at the same rate a purchase uses. This is the figure the balance and
    the spend checks read; it is always derived from the rupee amount, never configured separately."""
    return wallet.usd_for_paise(reward_paise(env), env)


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return reward_paise(env) > 0


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
    paise = reward_paise(env)
    if paise <= 0:
        return None
    usd = reward_usd(env)
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
                if released >= MAX_REFERRALS_PER_REFERRER else ("released", None)

            row = await conn.fetchrow(
                "INSERT INTO referral_rewards (code, referrer_user_id, referred_user_id, order_id, payment_id, "
                "amount_paise, reward_inr, reward_usd, status, reason, released_at) "
                "VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, "
                "CASE WHEN $9 = 'released' THEN now() ELSE NULL END) "
                "ON CONFLICT DO NOTHING "
                "RETURNING *",
                code, referrer, buyer, order_id, str(payment_id), amount_paise, paise // 100, usd, status, reason)
            if row is None:
                # A concurrent delivery of the same payment won the insert; return what it made.
                row = await conn.fetchrow("SELECT * FROM referral_rewards WHERE payment_id = $1", str(payment_id))
                return dict(row) if row else None

            if status == "released":
                # AUTOMATIC RELEASE: the reward is credited in the SAME transaction that records the referral, so a
                # referral can never be recorded as paid without its credit, and a rollback returns both. The wallet
                # row's payment_ref is unique, so a retry credits once. A crash after commit is covered by
                # `release_pending`, which finds a 'released' row with no wallet row and finishes the job.
                credited = await conn.fetchval(
                    "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, "
                    "created_by) VALUES ($1::uuid, $2, 'referral_reward', $3, $4, 'referral') "
                    "ON CONFLICT (payment_ref) DO NOTHING RETURNING 1",
                    referrer, f"referral:{row['id']}", paise, usd)
                if not credited:
                    log.info("referral %s was already credited to %s", row["id"], referrer)
            # Return the row as stored, ONCE the credit is written: a caller that changes the returned dict (a test, a
            # debugger) must not reach the stored row.
            row = dict(row)
            row["status"] = status
            row["released_at"] = row["created_at"] if status == "released" and row.get("released_at") is None \
                else row.get("released_at")
            return row
    return None

async def release(pool: Any, *, referral_id: str) -> Optional[dict[str, Any]]:
    """Credit a still-'pending' referral's reward to the referrer's own wallet, and mark it released.

    New referrals are released inline by `record_paid_order`, so in normal running there is nothing here to find.
    This exists for two honest cases: a row left 'pending' by an older deployment, and a repair run
    (`release_pending`). Idempotent on the wallet's unique payment_ref ('referral:<id>')."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT * FROM referral_rewards WHERE id = $1::uuid", str(referral_id))
        if row is None or str(row["status"]) != "pending":
            return dict(row) if row else None
        referrer = str(row["referrer_user_id"])
        reward_inr, reward_usd_value = int(row["reward_inr"]), row["reward_usd"]

    async with tenant_transaction(pool, TenantScope.for_tenant(referrer)) as conn:
        credited = await conn.fetchval(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "VALUES ($1::uuid, $2, 'referral_reward', $3, $4, 'referral') ON CONFLICT (payment_ref) DO NOTHING RETURNING 1",
            referrer, f"referral:{referral_id}", reward_inr * 100, reward_usd_value)
    if not credited:
        log.info("referral %s was already credited to %s; nothing to release", referral_id, referrer)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        done = await conn.fetchrow(
            "UPDATE referral_rewards SET status = 'released', released_at = now() "
            "WHERE id = $1::uuid AND status = 'pending' RETURNING *", str(referral_id))
    return dict(done) if done else None


async def release_pending(pool: Any, *, limit: int = 100) -> list[dict[str, Any]]:
    """The repair run: finish any referral left unfinished.

    Two shapes of unfinished, both of which a crash can produce and neither of which should ever pay twice:
      * 'pending'                    -- the row was written but the credit was not (an older deployment, or a failure
                                        between the two writes before they were made one transaction).
      * 'released' with no wallet row -- the status says paid but the credit row is missing.
    Returns the rows it acted on. Safe to run repeatedly and on a schedule."""
    fixed: list[dict[str, Any]] = []
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        pending = await conn.fetch(
            "SELECT id::text AS id FROM referral_rewards WHERE status = 'pending' "
            "ORDER BY created_at ASC LIMIT $1", limit)
        missing = await conn.fetch(
            "SELECT r.id::text AS id, r.referrer_user_id::text AS referrer FROM referral_rewards r "
            "WHERE r.status = 'released' AND NOT EXISTS ("
            "  SELECT 1 FROM org_wallet_credits w WHERE w.payment_ref = 'referral:' || r.id::text) "
            "ORDER BY r.created_at ASC LIMIT $1", limit)
    for r in pending:
        done = await release(pool, referral_id=r["id"])
        # `release` returns the row even when there was nothing to do (already released); only claim the ones it moved.
        if done and str(done.get("status")) == "released":
            fixed.append(done)
    for r in missing:
        # Re-credit through the same idempotent insert release() uses; the status is already 'released'.
        async with tenant_transaction(pool, TenantScope.for_tenant(r["referrer"])) as conn:
            row = await conn.fetchrow("SELECT reward_inr, reward_usd FROM referral_rewards WHERE id = $1::uuid", r["id"])
            if row is not None:
                await conn.execute(
                    "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
                    "VALUES ($1::uuid, $2, 'referral_reward', $3, $4, 'referral_repair') "
                    "ON CONFLICT (payment_ref) DO NOTHING",
                    r["referrer"], f"referral:{r['id']}", int(row["reward_inr"]) * 100, row["reward_usd"])
                fixed.append({"id": r["id"], "repaired": True})
    return fixed


async def reverse(pool: Any, *, referral_id: str, reason: str) -> Optional[dict[str, Any]]:
    """A refund or chargeback took the payment back: close the credit with a negative wallet row (money out is a row
    of its own here, as in db/156's comment: the wallet is append-only, so a refund is never an edit) and mark the
    referral reversed. Idempotent on the negative row's payment_ref."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT * FROM referral_rewards WHERE id = $1::uuid", str(referral_id))
        if row is None or str(row["status"]) not in ("released", "pending"):
            return dict(row) if row else None
        referrer = str(row["referrer_user_id"])
        reward_paise_value = int(row["reward_inr"]) * 100
        reward_usd_value = row["reward_usd"]
    async with tenant_transaction(pool, TenantScope.for_tenant(referrer)) as conn:
        # Only take it back if it was actually credited, and take back exactly what was credited. A row that was never
        # credited has nothing to reverse (a reversal of money never received would go the wrong way).
        await conn.execute(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "SELECT $1::uuid, $2, 'referral_reward', $3, $4, 'referral_reversal' "
            "WHERE EXISTS (SELECT 1 FROM org_wallet_credits WHERE payment_ref = $5) "
            "ON CONFLICT (payment_ref) DO NOTHING",
            referrer, f"referral-reversal:{referral_id}", -reward_paise_value, -Decimal(str(reward_usd_value)),
            f"referral:{referral_id}")
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
            "SELECT id::text AS id, status, reward_inr, created_at, released_at, reason "
            "FROM referral_rewards WHERE referrer_user_id = $1::uuid ORDER BY created_at DESC LIMIT 100",
            str(referrer_user_id))
    out = []
    earned = Decimal("0")
    pending = Decimal("0")
    used = 0
    for r in rows:
        if str(r["status"]) == "released":
            used += 1
            earned += Decimal(str(r["reward_inr"]))
        elif str(r["status"]) == "pending":
            pending += Decimal(str(r["reward_inr"]))
        out.append({"id": r["id"], "status": r["status"], "reward_inr": int(r["reward_inr"]),
                    "at": r["created_at"].isoformat(),
                    "released_at": r["released_at"].isoformat() if r["released_at"] else None,
                    "reason": r["reason"]})
    return {
        "limit": MAX_REFERRALS_PER_REFERRER,
        "used": used,
        "remaining": max(0, MAX_REFERRALS_PER_REFERRER - used),
        "earned_inr": str(earned),
        "pending_inr": str(pending),
        "reward_inr": str(reward_inr()),
        "enabled": enabled(),
        "referrals": out,
    }
