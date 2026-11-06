"""Free usage credit by code, with no payment (db/183): a company trial such as COMPANYTRIAL gives each person $0.25, up to ten people.

Why a code and not a plan. The plan tables are for things that were paid for (amount and price must be above zero), and a free grant
is a different thing; keeping it apart means a free code can never be mistaken for a purchase, and it does not unlock the paid
one-person plan (referrals.active_codes leaves it out).

What it does. ``redeem`` records the person against the code (one row per person per code, the limit counted under a per-code lock so
two people cannot take the last place) and then adds the credit to the person's own wallet as a ``signup_bonus`` row of 0 paise paid,
keyed by the code and the person so a retry credits once. If the process dies between the two writes, the next ``redeem`` of the same code
by the same person sees the recorded redemption, completes the missing credit and says so; nobody is left recorded-but-unpaid.

A code can instead put the person in a TEAM (``grant_org_id``): they become a ``member`` of that organization and its wallet and policy pay
for their use (db/184). A code can also require a verified phone (``require_phone``): the number must be on the person's sign-in
(Supabase Auth phone confirmation puts it in the token), and one number redeems a code once; only a keyed hash of it is stored.

Honest limits. The limit is per code. A phone check stops one person using many email accounts but not someone with many SIM cards, and it
is only as good as the SMS provider behind Supabase Auth. A team member's use is bounded by the organization's per-person daily cap and
monthly budget, not by a lifetime amount per person.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from decimal import Decimal
from typing import Any

from app.services import referrals, wallet
from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)


class FreeCodeError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def phone_digits(value: Any) -> str:
    """A phone number as digits only (the token carries it without the plus), or "" when it is not plausibly one."""
    d = re.sub(r"\D", "", str(value or ""))
    return d if 8 <= len(d) <= 15 else ""


def phone_hash(code: str, digits: str, key: str | None = None) -> str:
    """A keyed hash of (code, number): the number cannot be read back from the table, and the same number under another code differs."""
    k = key if key is not None else os.environ.get("ORG_KEY_ENCRYPTION_KEY", "")
    if not k:
        raise FreeCodeError("phone checks are not set up on this server yet", 503)
    return hmac.new(k.encode("utf-8"), f"{code}:{digits}".encode("utf-8"), hashlib.sha256).hexdigest()


async def redeem(pool: Any, *, user_id: str, code: Any, phone: Any = None) -> dict[str, Any]:
    """Apply ``code`` for ``user_id`` once. Returns {code, kind: credit|team, credited_usd, already, places_left, team}."""
    try:
        code = referrals.clean(code)
    except referrals.ReferralError as exc:
        raise FreeCodeError(str(exc)) from exc
    digits = phone_digits(phone)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"free-credit:{code}")
        row = await conn.fetchrow(
            "SELECT grant_usd, max_uses, grant_org_id::text AS org, require_phone FROM billing_referral_codes "
            "WHERE code = $1 AND active AND grant_usd IS NOT NULL", code)
        if row is None:
            raise FreeCodeError("we do not recognise that code; check it and try again", 404)
        amount = Decimal(str(row["grant_usd"]))
        used = int(await conn.fetchval("SELECT count(*) FROM free_credit_redemptions WHERE code = $1", code))
        mine = await conn.fetchval("SELECT 1 FROM free_credit_redemptions WHERE code = $1 AND user_id = $2::uuid", code, str(user_id))
        already = bool(mine)
        if not already:
            ph = None
            if row["require_phone"]:
                if not digits:
                    raise FreeCodeError("verify your phone number first: this offer needs a confirmed phone number on your account", 403)
                ph = phone_hash(code, digits)
                if await conn.fetchval("SELECT 1 FROM free_credit_redemptions WHERE code = $1 AND phone_hash = $2", code, ph):
                    raise FreeCodeError("this phone number has already been used for this offer", 409)
            if row["max_uses"] is not None and used >= int(row["max_uses"]):
                raise FreeCodeError("this offer is full: all of its places have been taken", 409)
            await conn.execute("INSERT INTO free_credit_redemptions (code, user_id, amount_usd, phone_hash) VALUES ($1, $2::uuid, $3, $4)",
                               code, str(user_id), amount, ph)
            used += 1
    left = None if row["max_uses"] is None else max(0, int(row["max_uses"]) - used)
    if row["org"]:
        from app.services import org_governance as gov
        from app.services.org_onboarding import MEMBER_ROLE_ID

        async with tenant_transaction(pool, gov._scope(row["org"])) as conn:
            await conn.execute(
                "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) ON CONFLICT DO NOTHING",
                row["org"], str(user_id), MEMBER_ROLE_ID)
        return {"code": code, "kind": "team", "credited_usd": "0", "allowance_usd": str(amount), "already": already, "places_left": left,
                "team": row["org"]}
    async with tenant_transaction(pool, TenantScope.for_tenant(str(user_id))) as conn:
        credited = await conn.fetchval(
            "INSERT INTO org_wallet_credits (organization_id, payment_ref, source, amount_paise, amount_usd, created_by) "
            "VALUES ($1::uuid, $2, 'signup_bonus', 0, $3, $4) ON CONFLICT (payment_ref) DO NOTHING RETURNING 1",
            str(user_id), f"free:{code}:{user_id}", amount, f"code:{code}")
    if already and not credited:
        raise FreeCodeError("you have already used this code", 409)
    return {"code": code, "kind": "credit", "credited_usd": str(amount), "already": already, "places_left": left}


async def balance_after(pool: Any, user_id: str) -> str:
    return (await wallet.balance(pool, user_id))["remaining_usd"]
