"""The organization admin API on the hosted server.

The admin pages on the website (`/org`, members, policy, activity, provider keys) call `/v1/orgs/...` and `/v1/join`. Those routes were written for the
full API app (`app/api/org_admin.py`, mounted in `main.py`), which is not what the hosted MCP service runs, so on the hosted origin every one of them answered
404. This gate serves them from the hosted server: the same routers, unchanged, in a small FastAPI app that sits behind the hosted server's own sign-in.

What it does for a request under `/v1/orgs` or `/v1/join`:
  * answers the CORS preflight the way the other website routes do;
  * signs the person in with the hosted server's gate (the website-session token), and refuses with the gate's own sentence and status if that fails;
  * tells the router's own identity resolver who the person is (the same Actor, with the issuer, that the full app's middleware would set), so roles still come from
    `org_memberships` in the database at that moment, and a non-member still gets 404 and the wrong role 403, exactly as in the full app;
  * adds the CORS headers to whatever the router answers.

Off unless ORG_ADMIN_ROUTES=on: a route that can set a spending policy and a kill switch is switched on on purpose, with the site flag
(NEXT_PUBLIC_ORG_ADMIN=on) after it. Everything else passes to the wrapped app untouched.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.mcp_server.room_routes import cors_headers

PATH = re.compile(r"^/v1/(orgs(/|$)|join(/|$))")


def enabled() -> bool:
    return os.environ.get("ORG_ADMIN_ROUTES", "").strip().lower() in ("on", "1", "true", "yes")


def build_sub_app() -> Any:
    """The org-admin routers on their own FastAPI app (imported late: the full API package is heavy and only needed when the routes are on)."""
    from fastapi import FastAPI

    from app.api import org_admin

    sub = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sub.include_router(org_admin.mine_router)
    sub.include_router(org_admin.router)
    sub.include_router(org_admin.join_router)
    return sub


class OrgAdminGate:
    """Wraps the hosted app: `/v1/orgs...` and `/v1/join...` are served here when switched on; everything else goes through untouched."""

    def __init__(self, app: Any, gate: Any, pool_getter: Callable[[], Any], sub_app: Any = None, is_on: Callable[[], bool] = enabled) -> None:
        self.app, self.gate, self.pool_getter, self._sub, self.is_on = app, gate, pool_getter, sub_app, is_on

    def __getattr__(self, name: str) -> Any:                 # the wrapped app's attributes (state, router, lifespan) stay reachable
        return getattr(self.app, name)

    def _sub_app(self) -> Any:
        if self._sub is None:
            self._sub = build_sub_app()
        return self._sub

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not PATH.match(scope.get("path", "")) or not self.is_on():
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.method == "OPTIONS":
            await Response(status_code=204, headers=cors_headers(request))(scope, receive, send)
            return
        subject, refusal = await self.gate.subject(request)
        if refusal is not None:
            await JSONResponse({"error": refusal[0]}, status_code=refusal[1], headers=cors_headers(request))(scope, receive, send)
            return
        pool = self.pool_getter()
        if pool is None:
            await JSONResponse({"error": "the server is starting; retry shortly"}, status_code=503, headers=cors_headers(request))(scope, receive, send)
            return
        from app.services.authn import Actor, reset_current_actor, set_current_actor

        extra = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in cors_headers(request).items()]

        async def send_with_cors(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message = {**message, "headers": [*message.get("headers", []), *[(k, v) for k, v in extra if k not in have]]}
            await send(message)

        sub = self._sub_app()
        sub.state.pool = pool                                  # what the routers read as request.app.state.pool
        # A person is a users row keyed by (issuer, subject): the issuer the hosted server verified their token against, so the routers find the same
        # person (and their memberships) the rest of the platform does, and never mint a second, empty account for the same sign-in.
        cfg = getattr(getattr(self.gate, "_verifier", None), "_oidc_config", None)
        token = set_current_actor(Actor(subject=subject, issuer=getattr(cfg, "issuer", None)))
        try:
            await sub(scope, receive, send_with_cors)
        finally:
            reset_current_actor(token)
