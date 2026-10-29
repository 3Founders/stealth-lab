"""
HTTP side of the profile photo (``/v1/me/photo``): what the kell website calls so a person's photo follows them across devices.
The table is db/159_profile_photos.sql (in the Neon database, like rooms).

Who may call: a signed-in person, proven by their Supabase WEBSITE-SESSION token, exactly as for /v1/rooms (``Gate`` is reused from
delegate_routes). The subject scopes the row: there is no way to name someone else's photo, so there is nothing to enumerate.
This is the caller's OWN photo only. Showing a photo to other people (for example in a shared room) is a separate decision.

The server does not decode or re-encode images. It checks the size (at most 64 KB; the site sends about 10 KB) and that the first
bytes are a JPEG, PNG or WebP signature, and serves the bytes back with that exact type and ``nosniff``. Honest limit: a file with
a valid signature can still be a broken picture; a browser just shows nothing for it.

CORS: the same explicit origin allow-list as the other website routes, never ``*``, never with credentials.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.mcp_server.delegate_routes import Gate
from app.mcp_server.room_routes import cors_headers

MAX_BYTES = 64 * 1024


def sniff_image_type(data: bytes) -> Optional[str]:
    """The image type named by the leading bytes, or None. Only the three types the site produces and accepts."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _error(request: Request, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers={**cors_headers(request), "Cache-Control": "no-store"})


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any]) -> None:
    """Attach the route to the MCP server's custom-route table."""

    @server.custom_route("/v1/me/photo", methods=["GET", "PUT", "DELETE", "OPTIONS"], include_in_schema=False)
    async def photo(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        subject, refusal = await gate.subject(request)
        if refusal is not None:
            return _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return _error(request, "the server is starting; retry shortly", 503)
        headers = {**cors_headers(request), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if request.method == "GET":
            row = await pool.fetchrow("SELECT content_type, data, updated_at FROM profile_photos WHERE sub = $1", subject)
            if row is None:
                return _error(request, "no photo", 404)
            return Response(bytes(row["data"]), media_type=row["content_type"],
                            headers={**headers, "X-Photo-Updated": row["updated_at"].isoformat()})
        if request.method == "DELETE":
            await pool.execute("DELETE FROM profile_photos WHERE sub = $1", subject)
            return Response(status_code=204, headers=headers)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BYTES:
            return _error(request, "that picture is too large", 413)
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_BYTES:
                return _error(request, "that picture is too large", 413)
        kind = sniff_image_type(data)
        if kind is None:
            return _error(request, "the picture must be a JPEG, PNG or WebP", 415)
        await pool.execute(
            "INSERT INTO profile_photos (sub, content_type, data) VALUES ($1, $2, $3) "
            "ON CONFLICT (sub) DO UPDATE SET content_type = EXCLUDED.content_type, data = EXCLUDED.data, updated_at = now()",
            subject, kind, data)
        return Response(status_code=204, headers=headers)
