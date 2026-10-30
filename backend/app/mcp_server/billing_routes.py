"""
Buying credits, on the hosted MCP service (the site calls these; Razorpay calls the webhook):

  GET  /v1/billing/wallet    the person's balance, history and what a rupee buys     (website session)
  POST /v1/billing/orders    {"amount_inr": 500} -> a Razorpay order for Checkout    (website session)
  POST /v1/billing/webhook   Razorpay's payment.captured / order.paid                (signature, no session)

The money goes to the person's PERSONAL organization (services/personal_org.py), whose policy requires a positive prepaid
balance. Crediting happens only in the webhook, from a payment Razorpay signed and an order WE recorded; the browser
returning from Checkout credits nothing, so a forged "success" page changes no balance. See services/wallet.py for the
signature rule, the idempotency key and the pricing rule.

Off (503) unless RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and KELL_PERSONAL_ORGS=on are set; the webhook additionally needs
RAZORPAY_WEBHOOK_SECRET. Test-mode keys work without any other change.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.mcp_server.chat_routes import BY_ADDRESS, client_address, cors_headers as _chat_cors, resolve_identity
from app.services import personal_org, wallet

log = logging.getLogger(__name__)
RAZORPAY_ORDERS = "https://api.razorpay.com/v1/orders"


def cors_headers(request: Request) -> dict[str, str]:
    h = _chat_cors(request)
    if "Access-Control-Allow-Origin" in h:
        h["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return h


def _json(request: Request, body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**cors_headers(request), "Cache-Control": "no-store"})


def _err(request: Request, status: int, message: str) -> JSONResponse:
    return _json(request, {"error": {"message": message}}, status)


def configured() -> bool:
    return bool(personal_org.enabled() and os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"))


def parse_amount_inr(value: Any) -> int:
    """Whole rupees within the allowed range -> paise. Raises wallet.WalletError otherwise."""
    try:
        inr = int(value)
    except (TypeError, ValueError):
        raise wallet.WalletError("amount_inr must be a whole number of rupees")
    if not wallet.MIN_TOPUP_INR <= inr <= wallet.MAX_TOPUP_INR:
        raise wallet.WalletError(f"amount_inr must be between {wallet.MIN_TOPUP_INR} and {wallet.MAX_TOPUP_INR}")
    return inr * 100


def payment_from_event(body: dict[str, Any]) -> Optional[dict[str, Any]]:
    """(payment id, order id, amount in paise) from a payment.captured or order.paid event, else None."""
    event = body.get("event")
    if event not in ("payment.captured", "order.paid"):
        return None
    pay = (((body.get("payload") or {}).get("payment") or {}).get("entity")) or {}
    if not pay.get("id") or not pay.get("order_id") or pay.get("currency", "INR") != "INR":
        return None
    try:
        return {"payment_id": str(pay["id"]), "order_id": str(pay["order_id"]), "amount": int(pay["amount"])}
    except (KeyError, TypeError, ValueError):
        return None


async def _create_razorpay_order(amount_paise: int, receipt: str) -> dict[str, Any]:
    from app.providers.adapters import http_client

    auth = (os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
    async with http_client(timeout=30.0) as client:
        r = await client.post(RAZORPAY_ORDERS, auth=auth, json={"amount": amount_paise, "currency": "INR",
                                                                "receipt": receipt[:40]})
    if r.status_code >= 300:
        raise wallet.WalletError("the payment provider refused to create the order")
    return r.json()


def register(server: Any, verifier: Any, pool_getter: Callable[[], Any], is_local: Callable[[Request], bool]) -> None:
    async def person(request: Request):
        """(identity, billing org id, None) or (None, None, an error response)."""
        if not configured():
            return None, None, _err(request, 503, "buying credits is not switched on yet")
        if not BY_ADDRESS.allow(client_address(request)):
            return None, None, _err(request, 429, "too many requests; slow down")
        who, refusal = await resolve_identity(verifier, pool_getter(), request, is_local)
        if refusal is not None:
            return None, None, _err(request, refusal[1], refusal[0])
        org = await personal_org._existing_personal(pool_getter(), who.user_id) if who.user_id else None
        if not org:
            return None, None, _err(request, 409, "your account has no wallet yet; try again in a moment")
        return who, org, None

    @server.custom_route("/v1/billing/wallet", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def wallet_route(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        who, org, bad = await person(request)
        if bad is not None:
            return bad
        out = await wallet.balance(pool_getter(), org)
        out["usd_per_100_inr"] = str(wallet.usd_for_paise(10_000))
        out["min_inr"], out["max_inr"] = wallet.MIN_TOPUP_INR, wallet.MAX_TOPUP_INR
        return _json(request, out)

    @server.custom_route("/v1/billing/orders", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def order_route(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        who, org, bad = await person(request)
        if bad is not None:
            return bad
        try:
            data = await request.json()
            paise = parse_amount_inr(data.get("amount_inr") if isinstance(data, dict) else None)
            order = await _create_razorpay_order(paise, f"kell-{org[:8]}")
            await wallet.record_order(pool_getter(), org_id=org, order_id=str(order["id"]), amount_paise=paise,
                                      created_by=who.actor_id)
        except wallet.WalletError as exc:
            return _err(request, exc.status, str(exc))
        except ValueError:
            return _err(request, 400, "the request body must be JSON")
        return _json(request, {"order_id": order["id"], "amount_paise": paise, "currency": "INR",
                               "key_id": os.environ["RAZORPAY_KEY_ID"],
                               "credit_usd": str(wallet.usd_for_paise(paise))}, 201)

    @server.custom_route("/v1/billing/webhook", methods=["POST"], include_in_schema=False)
    async def webhook_route(request: Request) -> Response:
        raw = await request.body()
        if not wallet.verify_signature(raw, request.headers.get("x-razorpay-signature"),
                                       os.environ.get("RAZORPAY_WEBHOOK_SECRET")):
            return Response(status_code=400)                       # not from Razorpay: credit nothing
        try:
            pay = payment_from_event(json.loads(raw))
        except ValueError:
            return Response(status_code=400)
        if pay is None:
            return Response(status_code=200)                       # an event we do not act on
        pool = pool_getter()
        order = await wallet.find_order(pool, pay["order_id"])
        if order is None or int(order["amount_paise"]) != pay["amount"]:
            log.warning("billing webhook: payment %s does not match a known order", pay["payment_id"])
            return Response(status_code=200)                       # acknowledged, not credited; reconcile by hand
        try:
            await wallet.credit_payment(pool, org_id=order["organization_id"], payment_ref=pay["payment_id"],
                                        amount_paise=pay["amount"], created_by="razorpay", order_id=pay["order_id"])
        except Exception:  # noqa: BLE001 - tell Razorpay to retry rather than lose a payment
            log.exception("billing webhook: crediting payment %s failed", pay["payment_id"])
            return Response(status_code=500)
        return Response(status_code=200)
