"""
GST tax invoices for INR payments, and the credit notes that reverse them (db/161_invoicing.sql). Used by the admin billing routes
(app/api/admin_billing.py), never by the payment webhook: taking money and issuing an invoice are separate steps, so a problem with
invoice details can never hold up or undo a credit.

What this does and does not claim:
  * It records an invoice the way Rule 46 of the CGST Rules describes one (a consecutive number of at most 16 characters, seller and
    buyer details, place of supply, SAC code, taxable value, the CGST/SGST or IGST split and the total), and it refuses to issue
    one until the seller's legal details are complete. It does not decide any tax question: the rate, the SAC code and whether the
    amount a customer paid includes GST all come from the seller profile and from ``INCLUSIVE`` below, which are the founders' and
    their accountant's to set (docs/billing.md, "Open decisions").
  * It does NOT register invoices with the government e-invoice portal (IRN and QR code). That is required above a turnover
    threshold the research could not pin down (sources disagreed between 5 and 10 crore rupees), so it is not built and the admin
    page says so.
  * A payment amount is treated as the GST-INCLUSIVE total the buyer paid (Razorpay charges one amount). If the founders decide GST
    is added on top, the order amount must change first; this module would then record the larger amount as the total.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
STATE_RE = re.compile(r"^[0-9]{2}$")
INCLUSIVE = True


class InvoiceError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def financial_year(d: date) -> str:
    """The Indian financial year (April to March) as four digits: 2026-10-30 is '2627', 2027-02-01 is '2627'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def invoice_number(prefix: str, fy: str, n: int) -> str:
    """e.g. KEL/2627/000001. At most 4 + 1 + 4 + 1 + 6 = 16 characters, which is the GST limit."""
    out = f"{prefix}/{fy}/{n:06d}"
    if len(out) > 16:
        raise InvoiceError("the invoice number would be longer than 16 characters")
    return out


def valid_gstin(g: Optional[str]) -> bool:
    return bool(g and GSTIN_RE.match(g))


def state_of_gstin(g: str) -> str:
    return g[:2]


