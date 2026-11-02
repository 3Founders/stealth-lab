"""/v1/rooms/{room_id}/docs (db/174_room_docs.sql, services/room_docs.py): the shared documents of a room. Needs the website-session token and
membership of the room, like every room route. A page asks for a document with ``?since=<version>`` every second or two and gets the cheap
"unchanged" answer until someone saves."""
from __future__ import annotations

from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.mcp_server.delegate_routes import Gate
from app.mcp_server.room_routes import _error, _json, cors_headers
from app.services import room_docs as rd
from app.services.rooms import RoomError, _Limiter

SAVES = _Limiter(240, 60.0)         # saves per person per minute (autosave while typing)


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any]) -> None:
    async def authed(request: Request):
        if request.method == "OPTIONS":
            return None, None, Response(status_code=204, headers=cors_headers(request))
        subject, refusal = await gate.subject(request)
        if refusal is not None:
            return None, None, _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return None, None, _error(request, "the server is starting; retry shortly", 503)
        return pool, subject, None

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    @server.custom_route("/v1/rooms/{room_id}/docs", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    async def docs_collection(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        rid = request.path_params["room_id"]
        try:
            if request.method == "GET":
                return _json(request, {"docs": await rd.list_docs(pool, subject, rid)})
            data = await body_of(request)
            return _json(request, await rd.create_doc(pool, subject, rid, str(data.get("title") or ""), str(data.get("kind") or "document"),
                                                      str(data.get("body") or "")), 201)
        except RoomError as exc:
            return _error(request, str(exc), exc.status)

    @server.custom_route("/v1/rooms/{room_id}/docs/{doc_id}", methods=["GET", "PUT", "DELETE", "OPTIONS"], include_in_schema=False)
    async def doc_item(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        rid, did = request.path_params["room_id"], request.path_params["doc_id"]
        try:
            if request.method == "GET":
                since = request.query_params.get("since")
                return _json(request, await rd.get_doc(pool, subject, rid, did, int(since) if since and since.isdigit() else None))
            if request.method == "DELETE":
                return _json(request, {"deleted": await rd.delete_doc(pool, subject, rid, did)})
            if not SAVES.allow(subject):
                return _error(request, "saving too fast; wait a moment", 429)
            data = await body_of(request)
            base = data.get("base_version")
            if not isinstance(base, int):
                return _error(request, "send base_version, the version you edited", 400)
            return _json(request, await rd.save_doc(pool, subject, rid, did, str(data.get("body") or ""), base))
        except RoomError as exc:
            return _error(request, str(exc), exc.status)

    @server.custom_route("/v1/rooms/{room_id}/docs/{doc_id}/history", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def doc_history(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        try:
            return _json(request, {"versions": await rd.history(pool, subject, request.path_params["room_id"], request.path_params["doc_id"])})
        except RoomError as exc:
            return _error(request, str(exc), exc.status)

    @server.custom_route("/v1/rooms/{room_id}/docs/{doc_id}/restore", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def doc_restore(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        data = await body_of(request)
        try:
            return _json(request, await rd.restore(pool, subject, request.path_params["room_id"], request.path_params["doc_id"], int(data.get("version") or 0)))
        except (RoomError, ValueError) as exc:
            return _error(request, str(exc), getattr(exc, "status", 400))
