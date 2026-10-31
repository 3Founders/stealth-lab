"""
HTTP side of the Delegator relay: what the kell website calls (``/v1/delegate/...``). The agent side is two MCP tools,
``delegate_inbox`` and ``delegate_result`` (server.py). Both sides share one store (app/services/delegate.py).

Who may call: a signed-in person, proven by their Supabase WEBSITE-SESSION token (the one the site already holds). The MCP
server's own verifier takes only OAuth-flow tokens (authn.supabase_token_allowed splits the two so a leaked MCP token cannot
drive the website API, and the reverse), so these routes validate the token themselves with ``surface="api"``. Both kinds of
token carry the same Supabase subject, which is what scopes every row to its owner. With no identity provider configured
(a bare local checkout) only a loopback caller is accepted, as the single local user.

The browser calls this from another origin (www.kell.org.in -> the MCP host), so the routes answer CORS preflights for an
explicit allow-list (DELEGATE_ALLOWED_ORIGINS, comma-separated), never ``*``, and never with credentials/cookies: the bearer
token is the only credential.

Live updates are Server-Sent Events that poll the store every two seconds for at most ~50 s, then end so the client
reconnects with its last cursor. A browser's EventSource cannot send an Authorization header; the site must use fetch() and
read the stream. Honest limit: one polling loop per open stream per worker; fine for a person's own rooms, not for fan-out.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.services import delegate as dg

DEFAULT_ORIGINS = "https://www.kell.org.in,https://kell.org.in,http://localhost:3000,http://127.0.0.1:3000,http://localhost:3100,http://127.0.0.1:3100"
STREAM_SECONDS = 50
POLL_SECONDS = 2.0


def allowed_origins() -> set[str]:
    return {o.strip() for o in os.environ.get("DELEGATE_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()}


def cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    if origin and origin in allowed_origins():
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "authorization, content-type",
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS", "Access-Control-Max-Age": "600"}
    return {"Vary": "Origin"}


def _json(request: Request, body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**cors_headers(request), "Cache-Control": "no-store"})


def _error(request: Request, message: str, status: int) -> JSONResponse:
    return _json(request, {"error": message}, status)


def _bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") and h[7:].strip() else None


class Gate:
    """Resolves the caller of a website request to a Supabase subject, or says why not."""

    def __init__(self, verifier: Any, pool_getter: Callable[[], Any], is_local: Callable[[Request], bool]) -> None:
        self._verifier = verifier
        self._pool = pool_getter
        self._is_local = is_local

    async def subject(self, request: Request) -> tuple[Optional[str], Optional[tuple[str, int]]]:
        cfg = getattr(self._verifier, "_oidc_config", None)
        if cfg is None:
            # no identity provider: a bare local checkout. Loopback only, one local user.
            if self._is_local(request):
                return "local-user", None
            return None, ("sign-in is not configured on this server", 503)
        token = _bearer(request)
        if token is None:
            return None, ("sign in first", 401)
        from app.services import auth_context as ac
        from app.services.authn import IdentityInactive, TokenRejected, supabase_token_allowed, validate_token_async
        from app.config import settings

        try:
            actor = await validate_token_async(token, config=cfg, jwks_provider=self._verifier._jwks_provider)
        except TokenRejected:
            return None, ("your session is not valid; sign in again", 401)
        why = supabase_token_allowed(actor, cfg, surface="api", split=bool(getattr(settings, "split_supabase_tokens", True)))
        if why:
            return None, (why, 401)
        pool = self._pool()
        if pool is not None:
            try:
                await ac.resolve_auth_context(pool, actor)
            except IdentityInactive:
                return None, ("this account is inactive", 403)
            except Exception:  # noqa: BLE001 -- cannot establish identity state: no access
                return None, ("could not verify your account right now", 503)
        return actor.subject, None


def register(server: Any, gate: Gate, pool_getter: Callable[[], Any]) -> None:
    """Attach the routes to the MCP server's custom-route table."""

    def pool_or_503(request: Request):
        pool = pool_getter()
        return pool, (None if pool is not None else _error(request, "the server is starting; retry shortly", 503))

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            raise dg.DelegateError("the request body must be JSON")
        if not isinstance(data, dict):
            raise dg.DelegateError("the request body must be a JSON object")
        return data

    def handler(fn: Callable[..., Any]):
        async def wrapped(request: Request) -> Response:
            if request.method == "OPTIONS":
                return Response(status_code=204, headers=cors_headers(request))
            subject, refusal = await gate.subject(request)
            if refusal is not None:
                return _error(request, refusal[0], refusal[1])
            pool, not_ready = pool_or_503(request)
            if not_ready is not None:
                return not_ready
            try:
                return await fn(request, pool, subject)
            except dg.DelegateError as exc:
                return _error(request, str(exc), exc.status)
        wrapped.__name__ = fn.__name__
        return wrapped

    @server.custom_route("/v1/delegate/agents", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def agents(request: Request, pool: Any, subject: str) -> Response:
        if request.method == "GET":
            return _json(request, {"agents": await dg.list_agents(pool, subject)})
        data = await body_of(request)
        return _json(request, await dg.create_agent(pool, subject, str(data.get("name", "")), str(data.get("kind", "agent"))), 201)

    @server.custom_route("/v1/delegate/agents/{agent_id}", methods=["DELETE", "OPTIONS"], include_in_schema=False)
    @handler
    async def agent_one(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await dg.revoke_agent(pool, subject, request.path_params["agent_id"]))

    @server.custom_route("/v1/delegate/messages", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def messages(request: Request, pool: Any, subject: str) -> Response:
        data = await body_of(request)
        out = await dg.post_message(pool, subject, str(data.get("text", "")), data.get("room"),
                                    needs_approval=bool(data.get("needs_approval", False)))
        return _json(request, out, 201 if out["tasks"] else 200)

    @server.custom_route("/v1/delegate/tasks", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def tasks(request: Request, pool: Any, subject: str) -> Response:
        after = _parse_cursor(request.query_params.get("after"))
        return _json(request, {"tasks": await dg.list_tasks(pool, subject, request.query_params.get("room"), after)})

    @server.custom_route("/v1/delegate/tasks/{task_id}/approve", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def approve(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await dg.decide_task(pool, subject, request.path_params["task_id"], True))

    @server.custom_route("/v1/delegate/tasks/{task_id}/cancel", methods=["POST", "OPTIONS"], include_in_schema=False)
    @handler
    async def cancel(request: Request, pool: Any, subject: str) -> Response:
        return _json(request, await dg.decide_task(pool, subject, request.path_params["task_id"], False))

    @server.custom_route("/v1/delegate/events", methods=["GET", "OPTIONS"], include_in_schema=False)
    @handler
    async def events(request: Request, pool: Any, subject: str) -> Response:
        room = request.query_params.get("room")
        cursor = _parse_cursor(request.query_params.get("after"))
        dg.normalize_room(room) if room else None          # validate before streaming
        return StreamingResponse(_stream(pool, subject, room, cursor), media_type="text/event-stream",
                                 headers={**cors_headers(request), "Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _parse_cursor(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise dg.DelegateError("after must be an ISO timestamp") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _latest(task: dict[str, Any]) -> datetime:
    stamps = [task.get(k) for k in ("created_at", "claimed_at", "finished_at") if task.get(k)]
    return max(datetime.fromisoformat(s) for s in stamps)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


async def _stream(pool: Any, subject: str, room: Optional[str], cursor: Optional[datetime]) -> AsyncIterator[bytes]:
    """Snapshot of the room (or what changed after ``cursor``), then changes, for about STREAM_SECONDS. Each ``task`` event
    carries the whole task, so a client replaces by id and a replay after a reconnect never duplicates a message."""
    yield b": kell delegator stream\n\n"
    loop_start = asyncio.get_event_loop().time()
    tick = 0
    while asyncio.get_event_loop().time() - loop_start < STREAM_SECONDS:
        try:
            for t in await dg.list_tasks(pool, subject, room, cursor):
                yield _sse("task", t)
                cursor = max(cursor, _latest(t)) if cursor else _latest(t)
            if tick % 3 == 0:
                yield _sse("agents", {"agents": await dg.list_agents(pool, subject)})
        except dg.DelegateError as exc:
            yield _sse("error", {"error": str(exc)})
            return
        tick += 1
        yield b": keepalive\n\n"
        await asyncio.sleep(POLL_SECONDS)
    yield _sse("end", {"cursor": cursor.isoformat() if cursor else None})