def gst_split(total_paise: int, rate_pct: Decimal, seller_state: str, supply_state: str, inclusive: bool = INCLUSIVE) -> dict[str, int]:
    """Splits a total into taxable value and tax, in whole paise that add up exactly to the total.

    Same state as the seller: half the tax is CGST and half SGST. Another state: all of it is IGST. With an odd number of tax paise
    the extra paisa goes to SGST (so CGST + SGST is exactly the tax)."""
    if total_paise <= 0:
        raise InvoiceError("an invoice needs a positive amount")
    rate = Decimal(rate_pct)
    if not inclusive:
        raise InvoiceError("GST added on top of the amount is not supported yet; the order amount must include it")
    taxable = int((Decimal(total_paise) * 100 / (100 + rate)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    tax = total_paise - taxable
    if seller_state == supply_state:
        cgst = tax // 2
        return {"taxable": taxable, "cgst": cgst, "sgst": tax - cgst, "igst": 0}
    return {"taxable": taxable, "cgst": 0, "sgst": 0, "igst": tax}


REQUIRED_SELLER = ("legal_name", "gstin", "state_code", "address", "sac_code")


def seller_problems(p: Optional[Mapping[str, Any]]) -> list[str]:
    """What is missing before a tax invoice can be issued (empty when complete)."""
    if not p:
        return ["the seller details have not been saved yet"]
    out = [f"{k.replace('_', ' ')} is missing" for k in REQUIRED_SELLER if not (p.get(k) or "").strip()]
    if p.get("gstin") and not valid_gstin(p["gstin"]):
        out.append("the GSTIN is not in the right format")
    if p.get("gstin") and p.get("state_code") and p["gstin"][:2] != p["state_code"]:
        out.append("the GSTIN's state code does not match the state code")
    return out


def clean_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    g = (str(raw.get("gstin") or "").strip().upper()) or None
    state = (str(raw.get("state_code") or "").strip()) or (state_of_gstin(g) if g and len(g) >= 2 else "")
    if g and not valid_gstin(g):
        raise InvoiceError("the GSTIN is not in the right format (15 characters, for example 27ABCDE1234F1Z5)")
    if state and not STATE_RE.match(state):
        raise InvoiceError("the state code is two digits, for example 27 for Maharashtra")
    if g and state and g[:2] != state:
        raise InvoiceError("the GSTIN starts with a different state code than the one given")
    try:
        rate = Decimal(str(raw.get("gst_rate_pct", 18)))
    except Exception:  # noqa: BLE001
        raise InvoiceError("the GST rate must be a number")
    if not Decimal(0) <= rate <= Decimal(40):
        raise InvoiceError("the GST rate must be between 0 and 40")
    prefix = (str(raw.get("invoice_prefix") or "KEL").strip().upper())
    if not re.match(r"^[A-Z0-9]{1,4}$", prefix):
        raise InvoiceError("the invoice prefix is 1 to 4 letters or digits")
    name = str(raw.get("legal_name") or "").strip()
    if not name:
        raise InvoiceError("the legal name is needed")
    return {"legal_name": name[:200], "trade_name": (str(raw.get("trade_name") or "").strip()[:200]) or None, "gstin": g,
            "state_code": state or None, "address": (str(raw.get("address") or "").strip()[:500]) or None,
            "sac_code": (str(raw.get("sac_code") or "").strip()[:12]) or None, "gst_rate_pct": rate, "invoice_prefix": prefix}


def clean_buyer(raw: Mapping[str, Any]) -> dict[str, Optional[str]]:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise InvoiceError("the buyer's name is needed")
    g = (str(raw.get("gstin") or "").strip().upper()) or None
    if g and not valid_gstin(g):
        raise InvoiceError("the buyer's GSTIN is not in the right format")
    state = (str(raw.get("state_code") or "").strip()) or (state_of_gstin(g) if g else "")
    if not STATE_RE.match(state):
        raise InvoiceError("the buyer's state code (place of supply) is needed: two digits, for example 29 for Karnataka")
    if g and g[:2] != state:
        raise InvoiceError("the buyer's GSTIN starts with a different state code than the one given")
    return {"name": name[:200], "gstin": g, "state_code": state, "address": (str(raw.get("address") or "").strip()[:500]) or None}


_COLS = ("id, number, fy, kind, organization_id::text AS organization_id, payment_ref, credit_for::text AS credit_for, seller_name, "
         "seller_gstin, seller_state_code, seller_address, buyer_name, buyer_gstin, buyer_state_code, buyer_address, place_of_supply, "
         "description, sac_code, rate_pct, taxable_paise, cgst_paise, sgst_paise, igst_paise, total_paise, issued_by, issued_at")


def _json(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["id"] = str(out["id"])
    out["rate_pct"] = str(out["rate_pct"])
    out["issued_at"] = out["issued_at"].isoformat()
    return out


async def get_profile(pool: Any) -> Optional[dict[str, Any]]:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow("SELECT legal_name, trade_name, gstin, state_code, address, sac_code, gst_rate_pct, invoice_prefix, "
                                  "updated_by, updated_at FROM seller_profile WHERE id = 1")
    if row is None:
        return None
    out = dict(row)
    out["gst_rate_pct"] = str(out["gst_rate_pct"])
    out["updated_at"] = out["updated_at"].isoformat()
    return out


async def save_profile(pool: Any, raw: Mapping[str, Any], actor: str) -> dict[str, Any]:
    p = clean_profile(raw)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        await conn.execute(
            "INSERT INTO seller_profile (id, legal_name, trade_name, gstin, state_code, address, sac_code, gst_rate_pct, invoice_prefix, "
            "updated_by, updated_at) VALUES (1, $1, $2, $3, $4, $5, $6, $7, $8, $9, now()) ON CONFLICT (id) DO UPDATE SET "
            "legal_name = EXCLUDED.legal_name, trade_name = EXCLUDED.trade_name, gstin = EXCLUDED.gstin, state_code = EXCLUDED.state_code, "
            "address = EXCLUDED.address, sac_code = EXCLUDED.sac_code, gst_rate_pct = EXCLUDED.gst_rate_pct, "
            "invoice_prefix = EXCLUDED.invoice_prefix, updated_by = EXCLUDED.updated_by, updated_at = now()",
            p["legal_name"], p["trade_name"], p["gstin"], p["state_code"], p["address"], p["sac_code"], p["gst_rate_pct"],
            p["invoice_prefix"], actor)
    return (await get_profile(pool)) or {}


async def _next_number(conn: Any, prefix: str, fy: str) -> str:
    n = await conn.fetchval("INSERT INTO invoice_counters (fy, last_no) VALUES ($1, 1) ON CONFLICT (fy) DO UPDATE SET "
                            "last_no = invoice_counters.last_no + 1 RETURNING last_no", fy)
    return invoice_number(prefix, fy, int(n))


async def issue_invoice(pool: Any, *, payment_ref: str, buyer: Mapping[str, Any], actor: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """The tax invoice for one verified payment. Returns the existing one if the payment already has an invoice (so a double click or a
    retry never makes a second)."""
    b = clean_buyer(buyer)
    now = now or datetime.now(timezone.utc)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        again = await conn.fetchrow(f"SELECT {_COLS} FROM invoices WHERE payment_ref = $1 AND kind = 'invoice'", payment_ref)
        if again is not None:
            return {**_json(again), "already_issued": True}
        profile = await conn.fetchrow("SELECT legal_name, gstin, state_code, address, sac_code, gst_rate_pct, invoice_prefix "
                                      "FROM seller_profile WHERE id = 1")
        problems = seller_problems(dict(profile) if profile else None)
        if problems:
            raise InvoiceError("set the seller details first: " + "; ".join(problems), 409)
        credit = await conn.fetchrow("SELECT organization_id::text AS organization_id, amount_paise FROM org_wallet_credits WHERE payment_ref = $1",
                                     payment_ref)
        if credit is None:
            raise InvoiceError("no verified payment with that reference", 404)
        split = gst_split(int(credit["amount_paise"]), Decimal(profile["gst_rate_pct"]), profile["state_code"], b["state_code"])
        fy = financial_year(now.astimezone(timezone(offset=__import__("datetime").timedelta(hours=5, minutes=30))).date())
        number = await _next_number(conn, profile["invoice_prefix"], fy)
        row = await conn.fetchrow(
            f"INSERT INTO invoices (id, number, fy, kind, organization_id, payment_ref, seller_name, seller_gstin, seller_state_code, "
            f"seller_address, buyer_name, buyer_gstin, buyer_state_code, buyer_address, place_of_supply, description, sac_code, rate_pct, "
            f"taxable_paise, cgst_paise, sgst_paise, igst_paise, total_paise, issued_by, issued_at) VALUES "
            f"($1, $2, $3, 'invoice', $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12, $13, $12, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23) "
            f"RETURNING {_COLS}",
            uuid.uuid4(), number, fy, credit["organization_id"], payment_ref, profile["legal_name"], profile["gstin"], profile["state_code"],
            profile["address"], b["name"], b["gstin"], b["state_code"], b["address"], "Prepaid credits for the keळ service",
            profile["sac_code"], profile["gst_rate_pct"], split["taxable"], split["cgst"], split["sgst"], split["igst"],
            int(credit["amount_paise"]), actor, now)
    return {**_json(row), "already_issued": False}


async def cancel_invoice(pool: Any, *, invoice_id: str, actor: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """A credit note for the whole invoice. The invoice itself is never edited; a corrected invoice is issued separately."""
    now = now or datetime.now(timezone.utc)
    try:
        iid = uuid.UUID(invoice_id)
    except ValueError:
        raise InvoiceError("not a valid invoice id", 400)
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        inv = await conn.fetchrow(f"SELECT {_COLS} FROM invoices WHERE id = $1", iid)
        if inv is None:
            raise InvoiceError("no such invoice", 404)
        if inv["kind"] != "invoice":
            raise InvoiceError("a credit note cannot be cancelled", 409)
        done = await conn.fetchrow(f"SELECT {_COLS} FROM invoices WHERE credit_for = $1", iid)
        if done is not None:
            return {**_json(done), "already_issued": True}
        profile = await conn.fetchrow("SELECT invoice_prefix FROM seller_profile WHERE id = 1")
        fy = financial_year(now.astimezone(timezone(offset=__import__("datetime").timedelta(hours=5, minutes=30))).date())
        number = await _next_number(conn, (profile["invoice_prefix"] if profile else "KEL"), fy)
        row = await conn.fetchrow(
            f"INSERT INTO invoices (id, number, fy, kind, organization_id, payment_ref, credit_for, seller_name, seller_gstin, seller_state_code, "
            f"seller_address, buyer_name, buyer_gstin, buyer_state_code, buyer_address, place_of_supply, description, sac_code, rate_pct, "
            f"taxable_paise, cgst_paise, sgst_paise, igst_paise, total_paise, issued_by, issued_at) VALUES "
            f"($1, $2, $3, 'credit_note', $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25) "
            f"RETURNING {_COLS}",
            uuid.uuid4(), number, fy, inv["organization_id"], inv["payment_ref"], iid, inv["seller_name"], inv["seller_gstin"],
            inv["seller_state_code"], inv["seller_address"], inv["buyer_name"], inv["buyer_gstin"], inv["buyer_state_code"], inv["buyer_address"],
            inv["place_of_supply"], f"Credit note against {inv['number']}", inv["sac_code"], inv["rate_pct"], inv["taxable_paise"],
            inv["cgst_paise"], inv["sgst_paise"], inv["igst_paise"], inv["total_paise"], actor, now)
    return {**_json(row), "already_issued": False}


async def list_invoices(pool: Any, *, limit: int = 100, organization_id: Optional[str] = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        if organization_id:
            rows = await conn.fetch(f"SELECT {_COLS} FROM invoices WHERE organization_id = $1::uuid ORDER BY issued_at DESC LIMIT $2", organization_id, limit)
        else:
            rows = await conn.fetch(f"SELECT {_COLS} FROM invoices ORDER BY issued_at DESC LIMIT $1", limit)
    return [_json(r) for r in rows]


async def get_invoice(pool: Any, invoice_id: str) -> Optional[dict[str, Any]]:
    try:
        iid = uuid.UUID(invoice_id)
    except ValueError:
        return None
    async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
        row = await conn.fetchrow(f"SELECT {_COLS} FROM invoices WHERE id = $1", iid)
    return _json(row) if row else None
