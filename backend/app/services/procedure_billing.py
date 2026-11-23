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
    """A paid Way was run without a purchase. The named refusal the gate raises."""
    status_code = 402

    def __init__(self, price_paise: int, currency: str = "INR"):
        self.price_paise = int(price_paise)
        self.currency = currency or "INR"
        amount = f"{self.price_paise / 100:.2f}"
        super().__init__(f"this Way costs {self.currency} {amount} for one output; buy it before running it")


class ProcedureAlreadyUsed(ProcedureBillingError):
    """The buyer already received this Way's output; another output is another purchase."""
    status_code = 402

    def __init__(self, price_paise: int, currency: str = "INR"):
        self.price_paise = int(price_paise)
        self.currency = currency or "INR"
        amount = f"{self.price_paise / 100:.2f}"
        super().__init__(f"you already received this Way's output; buy another output for {self.currency} {amount}")


class ProcedureInUse(ProcedureBillingError):
    """A purchase is already bound to a run that is producing the output."""
    status_code = 409

    def __init__(self, message: str = "this purchase is already producing an output; wait for that run or cancel it"):
        super().__init__(message)


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
    """One active procedure version by row id, stable procedure_id, or a goal submission id.

    When the ref is a submission, the submission id is carried on the answer so the purchase can
    return the buyer to that Way's own page rather than to whatever page the browser came from.
    """
    text = str(ref or "")
    sub = await pool.fetchrow(
        "SELECT id::text AS submission_id, parent_procedure_id::text AS parent "
        "FROM goal_submissions WHERE id::text = $1", text)
    if sub is not None and sub.get("parent"):
        text = str(sub["parent"])
    row = await pool.fetchrow(
        """SELECT id::text AS id, procedure_id::text AS procedure_id, version, name, goal,
                  price_paise, price_currency, availability::text AS availability,
                  visibility::text AS visibility, owner_id
           FROM procedures
           WHERE (id::text = $1 OR procedure_id::text = $1) AND t_invalid IS NULL AND availability = 'active'
           ORDER BY (id::text = $1) DESC, version DESC LIMIT 1""", text)
    if row is None:
        return None
    out = dict(row)
    out["submission_id"] = (sub or {}).get("submission_id")
    if out["submission_id"] is None:
        # Bought by procedure row rather than by Way: return the newest published Way of this version,
        # so the buyer still lands on a paid page instead of the bare site.
        latest = await pool.fetchrow(
            "SELECT id::text AS submission_id FROM goal_submissions "
            "WHERE parent_procedure_id = $1::uuid AND status IN ('approved', 'ai_approved') "
            "ORDER BY created_at DESC LIMIT 1", out["id"])
        out["submission_id"] = latest["submission_id"] if latest else None
    return out


async def purchase_status(pool: Any, *, procedure_row_id: Any, buyer_user_id: Optional[str]) -> str:
    """'none' | 'paid' | 'used' | 'refunded' for this buyer and this exact version.

    'paid' means at least one output is bought and not yet produced. 'used' means every paid
    output was already produced; producing another one needs another purchase. A buyer may hold
    several paid, unconsumed purchases at once (a prepaid batch or an operator comp), so the
    answer is "is there any unconsumed paid row", not "what does the newest row say".
    """
    buyer = _uuid(buyer_user_id)
    row_id = _uuid(procedure_row_id)
    if buyer is None or row_id is None:
        return "none"
    rows = await pool.fetch(
        "SELECT status, consumed_at, expires_at FROM procedure_purchases "
        "WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid ORDER BY created_at DESC",
        row_id, buyer)
    now = datetime.now(timezone.utc)
    paid_unconsumed = False
    paid_consumed = False
    refunded = False
    for r in rows:
        if r["status"] == "paid":
            if r["consumed_at"] is not None:
                paid_consumed = True
            elif r["expires_at"] is None or r["expires_at"] > now:
                paid_unconsumed = True
        elif r["status"] == "refunded":
            refunded = True
    if paid_unconsumed:
        return "paid"
    if paid_consumed:
        return "used"
    if refunded:
        return "refunded"
    return "none"


async def licence_status(pool: Any, *, procedure_row_id: Any, buyer_user_id: Optional[str]) -> str:
    """Compatibility for the old field: a paid, unconsumed purchase reads as 'purchased'."""
    status = await purchase_status(pool, procedure_row_id=procedure_row_id, buyer_user_id=buyer_user_id)
    return "purchased" if status == "paid" else ("refunded" if status == "refunded" else "none")


