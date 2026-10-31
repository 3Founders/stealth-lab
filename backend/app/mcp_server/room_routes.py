"""
HTTP side of rooms (``/v1/rooms/...``): what the kell website calls so rooms and chats follow a person across devices. Both this
and the MCP room tools (later) share one store, app/services/rooms.py; the tables are in db/157_rooms.sql.

Who may call: a signed-in person, proven by their Supabase WEBSITE-SESSION token, exactly as for /v1/delegate (``Gate`` is reused
from delegate_routes: it refuses MCP OAuth tokens and, with no identity provider configured, accepts only loopback as the single
local user). The subject scopes every row: a room you are not in is "no such room".

CORS: the same explicit origin allow-list as the delegate and chat routes (``allowed_origins``), never ``*``, never with
credentials. These routes also use PUT and PATCH, so they answer for those methods.

Live updates are Server-Sent Events that poll the room every two seconds for about 50 s and then end with a cursor, so the
client reconnects with ``?after=<cursor>`` (the same shape as /v1/delegate/events). A browser's EventSource cannot send an
Authorization header; the site reads the stream with fetch(). Honest limit: one polling loop per open stream per worker.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.mcp_server.delegate_routes import Gate, allowed_origins
from app.services import rooms as rm

STREAM_SECONDS = 50
POLL_SECONDS = 2.0


def cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    if origin and origin in allowed_origins():
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "authorization, content-type",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS", "Access-Control-Max-Age": "600"}
    return {"Vary": "Origin"}


def _json(request: Request, body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**cors_headers(request), "Cache-Control": "no-store"})


def _error(request: Request, message: str, status: int) -> JSONResponse:
    return _json(request, {"error": message}, status)


def _int(value: Optional[str], default: int = 0) -> int:
    try:
        return max(0, int(value)) if value not in (None, "") else default
    except ValueError as exc:
        raise rm.RoomError("that must be a whole number") from exc


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any]) -> None:
    """Attach the routes to the MCP server's custom-route table."""

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            raise rm.RoomError("the request body must be JSON")
        if not isinstance(data, dict):
            raise rm.RoomError("the request body must be a JSON object")
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
            except rm.RoomError as exc:
                return _error(request, str(exc), exc.status)
        wrapped.__name__ = fn.__name__
        return wrapped

    @server.custom_route("/v1/rooms", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def rooms(request: Request, pool: Any, subject: str) -> Response:
        if request.method == "GET":
            return _json(request, {"rooms": await rm.list_rooms(pool, subject)})
        data = await body_of(request)
        return _json(request, await rm.create_room(pool, subject, str(data.get("title", "")), data.get("id")), 201)

    @server.custom_route("/v1/rooms/join", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def join(request: Request, pool: Any, subject: str) -> Response:
        data = await body_of(request)
        return _json(request, await rm.redeem_invite(pool, subject, str(data.get("code", ""))))

    @server.custom_route("/v1/rooms/{room_id}", methods=["PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    @handler
    async def room_one(request: Request, pool: Any, subject: str) -> Response:
        rid = request.path_params["room_id"]
        if request.method == "DELETE":
            return _json(request, await rm.delete_room(pool, subject, rid))
        data = await body_of(request)
        return _json(request, await rm.rename_room(pool, subject, rid, str(data.get("title", ""))))

    @server.custom_route("/v1/rooms/{room_id}/members", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def members(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, {"members": await rm.list_members(pool, subject, request.path_params["room_id"])})

    @server.custom_route("/v1/rooms/{room_id}/members/{who}", methods=["PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    @handler
    async def member_one(request: Request, pool: Any, subject: str) -> Response:
        if request.method == "PATCH":
            data = await body_of(request)
            return _json(request, await rm.set_member_role(pool, subject, request.path_params["room_id"], request.path_params["who"], str(data.get("role", ""))))
        return _json(request, await rm.remove_member(pool, subject, request.path_params["room_id"], request.path_params["who"]))

    @server.custom_route("/v1/rooms/{room_id}/invites", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def invites(request: Request, pool: Any, subject: str) -> Response:
        rid = request.path_params["room_id"]
        if request.method == "GET":
            return _json(request, {"invites": await rm.list_invites(pool, subject, rid)})
        data = await body_of(request)
        return _json(request, await rm.create_invite(pool, subject, rid, data.get("days", 7), data.get("max_uses", 20)), 201)

    @server.custom_route("/v1/rooms/{room_id}/invites/{invite_id}", methods=["DELETE", "OPTIONS"], include_in_schema=False)
    @handler
    async def invite_one(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await rm.revoke_invite(pool, subject, request.path_params["room_id"], request.path_params["invite_id"]))

    @server.custom_route("/v1/rooms/{room_id}/events", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def events(request: Request, pool: Any, subject: str) -> Response:
        rid = request.path_params["room_id"]
        if request.method == "GET":
            return _json(request, await rm.list_events(pool, subject, rid, _int(request.query_params.get("after")),
                                                       _int(request.query_params.get("limit"), 200)))
        data = await body_of(request)
        out = await rm.append_events(pool, subject, rid, data.get("events"))
        return _json(request, out, 201 if out["accepted"] else 200)

    @server.custom_route("/v1/rooms/{room_id}/read", methods=["PUT", "OPTIONS"], include_in_schema=False)
    @handler
    async def read(request: Request, pool: Any, subject: str) -> Response:
        data = await body_of(request)
        return _json(request, await rm.mark_read(pool, subject, request.path_params["room_id"], data.get("seq", -1)))

    @server.custom_route("/v1/rooms/{room_id}/stream", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def stream(request: Request, pool: Any, subject: str) -> Response:
        rid = request.path_params["room_id"]
        after = _int(request.query_params.get("after"))
        await rm.list_events(pool, subject, rid, after, 1)          # membership is checked before streaming starts
        return StreamingResponse(_stream(pool, subject, rid, after), media_type="text/event-stream",
                                 headers={**cors_headers(request), "Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


async def _stream(pool: Any, subject: str, room_id: str, after: int) -> AsyncIterator[bytes]:
    """Events after `after`, then new ones, for about STREAM_SECONDS. Each ``event`` carries one stored event with its seq, so a
    client applies it by id and a replay after a reconnect never duplicates a message. Ends with the cursor to resume from."""
    yield b": kell rooms stream\n\n"
    start = asyncio.get_event_loop().time()
    cursor = after
    while asyncio.get_event_loop().time() - start < STREAM_SECONDS:
        try:
            page = await rm.list_events(pool, subject, room_id, cursor, rm.MAX_PAGE)
        except rm.RoomError as exc:                       # removed from the room, or it was deleted
            yield _sse("error", {"error": str(exc), "status": exc.status})
            return
        for e in page["events"]:
            yield _sse("event", e)
        cursor = page["next"]
        if not page["more"]:
            yield b": keepalive\n\n"
            await asyncio.sleep(POLL_SECONDS)
    yield _sse("end", {"cursor": cursor})
