"""
The admin dashboard's billing endpoints (``/v1/admin/billing/...``): what was ordered and paid in rupees, whether each order was
credited, GST tax invoices and credit notes, and the seller details that print on them. Money in is recorded by the payment webhook
(app/mcp_server/billing_routes.py); this module only reads it and issues invoices, so nothing here can add or remove a credit.

Who may call: a signed-in HUMAN who holds the platform ``admin:ops`` scope (the platform_admin grant). Unlike the older /v1/admin
routes it does NOT accept the shared static admin key, because money records must be attributable to a person. While
``ADMIN_REQUIRE_MFA`` is on, the token must also carry the second-factor level (``aal2``): sign in with an authenticator code first.
It is off by default so the console can be tried before anyone has enrolled; turn it on for production (docs/admin_login.md).
"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_auth_context
from app.services import auth_context as _ac
from app.services import invoicing
from app.services.access import TenantScope, tenant_transaction
from app.services.audit import record_security_event

router = APIRouter(prefix="/v1/admin/billing", tags=["admin-billing"])


async def get_pool(request: Request):
    return request.app.state.pool

STALE_ORDER_MINUTES = 30


def mfa_required() -> bool:
    return (os.environ.get("ADMIN_REQUIRE_MFA", "off") or "off").strip().lower() in ("1", "on", "true", "yes")


def check_billing_admin(ctx: Any, *, mfa: Optional[bool] = None) -> None:
    """Raises 403 unless this is a person with the admin scope (and the second factor, when required). Pure, so it is testable."""
    if not isinstance(ctx, _ac.AuthContext) or not ctx.has_scope(_ac.ADMIN_OPS):
        raise HTTPException(403, "billing admin access needed (a platform admin signed in as themselves)")
    if (mfa_required() if mfa is None else mfa) and (ctx.claims or {}).get("aal") != "aal2":
        raise HTTPException(403, "multi-factor sign-in required: sign in again with your authenticator code")


async def billing_admin(request: Request) -> Any:
    ctx = await get_auth_context(request)
    check_billing_admin(ctx)
    return ctx


def _problem(exc: invoicing.InvoiceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _audit(pool: Any, ctx: Any, action: str, object_id: str, details: Optional[dict] = None) -> None:
    await record_security_event(pool, actor_subject=ctx.subject, action=action, object_type="billing", object_id=object_id[:200], details=details or {})


@router.get("/overview")
async def overview(ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as c:
        orders = await c.fetchrow("SELECT count(*) AS n, COALESCE(sum(amount_paise), 0) AS paise FROM billing_orders")
        credits = await c.fetchrow("SELECT count(*) AS n, COALESCE(sum(amount_paise), 0) AS paise FROM org_wallet_credits WHERE source = 'razorpay'")
        stale = await c.fetchval(
            "SELECT count(*) FROM billing_orders o WHERE o.created_at < now() - make_interval(mins => $1) AND NOT EXISTS "
            "(SELECT 1 FROM org_wallet_credits w WHERE w.order_id = o.order_id)", STALE_ORDER_MINUTES)
        inv = await c.fetchrow("SELECT count(*) FILTER (WHERE kind = 'invoice') AS invoices, count(*) FILTER (WHERE kind = 'credit_note') AS notes, "
                               "COALESCE(sum(total_paise) FILTER (WHERE kind = 'invoice'), 0) AS invoiced, "
                               "COALESCE(sum(total_paise) FILTER (WHERE kind = 'credit_note'), 0) AS noted FROM invoices")
        uninvoiced = await c.fetchval("SELECT count(*) FROM org_wallet_credits w WHERE w.source = 'razorpay' AND NOT EXISTS "
                                      "(SELECT 1 FROM invoices i WHERE i.payment_ref = w.payment_ref AND i.kind = 'invoice')")
    return {
        "orders": int(orders["n"]), "ordered_paise": int(orders["paise"]), "payments": int(credits["n"]), "paid_paise": int(credits["paise"]),
        "stale_orders": int(stale), "stale_after_minutes": STALE_ORDER_MINUTES, "invoices": int(inv["invoices"]), "credit_notes": int(inv["notes"]),
        "invoiced_paise": int(inv["invoiced"]), "credit_noted_paise": int(inv["noted"]), "payments_without_invoice": int(uninvoiced),
        "mfa_required": mfa_required(),
    }


@router.get("/orders")
async def orders(limit: int = Query(100, ge=1, le=500), ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as c:
        rows = await c.fetch(
            "SELECT o.order_id, o.organization_id::text AS organization_id, g.name AS organization, o.amount_paise, o.created_by, o.created_at, "
            "EXISTS (SELECT 1 FROM org_wallet_credits w WHERE w.order_id = o.order_id) AS credited FROM billing_orders o "
            "LEFT JOIN organizations g ON g.id = o.organization_id ORDER BY o.created_at DESC LIMIT $1", limit)
    return {"orders": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/payments")
async def payments(limit: int = Query(100, ge=1, le=500), ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    async with tenant_transaction(pool, TenantScope.unrestricted()) as c:
        rows = await c.fetch(
            "SELECT w.payment_ref, w.order_id, w.source, w.organization_id::text AS organization_id, g.name AS organization, w.amount_paise, "
            "w.amount_usd::text AS amount_usd, w.created_at, i.id::text AS invoice_id, i.number AS invoice_number FROM org_wallet_credits w "
            "LEFT JOIN organizations g ON g.id = w.organization_id "
            "LEFT JOIN invoices i ON i.payment_ref = w.payment_ref AND i.kind = 'invoice' ORDER BY w.created_at DESC LIMIT $1", limit)
    return {"payments": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/reconciliation")
async def reconciliation(ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    """Things a person should look at: orders that were never credited, and payments that have no invoice."""
    async with tenant_transaction(pool, TenantScope.unrestricted()) as c:
        stale = await c.fetch(
            "SELECT o.order_id, o.organization_id::text AS organization_id, g.name AS organization, o.amount_paise, o.created_at FROM billing_orders o "
            "LEFT JOIN organizations g ON g.id = o.organization_id WHERE o.created_at < now() - make_interval(mins => $1) AND NOT EXISTS "
            "(SELECT 1 FROM org_wallet_credits w WHERE w.order_id = o.order_id) ORDER BY o.created_at DESC LIMIT 200", STALE_ORDER_MINUTES)
        bare = await c.fetch(
            "SELECT w.payment_ref, w.organization_id::text AS organization_id, g.name AS organization, w.amount_paise, w.created_at FROM org_wallet_credits w "
            "LEFT JOIN organizations g ON g.id = w.organization_id WHERE w.source = 'razorpay' AND NOT EXISTS "
            "(SELECT 1 FROM invoices i WHERE i.payment_ref = w.payment_ref AND i.kind = 'invoice') ORDER BY w.created_at DESC LIMIT 200")
    fix = lambda rows: [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]  # noqa: E731
    return {"uncredited_orders": fix(stale), "payments_without_invoice": fix(bare), "stale_after_minutes": STALE_ORDER_MINUTES,
            "note": "An old order with no credit usually means the customer did not finish paying. Check the gateway before acting: this page never creates a credit."}


@router.get("/seller")
async def seller(ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    p = await invoicing.get_profile(pool)
    return {"profile": p, "problems": invoicing.seller_problems(p), "e_invoicing": "not built: the government e-invoice portal (IRN and QR code) is not connected"}


@router.put("/seller")
async def save_seller(body: dict, ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    try:
        p = await invoicing.save_profile(pool, body, ctx.subject)
    except invoicing.InvoiceError as exc:
        raise _problem(exc) from exc
    await _audit(pool, ctx, "billing.seller_saved", "seller_profile", {"gstin": p.get("gstin")})
    return {"profile": p, "problems": invoicing.seller_problems(p)}


@router.get("/invoices")
async def invoices(limit: int = Query(100, ge=1, le=500), organization_id: Optional[str] = None, ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    return {"invoices": await invoicing.list_invoices(pool, limit=limit, organization_id=organization_id)}


@router.get("/invoices/{invoice_id}")
async def invoice_one(invoice_id: str, ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    inv = await invoicing.get_invoice(pool, invoice_id)
    if inv is None:
        raise HTTPException(404, "no such invoice")
    return inv


@router.post("/invoices", status_code=201)
async def issue(body: dict, ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    try:
        inv = await invoicing.issue_invoice(pool, payment_ref=str(body.get("payment_ref") or ""), buyer=body.get("buyer") or {}, actor=ctx.subject)
    except invoicing.InvoiceError as exc:
        raise _problem(exc) from exc
    if not inv.get("already_issued"):
        await _audit(pool, ctx, "billing.invoice_issued", inv["number"], {"payment_ref": inv["payment_ref"], "total_paise": inv["total_paise"]})
    return inv


@router.post("/invoices/{invoice_id}/credit-note", status_code=201)
async def credit_note(invoice_id: str, ctx=Depends(billing_admin), pool=Depends(get_pool)) -> dict:
    try:
        note = await invoicing.cancel_invoice(pool, invoice_id=invoice_id, actor=ctx.subject)
    except invoicing.InvoiceError as exc:
        raise _problem(exc) from exc
    if not note.get("already_issued"):
        await _audit(pool, ctx, "billing.credit_note_issued", note["number"], {"invoice": invoice_id, "total_paise": note["total_paise"]})
    return note
