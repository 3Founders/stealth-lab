"""HTTP side of desktop sessions in the rooms (``/v1/desktop/...``, app/services/desktop_remote.py, docs/desktop_remote.md).

The desktop app calls heartbeat / next / asks/{id}/update with the person's website session (the Rooms sign-in it already
holds, desktop/lib/websession.ts). The website calls the rest: the "My desktop sessions" panel, grants, approvals.
The same Gate as the rooms routes: a website-session token, or loopback as the single local user without an identity
provider. Registered from room_routes.register.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.mcp_server.room_routes import _error, _json, cors_headers
from app.services import desktop_remote as dr
from app.services import rooms as rm

NEXT_WAIT_SECONDS = 25.0
NEXT_POLL_SECONDS = 1.5


def register(server: Any, gate: Any, pool_getter: Callable[[], Any]) -> None:

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            raise dr.DesktopError("the request body must be JSON")
        if not isinstance(data, dict):
            raise dr.DesktopError("the request body must be a JSON object")
        return data

    def handler(fn: Callable[..., Any]):
        async def wrapped(request: Request) -> Response:
            if request.method == "OPTIONS":
                return Response(status_code=204, headers=cors_headers(request))
            subject, refusal = await gate.subject(request)
            if refusal is not None:
                return _error(request, refusal[0], refusal[1])
            pool = pool_getter()
            if pool is None:
                return _error(request, "the server is starting; retry shortly", 503)
            try:
                return await fn(request, pool, subject)
            except (dr.DesktopError, rm.RoomError) as exc:
                return _error(request, str(exc), exc.status)
        wrapped.__name__ = fn.__name__
        return wrapped

    # ------------------------------------------------------------------------------------------------ the desktop app

    @server.custom_route("/v1/desktop/heartbeat", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def heartbeat(request: Request, pool: Any, subject: str) -> Response:
        data = await body_of(request)
        return _json(request, await dr.heartbeat(pool, subject, str(data.get("device_id") or ""), str(data.get("name") or ""),
                                                 data.get("sessions")))

    @server.custom_route("/v1/desktop/next", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def next_ask(request: Request, pool: Any, subject: str) -> Response:
        """Long poll: the next ask for this device, waiting up to NEXT_WAIT_SECONDS. Each call also counts as a heartbeat
        (without changing the session list), so a desktop that is polling is online."""
        device = str(request.query_params.get("device_id") or "")
        await dr.heartbeat(pool, subject, device, str(request.query_params.get("name") or ""), None)
        loop = asyncio.get_event_loop()
        until = loop.time() + NEXT_WAIT_SECONDS
        while True:
            ask = await dr.next_ask(pool, subject, device)
            if ask is not None or loop.time() >= until:
                return _json(request, {"ask": ask})
            await asyncio.sleep(NEXT_POLL_SECONDS)

    @server.custom_route("/v1/desktop/asks/{ask_id}/update", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def update(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await dr.update_ask(pool, subject, request.path_params["ask_id"], await body_of(request)))

    # ------------------------------------------------------------------------------------------------------ the website

    @server.custom_route("/v1/desktop/sessions", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def sessions(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, {"mine": await dr.sessions_of(pool, [subject]), "devices": await dr.my_devices(pool, subject),
                               "shared": [s for s in await dr.reachable(pool, subject) if s["owner"] != subject],
                               "pending": await dr.pending_for_owner(pool, subject)})

    @server.custom_route("/v1/desktop/asks", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def asks(request: Request, pool: Any, subject: str) -> Response:
        if request.method == "GET":
            return _json(request, {"asks": await dr.recent_asks(pool, subject, request.query_params.get("session_id") or None)})
        data = await body_of(request)
        ask = await dr.create_ask(pool, subject, str(data.get("device_id") or ""), str(data.get("session_id") or ""),
                                  str(data.get("text") or ""), data.get("room_id") or None)
        return _json(request, ask, 201)

    @server.custom_route("/v1/desktop/asks/{ask_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def ask_one(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await dr.get_ask(pool, subject, request.path_params["ask_id"]))

    @server.custom_route("/v1/desktop/asks/{ask_id}/approve", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def approve(request: Request, pool: Any, subject: str) -> Response:
        data = await body_of(request)
        return _json(request, await dr.approve(pool, subject, request.path_params["ask_id"], bool(data.get("yes"))))

    @server.custom_route("/v1/desktop/asks/{ask_id}/permission", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def permission(request: Request, pool: Any, subject: str) -> Response:
        data = await body_of(request)
        return _json(request, await dr.decide_permission(pool, subject, request.path_params["ask_id"], str(data.get("decision") or "")))

    @server.custom_route("/v1/desktop/asks/{ask_id}/cancel", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def cancel(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await dr.cancel(pool, subject, request.path_params["ask_id"]))

    @server.custom_route("/v1/desktop/grants", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
    @handler
    async def grants(request: Request, pool: Any, subject: str) -> Response:
        if request.method == "GET":
            return _json(request, await dr.grants(pool, subject))
        if request.method == "DELETE":
            return _json(request, await dr.remove_grant(pool, subject, str(request.query_params.get("grantee") or "")))
        data = await body_of(request)
        return _json(request, await dr.set_grant(pool, subject, str(data.get("username") or ""), str(data.get("level") or "")))