async def check_entitlement(pool: Any, *, procedure_row_id: Any,
                            buyer_user_id: Optional[str]) -> Optional[dict[str, Any]]:
    """The gate: return the paid, unconsumed purchase for this version, or raise the named refusal.

    A free version (price NULL or 0) returns None and never queries a purchase. A paid version with
    no purchase raises ProcedureNotPurchased; a purchase already used raises ProcedureAlreadyUsed;
    one already bound to a running output raises ProcedureInUse. The returned row is what
    `way_runs.start` binds to the run so one purchase produces exactly one output.
    """
    row = await pool.fetchrow(
        "SELECT price_paise, price_currency FROM procedures WHERE id::text = $1 AND t_invalid IS NULL",
        str(procedure_row_id))
    if row is None:
        return None                             # the caller's own loader owns "no such Way"
    price = row["price_paise"]
    if price is None or int(price) <= 0:
        return None
    currency = row["price_currency"] or "INR"
    buyer = _uuid(buyer_user_id)
    row_id = _uuid(procedure_row_id)
    if buyer is None or row_id is None:
        raise ProcedureNotPurchased(int(price), currency)
    # Prefer a row no run has claimed yet; if every one is bound, the selected row
    # still triggers ProcedureInUse below, which is the honest refusal.
    purchase = await pool.fetchrow(
        "SELECT id::text AS id, status, consumed_at, used_by_run_id FROM procedure_purchases "
        "WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid AND status = 'paid' "
        "AND consumed_at IS NULL AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY (used_by_run_id IS NULL) DESC, created_at DESC LIMIT 1", row_id, buyer)
    if purchase is None:
        used = await pool.fetchval(
            "SELECT 1 FROM procedure_purchases WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid "
            "AND status = 'paid' AND consumed_at IS NOT NULL LIMIT 1", row_id, buyer)
        if used:
            raise ProcedureAlreadyUsed(int(price), currency)
        raise ProcedureNotPurchased(int(price), currency)
    if purchase["used_by_run_id"] is not None:
        raise ProcedureInUse()
    return dict(purchase)


async def bind_purchase(pool: Any, *, purchase_id: str, run_id: str) -> None:
    """Bind one purchase to the run that will produce its output; a second run is refused."""
    done = await pool.fetchval(
        "UPDATE procedure_purchases SET used_by_run_id = $2::uuid "
        "WHERE id = $1::uuid AND status = 'paid' AND consumed_at IS NULL "
        "AND (used_by_run_id IS NULL OR used_by_run_id = $2::uuid) RETURNING 1",
        str(purchase_id), str(run_id))
    if not done:
        raise ProcedureInUse()


async def consume_run(pool: Any, run_id: str) -> None:
    """The run reached done: its output was produced, so the purchase is spent."""
    await pool.execute(
        "UPDATE procedure_purchases SET consumed_at = now() "
        "WHERE used_by_run_id = $1::uuid AND consumed_at IS NULL", str(run_id))


async def release_run(pool: Any, run_id: str) -> None:
    """The run failed or was cancelled: release the purchase so the person can try again."""
    await pool.execute(
        "UPDATE procedure_purchases SET used_by_run_id = NULL "
        "WHERE used_by_run_id = $1::uuid AND consumed_at IS NULL", str(run_id))


