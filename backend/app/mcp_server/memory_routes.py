"""HTTP side of kell's memory (/v1/memory..., services/memory.py, db/173_memory.sql): what the Settings > Memory page, the Notes panel and the
Knowledge page call. Same gate as rooms: the person's own website-session token, never a connector's token (connectors use the MCP memory
tools, which honour the person's "let my connected agents use my memory" choice).

Dreaming: ``POST /v1/memory/dream`` runs one tidy-up with the caller's own session (so the cost and caps are theirs). Without ``force`` it
does nothing unless a dream is due (memory on, the last dream long enough ago, something new said). The page asks when it opens.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.mcp_server.delegate_routes import Gate
from app.mcp_server.room_routes import _error, _json, cors_headers
from app.services import memory as mem
from app.services.rooms import RoomError, _Limiter

log = logging.getLogger(__name__)

ModelCall = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], Awaitable[dict[str, Any]]]
# A dream calls the model: a few per person per hour, in memory (one worker).
DREAM_LIMITER = _Limiter(4, 3600.0)
WRITE_LIMITER = _Limiter(120, 60.0)


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any], *, model_call: ModelCall) -> None:
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

    @server.custom_route("/v1/memory", methods=["GET", "DELETE", "OPTIONS"], include_in_schema=False)
    async def memory_root(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        try:
            if request.method == "DELETE":
                await mem.delete_all(pool, subject)
                return _json(request, {"deleted": True, "settings": await mem.settings(pool, subject)})
            return _json(request, {"settings": await mem.settings(pool, subject), "files": await mem.list_files(pool, subject),
                                   "dreams": await mem.dreams(pool, subject)})
        except RoomError as exc:
            return _error(request, str(exc), exc.status)

    @server.custom_route("/v1/memory/settings", methods=["PUT", "OPTIONS"], include_in_schema=False)
    async def memory_settings(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        data = await body_of(request)
        en = data.get("enabled") if isinstance(data.get("enabled"), bool) else None
        sh = data.get("share_agents") if isinstance(data.get("share_agents"), bool) else None
        return _json(request, await mem.set_settings(pool, subject, en, sh))

    @server.custom_route("/v1/memory/file", methods=["GET", "PUT", "DELETE", "OPTIONS"], include_in_schema=False)
    async def memory_file(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        try:
            if request.method == "GET":
                f = await mem.read_file(pool, subject, request.query_params.get("path", ""))
                return _json(request, f) if f else _error(request, "no such file", 404)
            if request.method == "DELETE":
                return _json(request, {"deleted": await mem.delete_file(pool, subject, request.query_params.get("path", ""))})
            if not WRITE_LIMITER.allow(subject):
                return _error(request, "too many changes; wait a minute", 429)
            data = await body_of(request)
            ev = data.get("expected_version")
            return _json(request, await mem.write_file(pool, subject, str(data.get("path") or ""), str(data.get("body") or ""), by="you",
                                                       reason=str(data.get("reason") or "")[:300],
                                                       expected_version=ev if isinstance(ev, int) else None))
        except RoomError as exc:
            return _error(request, str(exc), exc.status)

    @server.custom_route("/v1/memory/versions", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def memory_versions(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        try:
            return _json(request, {"versions": await mem.versions(pool, subject, request.query_params.get("path", ""))})
        except RoomError as exc:
            return _error(request, str(exc), exc.status)

    @server.custom_route("/v1/memory/revert", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def memory_revert(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        data = await body_of(request)
        try:
            return _json(request, await mem.revert(pool, subject, str(data.get("path") or ""), int(data.get("version") or 0)))
        except (RoomError, ValueError) as exc:
            return _error(request, str(exc), getattr(exc, "status", 400))

    @server.custom_route("/v1/memory/search", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def memory_search(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        return _json(request, {"hits": await mem.search(pool, subject, request.query_params.get("q", ""), 5, skip_index=False)})

    @server.custom_route("/v1/memory/dream", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def memory_dream(request: Request) -> Response:
        pool, subject, refusal = await authed(request)
        if refusal is not None:
            return refusal
        data = await body_of(request)
        force = data.get("force") is True
        try:
            if not (await mem.settings(pool, subject))["enabled"]:
                return _json(request, {"ran": False, "why": "memory is off"})
            if not force and not await mem.due(pool, subject):
                return _json(request, {"ran": False, "why": "not due yet"})
            if not DREAM_LIMITER.allow(subject):
                return _error(request, "too many dreams this hour", 429)
            messages = await mem.dream_prompt(pool, subject)
            res = await model_call(request.headers.get("authorization", ""), messages, [])
            text = str((((res.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
            changed = await mem.apply_dream(pool, subject, text)   # no file blocks ("NOTHING") still logs the dream and moves the clock
            return _json(request, {"ran": True, "changed": changed, "dreams": await mem.dreams(pool, subject, 3)})
        except RoomError as exc:
            return _error(request, str(exc), exc.status)
        except RuntimeError as exc:                         # the model endpoint's own refusal (cap, policy, sign-in)
            log.info("memory: the dream could not run: %s", exc)
            return _error(request, f"kell could not tidy memory now: {str(exc).strip()[:200]}", 502)
