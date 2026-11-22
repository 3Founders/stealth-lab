"""Per-procedure billing: a one-off licence to run one pinned Way version.

The design is docs/procedure_billing.md. The short version:

  * A price lives on the procedure VERSION (`procedures.price_paise`). NULL is unpriced (free);
    0 is a deliberate free listing (also free). Bounds: 0..100000 paise.
  * A licence is one row per (version, buyer). `UNIQUE (procedure_row_id, buyer_user_id)` makes
    paying twice for the same version impossible by construction.
  * The gateway is Razorpay Payment Links: fixed amount, one-time, hosted `short_url`. A Payment
    Link is the right primitive here because the Way page already redirects to a URL and because
    a one-off charge avoids the RBI recurring-mandate framework entirely.
  * Nothing is granted by the browser returning from checkout. The licence becomes `paid` only
    from a `payment_link.paid` webhook (or an explicit reconcile against the gateway), and the
    payment id is the idempotency key.
  * The gate is `check_entitlement`, called from `way_runs.start` (the common path for the
    website run, the MCP `run_way` tool and `submissions.start_run`).

Identity: the buyer is the Supabase subject (`who.actor_id`), because that is the `owner_id`
the submission and run paths already use. It is a UUID for a real signed-in person; the local
single-user operator (`local-user`) cannot buy, which is correct: there is no account to charge.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

log = logging.getLogger(__name__)

RAZORPAY_LINKS = "https://api.razorpay.com/v1/payment_links"
MAX_PRICE_PAISE = 100_000          # Rs 1,000; the brief's band is Rs 100-200
DEFAULT_CALLBACK = "https://www.kell.org.in"


class ProcedureBillingError(Exception):
    """A refusal safe to show the buyer. `status_code` is the HTTP answer."""
    status_code = 400


class ProcedureNotPurchased(ProcedureBillingError):
    """A paid Way was run without a live licence. The named refusal the gate raises."""
    status_code = 402

    def __init__(self, price_paise: int, currency: str = "INR"):
        self.price_paise = int(price_paise)
        self.currency = currency or "INR"
        amount = f"{self.price_paise / 100:.2f}"
        super().__init__(f"this Way costs {self.currency} {amount}; buy a licence before running it")


class PurchaseError(ProcedureBillingError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


# ------------------------------------------------------------------ helpers

def _uuid(value: Any) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _auth() -> tuple[str, str]:
    key = (os.environ.get("RAZORPAY_KEY_ID") or "").strip()
    secret = (os.environ.get("RAZORPAY_KEY_SECRET") or "").strip()
    if not key or not secret:
        raise PurchaseError("buying is not switched on yet", 503)
    return key, secret


def _callback(url: Optional[str]) -> str:
    """Only an http(s) URL we would send a buyer back to; anything else gets the public site."""
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url[:2000]
    return DEFAULT_CALLBACK


async def procedure_row(pool: Any, ref: str) -> Optional[dict[str, Any]]:
    """One active procedure version by row id, stable procedure_id, or a goal submission id."""
    text = str(ref or "")
    sub = await pool.fetchrow("SELECT parent_procedure_id::text AS parent FROM goal_submissions WHERE id::text = $1", text)
    if sub is not None and sub.get("parent"):
        text = str(sub["parent"])
    row = await pool.fetchrow(
        """SELECT id::text AS id, procedure_id::text AS procedure_id, version, name, goal,
                  price_paise, price_currency, availability::text AS availability,
                  visibility::text AS visibility, owner_id
           FROM procedures
           WHERE (id::text = $1 OR procedure_id::text = $1) AND t_invalid IS NULL AND availability = 'active'
           ORDER BY (id::text = $1) DESC, version DESC LIMIT 1""", text)
    return dict(row) if row else None


async def licence_status(pool: Any, *, procedure_row_id: Any, buyer_user_id: Optional[str]) -> str:
    """'none' | 'purchased' | 'refunded' for this buyer and this exact version."""
    buyer = _uuid(buyer_user_id)
    row_id = _uuid(procedure_row_id)
    if buyer is None or row_id is None:
        return "none"
    rows = await pool.fetch(
        "SELECT status, expires_at FROM procedure_licences "
        "WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid ORDER BY created_at DESC",
        row_id, buyer)
    now = datetime.now(timezone.utc)
    for r in rows:
        if r["status"] == "paid" and (r["expires_at"] is None or r["expires_at"] > now):
            return "purchased"
        if r["status"] == "refunded":
            return "refunded"
    return "none"


async def check_entitlement(pool: Any, *, procedure_row_id: Any, buyer_user_id: Optional[str]) -> None:
    """Raise ProcedureNotPurchased when the version is priced and this buyer has no live licence.

    A free version (price NULL or 0) never queries a licence. The refusal is named, not a silent
    fallback, so the website and the MCP tool can say the same sentence.
    """
    row = await pool.fetchrow(
        "SELECT price_paise, price_currency FROM procedures WHERE id::text = $1 AND t_invalid IS NULL",
        str(procedure_row_id))
    if row is None:
        return                                  # the caller's own loader owns "no such Way"
    price = row["price_paise"]
    if price is None or int(price) <= 0:
        return
    status = await licence_status(pool, procedure_row_id=procedure_row_id, buyer_user_id=buyer_user_id)
    if status != "purchased":
        raise ProcedureNotPurchased(int(price), row["price_currency"] or "INR")


# ------------------------------------------------------------------ gateway

async def create_payment_link(*, amount_paise: int, description: str, reference_id: str,
                              callback_url: str, notes: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """POST /v1/payment_links. Fixed amount, one-time; the response carries `short_url`."""
    from app.providers.adapters import http_client

    key, secret = _auth()
    body = {
        "amount": int(amount_paise),
        "currency": "INR",
        "description": str(description)[:2048],
        "reference_id": str(reference_id)[:40],
        "callback_url": _callback(callback_url),
        "callback_method": "get",
        "reminder_enable": False,
        "notes": {str(k): str(v) for k, v in (notes or {}).items()},
    }
    async with http_client(timeout=30.0) as client:
        r = await client.post(RAZORPAY_LINKS, auth=(key, secret), json=body)
    if r.status_code >= 300:
        log.warning("razorpay refused a payment link: %s %s", r.status_code, r.text[:300])
        raise PurchaseError("the payment provider refused to open the checkout", 502)
    data = r.json()
    if not data.get("short_url") or not data.get("id"):
        raise PurchaseError("the payment provider returned no checkout URL", 502)
    return data


async def fetch_payment_link(payment_link_id: str) -> Optional[dict[str, Any]]:
    """GET /v1/payment_links/{id}. None when the link is gone or the gateway cannot be asked."""
    from app.providers.adapters import http_client

    key, secret = _auth()
    async with http_client(timeout=15.0) as client:
        r = await client.get(f"{RAZORPAY_LINKS}/{payment_link_id}", auth=(key, secret))
    if r.status_code == 404:
        return None
    if r.status_code >= 300:
        log.warning("razorpay could not fetch payment link %s: %s", payment_link_id, r.status_code)
        return None
    return r.json()


# ------------------------------------------------------------------ purchase

async def create_purchase(pool: Any, *, procedure_row_id: str, buyer_user_id: str,
                          callback_url: Optional[str] = None) -> dict[str, Any]:
    """Open (or reuse) a hosted checkout for one version. Returns `checkout_url` for the page."""
    row = await procedure_row(pool, procedure_row_id)
    if row is None:
        raise PurchaseError("no such Way", 404)
    price = row["price_paise"]
    if price is None or int(price) <= 0:
        raise PurchaseError("this Way is free; no purchase is needed", 400)
    buyer = _uuid(buyer_user_id)
    if buyer is None:
        raise PurchaseError("sign in with a kell account to buy", 401)
    callback = _callback(callback_url)
    currency = row["price_currency"] or "INR"

    existing = await pool.fetchrow(
        "SELECT id::text AS id, status, payment_link_id, amount_paise, procedure_version "
        "FROM procedure_licences WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid",
        row["id"], buyer)
    if existing is not None:
        if existing["status"] == "paid":
            # Already owned: send the page back to itself, where it will render "Licensed".
            return {"checkout_url": callback, "already": True, "licence_status": "purchased",
                    "price_paise": int(existing["amount_paise"] or price), "currency": currency,
                    "version": int(existing["procedure_version"] or row["version"])}
        if existing["status"] == "pending" and existing.get("payment_link_id"):
            link = await fetch_payment_link(str(existing["payment_link_id"]))
            if link is not None:
                if link.get("status") in ("created", "partially_paid") and link.get("short_url"):
                    return {"checkout_url": link["short_url"], "licence_id": existing["id"],
                            "price_paise": int(price), "currency": currency, "version": int(row["version"])}
                if link.get("status") == "paid":
                    paid = next((p for p in (link.get("payments") or []) if p.get("status") == "captured"), None)
                    if paid is not None:
                        await activate_by_link(pool, payment_link_id=str(link.get("id") or ""),
                                               payment_id=str(paid.get("payment_id") or ""),
                                               amount_paise=int(paid.get("amount") or 0))
                        return {"checkout_url": callback, "already": True, "licence_status": "purchased",
                                "price_paise": int(price), "currency": currency, "version": int(row["version"])}

    licence_id = await pool.fetchval(
        """INSERT INTO procedure_licences
               (procedure_id, procedure_row_id, procedure_version, buyer_user_id, amount_paise, currency)
           VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6)
           ON CONFLICT (procedure_row_id, buyer_user_id) DO UPDATE
             SET status = 'pending', amount_paise = EXCLUDED.amount_paise, currency = EXCLUDED.currency,
                 payment_ref = NULL, order_id = NULL, payment_link_id = NULL,
                 granted_at = NULL, refunded_at = NULL
             WHERE procedure_licences.status <> 'paid'
           RETURNING id::text""",
        row["procedure_id"], row["id"], int(row["version"]), buyer, int(price), currency)
    if licence_id is None:
        # The conflict hit a paid row between the read and the upsert: it is already owned.
        return {"checkout_url": callback, "already": True, "licence_status": "purchased",
                "price_paise": int(price), "currency": currency, "version": int(row["version"])}

    link = await create_payment_link(
        amount_paise=int(price), description=f"{row['name']} (v{row['version']})",
        reference_id=licence_id, callback_url=callback,
        notes={"licence_id": licence_id, "procedure_row_id": row["id"], "buyer_user_id": buyer})
    await pool.execute(
        "UPDATE procedure_licences SET payment_link_id = $2, order_id = $3 WHERE id = $1::uuid",
        licence_id, str(link["id"]), (str(link["order_id"]) if link.get("order_id") else None))
    return {"checkout_url": link["short_url"], "licence_id": licence_id, "price_paise": int(price),
            "currency": currency, "version": int(row["version"])}


# ------------------------------------------------------------------ activation / refund

async def activate_by_link(pool: Any, *, payment_link_id: str, payment_id: str, amount_paise: int,
                           licence_id: Optional[str] = None) -> bool:
    """A verified `payment_link.paid`. Idempotent on the payment id; the amount must match."""
    if not payment_link_id and not licence_id:
        return False
    row = None
    if payment_link_id:
        row = await pool.fetchrow(
            "SELECT id::text AS id, amount_paise, status FROM procedure_licences WHERE payment_link_id = $1",
            str(payment_link_id))
    if row is None and licence_id:
        row = await pool.fetchrow(
            "SELECT id::text AS id, amount_paise, status FROM procedure_licences WHERE id = $1::uuid",
            str(licence_id))
    if row is None:
        return False
    if int(row["amount_paise"]) != int(amount_paise):
        log.error("procedure licence %s: paid %s paise, expected %s; left pending",
                  row["id"], amount_paise, row["amount_paise"])
        return False
    if row["status"] == "paid":
        return False
    try:
        done = await pool.fetchval(
            "UPDATE procedure_licences SET status = 'paid', payment_ref = $2, granted_at = now(), "
            "expires_at = NULL, refunded_at = NULL WHERE id = $1::uuid AND status <> 'paid' RETURNING 1",
            row["id"], str(payment_id))
    except asyncpg.UniqueViolationError:
        return False                                  # the same payment id already granted a licence
    return bool(done)


async def activate_by_order(pool: Any, *, order_id: str, payment_id: str, amount_paise: int) -> bool:
    """A `payment.captured` whose order belongs to a licence (when the gateway gives us the order id)."""
    row = await pool.fetchrow(
        "SELECT id::text AS id, amount_paise, status FROM procedure_licences WHERE order_id = $1", str(order_id))
    if row is None:
        return False
    return await activate_by_link(pool, payment_link_id="", payment_id=payment_id,
                                  amount_paise=amount_paise, licence_id=row["id"])


async def close_link(pool: Any, *, payment_link_id: str, status: str) -> None:
    """`payment_link.cancelled` / `payment_link.expired`: the pending attempt is over."""
    if status not in ("expired", "failed"):
        return
    await pool.execute(
        "UPDATE procedure_licences SET status = $2 WHERE payment_link_id = $1 AND status = 'pending'",
        str(payment_link_id), status)


async def refund_by_payment(pool: Any, *, payment_id: str) -> Optional[str]:
    """A verified refund: the licence becomes refunded and the gate refuses future runs."""
    return await pool.fetchval(
        "UPDATE procedure_licences SET status = 'refunded', refunded_at = now() "
        "WHERE payment_ref = $1 AND status = 'paid' RETURNING id::text", str(payment_id))


# ------------------------------------------------------------------ price administration

async def set_price(pool: Any, *, procedure_row_id: str, price_paise: Optional[int],
                    currency: str = "INR", priced_by: str = "operator") -> dict[str, Any]:
    """Price one exact version. None un-prices it; 0 is a deliberate free listing."""
    if price_paise is not None:
        price_paise = int(price_paise)
        if not 0 <= price_paise <= MAX_PRICE_PAISE:
            raise PurchaseError(f"price_paise must be between 0 and {MAX_PRICE_PAISE}")
    if currency not in ("INR", "USD"):
        raise PurchaseError("currency must be INR or USD")
    row = await pool.fetchrow(
        """UPDATE procedures
           SET price_paise = $2, price_currency = $3, priced_by = $4, priced_at = now()
           WHERE id::text = $1 AND t_invalid IS NULL
           RETURNING id::text AS id, procedure_id::text AS procedure_id, version, name,
                     price_paise, price_currency""",
        str(procedure_row_id), price_paise, currency, str(priced_by))
    if row is None:
        raise PurchaseError("no such Way", 404)
    return dict(row)


# ------------------------------------------------------------------ reconcile

async def reconcile_link(pool: Any, *, licence_id: str) -> dict[str, Any]:
    """Ask the gateway about one pending licence. The fallback for a webhook that never arrived."""
    row = await pool.fetchrow(
        "SELECT id::text AS id, payment_link_id, amount_paise, status FROM procedure_licences WHERE id = $1::uuid",
        str(licence_id))
    if row is None:
        raise PurchaseError("no such licence", 404)
    if row["status"] == "paid" or not row.get("payment_link_id"):
        return {"licence_id": row["id"], "status": row["status"], "changed": False}
    link = await fetch_payment_link(str(row["payment_link_id"]))
    if link is None:
        return {"licence_id": row["id"], "status": row["status"], "changed": False}
    if link.get("status") == "paid":
        paid = next((p for p in (link.get("payments") or []) if p.get("status") == "captured"), None)
        if paid is not None:
            changed = await activate_by_link(pool, payment_link_id=str(link.get("id") or ""),
                                             payment_id=str(paid.get("payment_id") or ""),
                                             amount_paise=int(paid.get("amount") or 0))
            return {"licence_id": row["id"], "status": "paid" if changed else row["status"], "changed": changed}
    return {"licence_id": row["id"], "status": row["status"], "changed": False}


# ------------------------------------------------------------------ CLI

async def _main_async(args: argparse.Namespace) -> None:
    from app.db.session import create_pool
    from app.config import settings

    dsn = args.dsn or settings.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("set DATABASE_URL (or pass --dsn)")
    pool = await create_pool(dsn)
    try:
        if args.command == "set-price":
            print(json.dumps(await set_price(pool, procedure_row_id=args.row,
                                             price_paise=None if args.unprice else args.paise,
                                             currency=args.currency, priced_by=args.by), indent=2, default=str))
        elif args.command == "show":
            print(json.dumps(await procedure_row(pool, args.row), indent=2, default=str))
            print(json.dumps(await licence_status(pool, procedure_row_id=args.row, buyer_user_id=args.buyer)
                             if args.buyer else "pass --buyer for licence status", indent=2, default=str))
        elif args.command == "reconcile":
            print(json.dumps(await reconcile_link(pool, licence_id=args.licence), indent=2, default=str))
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Price a Way version and reconcile procedure licences.")
    parser.add_argument("--dsn", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("set-price", help="price one exact procedure version")
    p.add_argument("--row", required=True, help="procedures.id (the version row)")
    p.add_argument("--paise", type=int, default=15000)
    p.add_argument("--unprice", action="store_true", help="set NULL (free)")
    p.add_argument("--currency", default="INR")
    p.add_argument("--by", default="operator")
    s = sub.add_parser("show", help="show a version's price and a buyer's licence")
    s.add_argument("--row", required=True)
    s.add_argument("--buyer", default=None)
    r = sub.add_parser("reconcile", help="ask the gateway about one pending licence")
    r.add_argument("--licence", required=True)
    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
