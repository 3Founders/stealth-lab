"""Priced tools, metered against the person's own plan wallet (db/195_tool_listings.sql, docs/goal_router.md P3-06).

    line = await quote(pool, call)                  # "price: $0.0040 per call to <card>, paid from your kell credit"
    held = await charge(pool, call, call_key=key)   # before any network call: the price is held (Charge(ok, ...))
    ... the call runs ...
    await settle(pool, key)  or, if it failed,  await refund(pool, key)   # exactly one of them, once

Credits first: there is no cash payout here. A card with no active listing is free and never touches the wallet.

Who pays, in order:
  1. a provider-funded trial the person is inside (app/tool_routing/trials.py consume): the provider;
  2. the listing's free trial calls for this person: nobody (the provider gives them away);
  3. the person: a reserved row in provider_call_ledger (the ledger app/services/wallet.py computes the balance from;
     a reserved row already counts against it), keyed by a uuid derived from the call key, so a retry with the same key
     never holds twice. settle() makes it spent, refund() releases it: the ledger allows each row exactly one of the two.
A person whose remaining balance is below the price is refused ("REFUSED: balance ...") before any network call. An
account the wallet cannot read (wallets off, or an id that is not an account) has no balance: refused, never free.

TOOL_PRICING=on turns metering on in the gateway (server.py); off, priced tools are not charged and quote() still
shows the price.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

LEDGER_NS = uuid.UUID("2b7e1516-28ae-d2a6-abf7-158809cf4f3c")   # call key -> provider_call_ledger id


def enabled() -> bool:
    return os.environ.get("TOOL_PRICING", "off").strip().lower() in ("1", "on", "true", "yes")


@dataclass
class Charge:
    ok: bool
    reason: str
    price_usd: float = 0.0
    paid_by: str = ""
    call_key: str = ""


async def listing(pool: Any, card_or_server: str) -> Optional[dict]:
    try:
        row = await pool.fetchrow(
            """SELECT l.card_id, l.provider, l.price_usd::float8 AS price_usd, l.take_rate::float8 AS take_rate,
                      l.free_trial_calls
               FROM tool_listings l JOIN tool_cards c ON c.id = l.card_id
               WHERE l.status = 'active' AND (l.card_id = $1 OR c.server = $1)
               LIMIT 1""", card_or_server)
    except Exception as exc:  # noqa: BLE001 -- no 195 yet: every tool is free
        if type(exc).__name__ == "UndefinedTableError":
            return None
        raise
    return dict(row) if row else None


async def remaining_usd(pool: Any, owner_id: str) -> Optional[Decimal]:
    """The person's remaining wallet balance, or None when it cannot be read (not an account, wallets off)."""
    from app.services import wallet

    try:
        uuid.UUID(str(owner_id))
    except ValueError:
        return None
    try:
        return Decimal(str((await wallet.balance(pool, str(owner_id)))["remaining_usd"]))
    except Exception:  # noqa: BLE001 -- unreadable is not free: the caller refuses
        return None


async def quote(pool: Any, call: Any) -> Optional[str]:
    """The price line shown before the call, or None for a free tool."""
    item = await listing(pool, str(getattr(call, "server", "") or ""))
    if item is None or item["price_usd"] <= 0:
        return None
    return (f"price: ${item['price_usd']:.4f} per call to {item['card_id']} (by {item['provider']}), paid from your kell "
            f"credit" + (f"; your first {item['free_trial_calls']} calls are free" if item["free_trial_calls"] else ""))


