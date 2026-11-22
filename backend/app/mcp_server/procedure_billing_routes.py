"""The purchase endpoint for per-procedure one-run purchases (docs/procedure_billing.md §7.1).

  POST /v1/procedures/{procedure_id}/purchase  {"procedure_row_id": "<version row or submission id>"}
       -> {"checkout_url": "<Razorpay Payment Link>"}   (the Way page redirects there)
       -> 401 no sign-in, 404 no such Way, 400 free/already, 503 buying off, 502 gateway refused

The path id is the `parent_procedure_id` the Way page already has (a procedures row id or the
stable procedure_id); the body is the submission id the page has as `way.id`. Both are resolved
to the exact version row before anything is priced or charged.

The return page is derived from the purchase's submission (`/way/{id}/paid`), never from the
Referer: a purchase made from /route used to return the buyer to /route.
"""
from __future__ import annotations

from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.mcp_server.chat_routes import BY_ADDRESS, client_address, cors_headers as _cors, resolve_identity
from app.services import procedure_billing


def _cors_headers(request: Request) -> dict[str, str]:
    h = _cors(request)
    if "Access-Control-Allow-Origin" in h:
        h["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return h


def _json(request: Request, body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**_cors_headers(request), "Cache-Control": "no-store"})


def _err(request: Request, status: int, message: str) -> JSONResponse:
    return _json(request, {"error": {"message": message}}, status)


def register(server: Any, verifier: Any, pool_getter: Callable[[], Any], is_local: Callable[[Request], bool]) -> None:
    async def buyer(request: Request):
        """(who, None) or (None, an error response). A purchase needs a signed-in person."""
        if not BY_ADDRESS.allow(client_address(request)):
            return None, _err(request, 429, "too many payment attempts; try again in a moment")
        who, refusal = await resolve_identity(verifier, pool_getter(), request, is_local)
        if refusal is not None:
            return None, _err(request, refusal[1], refusal[0])
        if who is None or not getattr(who, "actor_id", None):
            return None, _err(request, 401, "sign in to kell to buy this Way")
        return who, None

    @server.custom_route("/v1/procedures/{procedure_id}/purchase", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def purchase(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers(request))
        who, bad = await buyer(request)
        if bad is not None:
            return bad
        try:
            body = await request.json()
        except ValueError:
            return _err(request, 400, "the request body must be JSON")
        if not isinstance(body, dict):
            return _err(request, 400, "the request body must be a JSON object")
        ref = body.get("procedure_row_id") or request.path_params.get("procedure_id")
        if not isinstance(ref, str) or not ref.strip():
            return _err(request, 400, "give the procedure version to buy")
        try:
            # No callback from the request: create_purchase sends the buyer to the Way's own paid page
            # (derived from the submission), never to whatever page the browser came from.
            result = await procedure_billing.create_purchase(
                pool_getter(), procedure_row_id=ref.strip(),
                buyer_user_id=str(who.actor_id))
        except procedure_billing.ProcedureBillingError as exc:
            return _err(request, exc.status_code, str(exc))
        return _json(request, result)
