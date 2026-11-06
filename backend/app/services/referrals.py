"""Referral codes for the one-person Pilot (docs/billing.md, "Referral codes"; the legal briefing is
docs/referral_legality_2026-11.md).

Two kinds, in billing_referral_codes:

  - issued: made by the founders in the admin dashboard (backend/app/api/admin_billing.py) and handed to partners and
    communities. Reusable, no reward, no expiry in the code itself (delete or deactivate ends it).
  - personal: made automatically for a buying account when the payment webhook activates a plan, so that a later reward
    decision has the attribution recorded. Rewards are NOT built (open questions in the briefing); all the buyer's own
    site can do is show them their code and say what it unlocks for the person who uses it.

The gate is server-side, like every amount: an order for the one-seat Pilot carries the code, the route asks this
service whether it is active, and the order row records it (billing_orders.referral_code). Nothing else moves.
"""
from __future__ import annotations

import logging
import secrets
import re
from typing import Any, Collection, Optional

from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

CODE = re.compile(r"^[A-Z0-9]{4,32}$")
# Unambiguous letters and digits: no 0/O, no 1/I. A personal code is "KEL" plus 7 of these.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ReferralError(Exception):
    status = 400


class NotFound(ReferralError):
    status = 404


def clean(value: Any) -> str:
    """The code as the table stores it: trimmed, upper case, 4 to 32 letters or numbers."""
    s = str(value or "").strip().upper()
    if not CODE.fullmatch(s):
        raise ReferralError("a code is 4 to 32 letters or numbers")
    return s


async def active_codes(pool: Any) -> frozenset[str]:
    """The codes a pilot order may be made with right now. Closed on failure: if the table is missing (a server
    deployed before the migration) nothing unlocks, and the route says so rather than crashing."""
    try:
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            rows = await conn.fetch("SELECT code FROM billing_referral_codes WHERE active AND grant_usd IS NULL AND grant_org_id IS NULL")      # a free-credit code (db/183) is not a key to the paid plan
        return frozenset(r["code"] for r in rows)
    except Exception:  # noqa: BLE001
        log.exception("billing: reading referral codes failed; the pilot stays locked")
        return frozenset()


def unlocked(given: Any, codes: Collection[str]) -> bool:
    """Whether the code as given (any case, spaces about the edges) is one of the active codes."""
    if not isinstance(given, str) or not given.strip():
        return False
    return given.strip().upper() in codes


def failure_sentence(given: Any, codes: Collection[str]) -> Optional[str]:
    """None when a one-seat Pilot may be bought with this code; otherwise the sentence the buyer sees. Shared by the
    order route, the codes/check route and the admin dashboard so the words agree everywhere."""
    if not codes:
        return "codes are not open yet; contact us"
    if not isinstance(given, str) or not given.strip():
        return "the one-person plan is unlocked with a referral code: enter the code you were given"
    if given.strip().upper() not in codes:
        return "we do not recognise that code; check it and try again"
    return None


async def ensure_personal(pool: Any, owner_user_id: str) -> Optional[str]:
    """The buyer's personal code, made once when a plan of theirs is activated. Never breaks the webhook path: on any
    failure it says so in the log and returns None (the plan, not the code, is what the payment bought)."""
    try:
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            have = await conn.fetchval("SELECT code FROM billing_referral_codes WHERE owner_user_id = $1::uuid LIMIT 1", str(owner_user_id))
            if have:
                return str(have)
            for _ in range(5):
                code = "KEL" + "".join(secrets.choice(ALPHABET) for _ in range(7))
                made = await conn.fetchval(
                    "INSERT INTO billing_referral_codes (code, kind, owner_user_id, active, created_by) "
                    "VALUES ($1, 'personal', $2::uuid, TRUE, 'plan_activation') ON CONFLICT (code) DO NOTHING RETURNING code",
                    code, str(owner_user_id))
                if made:
                    return str(made)
        log.error("billing: after 5 tries no unique personal code for %s", owner_user_id)
        return None
    except Exception:  # noqa: BLE001
        log.exception("billing: making the personal referral code for %s failed", owner_user_id)
        return None


async def mine(pool: Any, owner_user_id: str) -> Optional[dict[str, str]]:
    """The signed-in account's personal code, for their own billing page. None when they have none."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "SELECT code, kind FROM billing_referral_codes WHERE owner_user_id = $1::uuid AND active", str(owner_user_id))
    return {"code": str(row["code"]), "kind": str(row["kind"])} if row else None


async def listing(pool: Any, limit: int = 500) -> list[dict[str, Any]]:
    """Every code with how many orders it has been made with, newest first. The dashboard deletes a code only when no
    order carries it, so the attribution record never loses an order."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        rows = await conn.fetch(
            "SELECT c.code, c.kind, c.owner_user_id::text AS owner_user_id, c.note, c.active, c.created_by, c.created_at, "
            "(SELECT count(*) FROM billing_orders o WHERE o.referral_code = c.code) AS uses "
            "FROM billing_referral_codes c ORDER BY c.created_at DESC LIMIT $1", limit)
    return [{**dict(r), "uses": int(r["uses"]), "created_at": r["created_at"].isoformat()} for r in rows]


async def create(pool: Any, code: Any, note: Any, created_by: str) -> dict[str, Any]:
    """A hand-issued code (kind 'issued'). A code that already exists is refused, not reused."""
    c = clean(code)
    n = _note(note)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "INSERT INTO billing_referral_codes (code, kind, note, active, created_by) VALUES ($1, 'issued', $2, TRUE, $3) "
            "ON CONFLICT (code) DO NOTHING RETURNING code, kind, note, active, created_by, created_at", c, n, created_by)
    if row is None:
        raise ReferralError("that code already exists")
    return _out(row)


async def set_code(pool: Any, code: Any, *, active: Optional[bool] = None, note: Optional[str] = None) -> dict[str, Any]:
    """Turn a code on or off / change its note. A code that does not exist is a 404 to the dashboard."""
    c = clean(code)
    n = _note(note) if note is not None else None
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(
            "UPDATE billing_referral_codes SET active = COALESCE($2, active), note = COALESCE($3, note) "
            "WHERE code = $1 RETURNING code, kind, note, active, created_by, created_at", c, active, n)
    if row is None:
        raise NotFound("no such code")
    return _out(row)


async def remove(pool: Any, code: Any) -> dict[str, Any]:
    """Delete a code no order was ever made with; a used one must be deactivated instead, so attribution survives."""
    c = clean(code)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        used = await conn.fetchval("SELECT count(*) FROM billing_orders WHERE referral_code = $1", c)
        if int(used) > 0:
            raise ReferralError("deactivate it instead of deleting: orders were made with this code")
        row = await conn.fetchrow("DELETE FROM billing_referral_codes WHERE code = $1 RETURNING code", c)
    if row is None:
        raise NotFound("no such code")
    return {"code": str(row["code"]), "deleted": True}


def _note(note: Any) -> Optional[str]:
    if note in (None, ""):
        return None
    n = re.sub(r"\s+", " ", str(note)).strip()
    if not 0 < len(n) <= 200:
        raise ReferralError("the note is at most 200 characters")
    return n


def _out(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["created_at"] = d["created_at"].isoformat()
    return d