async def charge(pool: Any, call: Any, *, call_key: str) -> Charge:
    """Debit one priced call before it runs. Free tools pass with price 0. Idempotent per call_key."""
    server = str(getattr(call, "server", "") or "")
    owner = str(getattr(call, "owner_id", "") or "")
    item = await listing(pool, server)
    if item is None or item["price_usd"] <= 0:
        return Charge(True, "free", 0.0, "", call_key)
    price = Decimal(str(item["price_usd"]))
    done = await pool.fetchrow("SELECT paid_by, state FROM tool_charges WHERE call_key = $1", call_key)
    if done is not None:                                      # the same call again: already charged once
        return Charge(done["state"] != "refunded", "already_charged", float(price), done["paid_by"], call_key)
    provider_usd = (price * (1 - Decimal(str(item["take_rate"])))).quantize(Decimal("0.000001"))
    kell_usd = price - provider_usd

    # 1. a provider-funded trial
    goal = str(getattr(call, "goal_id", "") or "")
    if goal and owner:
        from app.tool_routing import trials

        offered = await trials.offer(pool, goal, owner_id=owner, request_id=call_key)
        if offered and offered["card_id"] == item["card_id"]:
            ok, _why = await trials.consume(pool, offered["id"], owner_id=owner, cost_usd=float(price))
            if ok:
                await _record(pool, call_key, owner, item, call, price, provider_usd, kell_usd, "provider_trial",
                              trial_id=offered["id"])
                return Charge(True, "trial", float(price), "provider_trial", call_key)

    # 2. the listing's free calls for this person
    if item["free_trial_calls"]:
        used = await pool.fetchval("SELECT count(*) FROM tool_charges WHERE owner_id = $1 AND card_id = $2 "
                                   "AND paid_by = 'free_trial_calls'", owner, item["card_id"])
        if used < item["free_trial_calls"]:
            await _record(pool, call_key, owner, item, call, price, Decimal(0), Decimal(0), "free_trial_calls")
            return Charge(True, "free_trial_call", 0.0, "free_trial_calls", call_key)

    # 3. the person's wallet
    left = await remaining_usd(pool, owner)
    if left is None:
        return Charge(False, "balance: no kell credit account for this caller", float(price), "", call_key)
    if left < price:
        return Charge(False, f"balance: ${left:.4f} left, this call costs ${price:.4f}", float(price), "", call_key)
    ledger_id = uuid.uuid5(LEDGER_NS, call_key)
    from app.services.access import TenantScope, tenant_transaction

    async with tenant_transaction(pool, TenantScope.for_tenant(owner)) as conn:
        await conn.execute(
            """
            INSERT INTO provider_call_ledger (id, organization_id, actor_subject, tool, unit, connection_id, provider,
                                              model, scaffold, data_class, status, reserved_usd)
            VALUES ($1, $2::uuid, $2, 'use_tool', $3, 'tool-listing', $4, $3, 'tool', $5, 'reserved', $6)
            ON CONFLICT (id) DO NOTHING
            """,
            ledger_id, owner, item["card_id"], f"tool:{item['provider']}",
            str(getattr(call, "data_class", "PUBLIC_SOURCE") or "PUBLIC_SOURCE"), price)
    await _record(pool, call_key, owner, item, call, price, provider_usd, kell_usd, "owner", ledger_id=ledger_id)
    return Charge(True, "held", float(price), "owner", call_key)


async def _record(pool: Any, call_key: str, owner: str, item: dict, call: Any, price: Decimal, provider_usd: Decimal,
                  kell_usd: Decimal, paid_by: str, *, ledger_id: Optional[uuid.UUID] = None,
                  trial_id: Optional[int] = None) -> None:
    await pool.execute(
        """INSERT INTO tool_charges (call_key, owner_id, card_id, tool, price_usd, provider_usd, kell_usd, paid_by,
                                     ledger_id, trial_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (call_key) DO NOTHING""",
        call_key, owner, item["card_id"], str(getattr(call, "tool", "") or ""), price, provider_usd, kell_usd, paid_by,
        ledger_id, trial_id)


async def _finish(pool: Any, call_key: str, state: str) -> bool:
    """Move a held charge to settled or refunded, with its ledger row (or trial spend), in one transaction, once."""
    from app.services.access import TenantScope, tenant_transaction

    row = await pool.fetchrow("SELECT owner_id, ledger_id, trial_id, price_usd, paid_by FROM tool_charges "
                              "WHERE call_key = $1 AND state = 'held'", call_key)
    if row is None:
        return False
    async with tenant_transaction(pool, TenantScope.for_tenant(str(row["owner_id"])) if row["ledger_id"]
                                  else TenantScope.unrestricted()) as conn:
        moved = await conn.fetchval("UPDATE tool_charges SET state = $2 WHERE call_key = $1 AND state = 'held' "
                                    "RETURNING id", call_key, state)
        if moved is None:                                      # someone else finished it first
            return False
        if row["ledger_id"] is not None:
            if state == "settled":
                await conn.execute("UPDATE provider_call_ledger SET status = 'settled', cost_usd = reserved_usd, "
                                   "cost_source = 'declared', settled_at = now() WHERE id = $1 AND status = 'reserved'",
                                   row["ledger_id"])
            else:
                await conn.execute("UPDATE provider_call_ledger SET status = 'failed', settled_at = now(), "
                                   "error_type = 'tool_call_failed' WHERE id = $1 AND status = 'reserved'",
                                   row["ledger_id"])
        elif state == "refunded" and row["paid_by"] == "provider_trial" and row["trial_id"] is not None:
            await conn.execute("UPDATE tool_trials SET spent_usd = spent_usd - $2, used_calls = used_calls - 1 "
                               "WHERE id = $1", row["trial_id"], row["price_usd"])
    return True


async def settle(pool: Any, call_key: str) -> bool:
    """The call ran: the held price is spent (or the trial's use stands). -> True the first time."""
    return await _finish(pool, call_key, "settled")


async def refund(pool: Any, call_key: str) -> bool:
    """The call failed: the hold is released and nobody pays (a trial's use is given back). -> True the first time."""
    return await _finish(pool, call_key, "refunded")
