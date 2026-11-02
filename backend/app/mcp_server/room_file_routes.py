"""POST/GET /v1/rooms/{room_id}/files (db/172_room_files.sql, services/room_files.py).

Upload is the raw file as the request body with ``?name=``; the type comes from the bytes, not from the header. Download returns the
bytes with download-only headers except for pictures, audio, video and PDF. Both need the website-session token and room membership.
"""
from __future__ import annotations

from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.mcp_server.delegate_routes import Gate
from app.mcp_server.room_routes import _error, _json, cors_headers
from app.services import room_files as rf
from app.services.rooms import RoomError


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any]) -> None:
    async def authed(request: Request):
        subject, refusal = await gate.subject(request)
        if refusal is not None:
            return None, None, _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return None, None, _error(request, "the server is starting; retry shortly", 503)
        return pool, subject, None

    @server.custom_route("/v1/rooms/{room_id}/files", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def upload(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > rf.MAX_BYTES + 1024:
            return _error(request, f"a file is at most {rf.MAX_BYTES // (1024 * 1024)} MB here", 413)
        data = await request.body()
        try:
            out = await rf.upload(pool, subject, request.path_params["room_id"], request.query_params.get("name", "file"), data)
        except RoomError as exc:
            return _error(request, str(exc), exc.status)
        return _json(request, out, 201)

    @server.custom_route("/v1/rooms/{room_id}/files/{file_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def download(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        try:
            meta, data = await rf.fetch(pool, subject, request.path_params["room_id"], request.path_params["file_id"])
        except RoomError as exc:
            return _error(request, str(exc), exc.status)
        return Response(content=data, headers={**cors_headers(request), **rf.download_headers(meta)})
