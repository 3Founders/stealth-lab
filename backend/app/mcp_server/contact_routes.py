"""
POST /v1/contact on the MCP host: the website's Contact form ("Talk to a founder").

The form's handler already exists as a FastAPI router (app/api/contact.py, served by app.main). Production runs only this MCP
server, which is the host the website already talks to (www.kell.org.in -> mcp.kell.org.in), so the same pipeline is exposed
here through the server's custom routes. Nothing about the pipeline changes (app/services/contact.py): a honeypot, rate limits,
Cloudflare Turnstile checked on the server, the row stored in ``contact_requests``, then a best-effort email to the team address
(CONTACT_NOTIFY_TO) through Resend. Anonymous by design, so there is no bearer token; abuse control is the pipeline's.

CORS reuses the delegate routes' explicit allow-list (never ``*``, no cookies). Every refusal comes back as ``{"detail": "..."}``
because that is what the form reads and shows the visitor. Honest limit, inherited from the service: the per-address rate limit
keys on the socket address, which behind the platform proxy is the proxy, so only the overall limit and Turnstile really bite.
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.contact import ContactIn
from app.mcp_server.delegate_routes import cors_headers
from app.services import contact


def register(server: Any, pool_getter: Callable[[], Any]) -> None:
    """Attach POST /v1/contact (and its CORS preflight) to the MCP server's custom-route table."""

    def reply(request: Request, body: dict[str, Any], status: int) -> JSONResponse:
        return JSONResponse(body, status_code=status, headers={**cors_headers(request), "Cache-Control": "no-store"})

    @server.custom_route("/v1/contact", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def contact_submit(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        pool = pool_getter()
        if pool is None:
            return reply(request, {"detail": "The contact form isn't available right now. Please email us instead."}, 503)
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001 -- not JSON
            return reply(request, {"detail": "We couldn't read that message. Please try again."}, 400)
        try:
            body = ContactIn.model_validate(data)
        except ValidationError:
            return reply(request, {"detail": "Please check your name, email and message and try again."}, 422)
        ip = request.client.host if request.client else None
        try:
            result = await contact.submit(
                pool,
                name=body.name, email=body.email, company=body.company, topic=body.topic, message=body.message,
                source_path=body.source_path, honeypot=body.website, turnstile_token=body.turnstile_token, remote_ip=ip,
            )
        except contact.ContactRefused as exc:
            return reply(request, {"detail": exc.reason}, exc.status)
        return reply(request, result, 202)