async def may_download(pool: Any, *, procedure_row_id: Any, buyer_user_id: Optional[str],
                       run_id: str) -> bool:
    """Can this person download a finished run's files?

    Deliberately NOT `check_entitlement`: that gate wants an *unconsumed* purchase, and by the time a buyer
    downloads, the run has consumed it. The rule here is the inverse — a purchase this person paid for, whose
    output was produced by *this* run. A free Way (NULL or 0) is open to anyone, same as its page.
    """
    row = await pool.fetchrow(
        "SELECT price_paise FROM procedures WHERE id::text = $1 AND t_invalid IS NULL", str(procedure_row_id))
    if row is None or row["price_paise"] is None or int(row["price_paise"]) <= 0:
        return True
    buyer = _uuid(buyer_user_id)
    row_id = _uuid(procedure_row_id)
    if buyer is None or row_id is None:
        return False
    return bool(await pool.fetchval(
        "SELECT 1 FROM procedure_purchases WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid "
        "AND status = 'paid' AND consumed_at IS NOT NULL AND used_by_run_id = $3::uuid LIMIT 1",
        row_id, buyer, str(run_id)))


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
    async with http_client(timeout=20.0) as client:
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
    async with http_client(timeout=6.0) as client:
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
    """Open (or reuse) a hosted checkout for ONE output of one version.

    One active purchase at a time (the partial unique index). A paid, unconsumed purchase is already
    the output the buyer asked for; a used or refunded purchase does not block buying another output
    later, which is why this inserts a new row instead of reusing the old one.
    """
    row = await procedure_row(pool, procedure_row_id)
    if row is None:
        raise PurchaseError("no such Way", 404)
    price = row["price_paise"]
    if price is None or int(price) <= 0:
        raise PurchaseError("this Way is free; no purchase is needed", 400)
    buyer = _uuid(buyer_user_id)
    if buyer is None:
        raise PurchaseError("sign in with a kell account to buy", 401)
    # The return page is explicit, never the Referer: a purchase made from /route used to send the
    # buyer back to /route. The Way's paid page offers the two ways to produce the output.
    submission_id = row.get("submission_id")
    if callback_url:
        callback = _callback(callback_url)
    elif submission_id:
        callback = f"https://www.kell.org.in/way/{submission_id}/paid"
    else:
        callback = DEFAULT_CALLBACK
    currency = row["price_currency"] or "INR"

    active = await pool.fetchrow(
        "SELECT id::text AS id, status, payment_link_id, checkout_url, amount_paise, procedure_version "
        "FROM procedure_purchases WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid "
        "AND status IN ('pending', 'paid') AND consumed_at IS NULL "
        "ORDER BY created_at DESC LIMIT 1", row["id"], buyer)
    purchase_id: Optional[str] = None
    if active is not None:
        if active["status"] == "paid":
            return {"checkout_url": callback, "already": True, "purchase_status": "paid",
                    "licence_status": "purchased", "purchase_id": active["id"],
                    "price_paise": int(active["amount_paise"] or price), "currency": currency,
                    "version": int(active["procedure_version"] or row["version"])}
        purchase_id = active["id"]
        if active.get("checkout_url"):
            # The link already exists: hand it back without asking the gateway again. This is what
            # makes a pre-warmed checkout feel instant.
            return {"checkout_url": active["checkout_url"], "purchase_id": active["id"],
                    "purchase_status": "pending", "price_paise": int(price),
                    "currency": currency, "version": int(row["version"])}
        if active.get("payment_link_id"):
            link = await fetch_payment_link(str(active["payment_link_id"]))
            if link is not None:
                if link.get("status") in ("created", "partially_paid") and link.get("short_url"):
                    await pool.execute(
                        "UPDATE procedure_purchases SET checkout_url = $2 WHERE id = $1::uuid",
                        active["id"], link["short_url"])
                    return {"checkout_url": link["short_url"], "purchase_id": active["id"],
                            "purchase_status": "pending", "price_paise": int(price),
                            "currency": currency, "version": int(row["version"])}
                if link.get("status") == "paid":
                    paid = next((p for p in (link.get("payments") or []) if p.get("status") == "captured"), None)
                    if paid is not None:
                        await activate_by_link(pool, payment_link_id=str(link.get("id") or ""),
                                               payment_id=str(paid.get("payment_id") or ""),
                                               amount_paise=int(paid.get("amount") or 0),
                                               purchase_id=active["id"])
                        return {"checkout_url": callback, "already": True, "purchase_status": "paid",
                                "licence_status": "purchased", "purchase_id": active["id"],
                                "price_paise": int(price), "currency": currency,
                                "version": int(row["version"])}

    if purchase_id is None:
        try:
            purchase_id = await pool.fetchval(
                """INSERT INTO procedure_purchases
                       (procedure_id, procedure_row_id, procedure_version, buyer_user_id, amount_paise, currency,
                        submission_id)
                   VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7::uuid)
                   RETURNING id::text""",
                row["procedure_id"], row["id"], int(row["version"]), buyer, int(price), currency,
                submission_id)
        except asyncpg.UniqueViolationError:
            # Another request opened the active purchase between the read and the insert.
            active = await pool.fetchrow(
                "SELECT id::text AS id, status, payment_link_id, checkout_url, amount_paise, procedure_version "
                "FROM procedure_purchases WHERE procedure_row_id = $1::uuid AND buyer_user_id = $2::uuid "
                "AND status IN ('pending', 'paid') AND consumed_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1", row["id"], buyer)
            if active is not None and active["status"] == "paid":
                return {"checkout_url": callback, "already": True, "purchase_status": "paid",
                        "licence_status": "purchased", "purchase_id": active["id"],
                        "price_paise": int(active["amount_paise"] or price), "currency": currency,
                        "version": int(active["procedure_version"] or row["version"])}
            raise PurchaseError("a purchase is already open for this output; try again", 409)

    link = await create_payment_link(
        amount_paise=int(price), description=f"{row['name']} (v{row['version']}) - one output",
        reference_id=purchase_id, callback_url=callback,
        notes={"purchase_id": purchase_id, "procedure_row_id": row["id"], "buyer_user_id": buyer})
    await pool.execute(
        "UPDATE procedure_purchases SET payment_link_id = $2, order_id = $3, checkout_url = $4 WHERE id = $1::uuid",
        purchase_id, str(link["id"]), (str(link["order_id"]) if link.get("order_id") else None),
        str(link["short_url"]))
    return {"checkout_url": link["short_url"], "purchase_id": purchase_id, "purchase_status": "pending",
            "price_paise": int(price), "currency": currency, "version": int(row["version"])}


# ------------------------------------------------------------------ activation / refund

async def activate_by_link(pool: Any, *, payment_link_id: str, payment_id: str, amount_paise: int,
                           purchase_id: Optional[str] = None) -> bool:
    """A verified `payment_link.paid`. Idempotent on the payment id; the amount must match.

    Only a PENDING purchase becomes paid. A used or refunded one is never reactivated by a late
    payment on an old link.
    """
    if not payment_link_id and not purchase_id:
        return False
    row = None
    if payment_link_id:
        row = await pool.fetchrow(
            "SELECT id::text AS id, amount_paise, status FROM procedure_purchases WHERE payment_link_id = $1",
            str(payment_link_id))
    if row is None and purchase_id:
        row = await pool.fetchrow(
            "SELECT id::text AS id, amount_paise, status FROM procedure_purchases WHERE id = $1::uuid",
            str(purchase_id))
    if row is None:
        return False
    if int(row["amount_paise"]) != int(amount_paise):
        log.error("procedure purchase %s: paid %s paise, expected %s; left pending",
                  row["id"], amount_paise, row["amount_paise"])
        return False
    if row["status"] != "pending":
        return False
    try:
        done = await pool.fetchval(
            "UPDATE procedure_purchases SET status = 'paid', payment_ref = $2, granted_at = now(), "
            "expires_at = NULL, refunded_at = NULL WHERE id = $1::uuid AND status = 'pending' RETURNING 1",
            row["id"], str(payment_id))
    except asyncpg.UniqueViolationError:
        return False                                  # the same payment id already paid a purchase
    return bool(done)


async def activate_by_order(pool: Any, *, order_id: str, payment_id: str, amount_paise: int) -> bool:
    """A `payment.captured` whose order belongs to a purchase (when the gateway gives us the order id)."""
    row = await pool.fetchrow(
        "SELECT id::text AS id, amount_paise, status FROM procedure_purchases WHERE order_id = $1", str(order_id))
    if row is None:
        return False
    return await activate_by_link(pool, payment_link_id="", payment_id=payment_id,
                                  amount_paise=amount_paise, purchase_id=row["id"])


async def close_link(pool: Any, *, payment_link_id: str, status: str) -> None:
    """`payment_link.cancelled` / `payment_link.expired`: the pending attempt is over."""
    if status not in ("expired", "failed"):
        return
    await pool.execute(
        "UPDATE procedure_purchases SET status = $2 WHERE payment_link_id = $1 AND status = 'pending'",
        str(payment_link_id), status)


async def refund_by_payment(pool: Any, *, payment_id: str) -> Optional[str]:
    """A verified refund: the licence becomes refunded and the gate refuses future runs."""
    return await pool.fetchval(
        "UPDATE procedure_purchases SET status = 'refunded', refunded_at = now() "
        "WHERE payment_ref = $1 AND status = 'paid' RETURNING id::text", str(payment_id))


# ------------------------------------------------------------------ price administration

async def set_price(pool: Any, *, procedure_row_id: str, price_paise: Optional[int],
                    currency: str = "INR", priced_by: str = "operator") -> dict[str, Any]:
    """Price one exact version. None un-prices it; 0 is a deliberate free listing.

    A Way that has never run cannot be priced above free (docs/blinkit/pricing_discussions.md D6). Its cost is
    unknown, so any positive number is a guess with a currency symbol on it — and since one purchase now buys exactly
    one run, an unmeasured price can lose money on every sale with nobody able to see it.

    Withdrawing a price (None) and listing free (0) are always allowed: neither has a margin to be wrong about.
    """
    if price_paise is not None:
        price_paise = int(price_paise)
        if not 0 <= price_paise <= MAX_PRICE_PAISE:
            raise PurchaseError(f"price_paise must be between 0 and {MAX_PRICE_PAISE}")
    if currency not in ("INR", "USD"):
        raise PurchaseError("currency must be INR or USD")
    row = await pool.fetchrow(
        """SELECT id::text AS id, name, version, measured_runs, measured_cost_paise
           FROM procedures WHERE id::text = $1 AND t_invalid IS NULL""",
        str(procedure_row_id))
    if row is None:
        raise PurchaseError("no such Way", 404)
    if price_paise is not None and price_paise > 0 and not int(row["measured_runs"] or 0):
        # Named, actionable, and it says what to do rather than only what went wrong.
        raise PurchaseError(
            f"{row['name']!r} v{row['version']} has not been run, so its cost is unknown; run it once, then price it",
            409)
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
        "SELECT id::text AS id, payment_link_id, amount_paise, status FROM procedure_purchases WHERE id = $1::uuid",
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
