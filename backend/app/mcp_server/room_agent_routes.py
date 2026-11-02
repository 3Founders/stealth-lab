"""POST /v1/rooms/{room_id}/agent: the built-in room agent (@kell) answers in a room.

The website calls this when a person mentions @kell in a room. The server does the work: it reads the room's recent messages,
asks kell's router which tools fit, lets the model answer (with at most a few rounds of tool calls, executed by the same
gateway as use_tool: consent, public data only, secret scan, rate limit, audit), and posts the reply into the room as @kell, so
it syncs to every device like any other message. Nothing is posted when the model or the gateway fails.

Who may call: a signed-in person (the same website-session gate as the rooms routes), and only for a room they are in. The
model call is forwarded to this deployment's own /v1/chat/completions with the caller's own bearer, so the pilot's policy,
budget and data-class checks apply unchanged (PUBLIC_SOURCE).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.mcp_server.room_routes import _error, _json, cors_headers
from app.services import room_agent as agent
from app.services import rooms as rm

log = logging.getLogger(__name__)

ModelCall = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], Awaitable[dict[str, Any]]]
FindTools = Callable[[str], Awaitable[list[dict[str, Any]]]]
UseTool = Callable[[str, str, str, dict[str, Any]], Awaitable[Any]]


def register(server: Any, gate: Any, pool_getter: Callable[[], Any], *, model_call: ModelCall,
             find_tools: FindTools, use_tool: UseTool, recent: int = 30) -> None:
    @server.custom_route("/v1/rooms/{room_id}/agent", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def room_agent_route(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers(request))
        subject, refusal = await gate.subject(request)
        if refusal is not None:
            return _error(request, refusal[0], refusal[1])
        pool = pool_getter()
        if pool is None:
            return _error(request, "the server is starting; retry shortly", 503)
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return _error(request, "the request body must be JSON", 400)
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            return _error(request, "send the message in `text`", 400)
        room_id = request.path_params["room_id"]
        try:
            history = agent.transcript(await rm.recent_events(pool, subject, room_id, recent))
        except rm.RoomError as exc:
            return _error(request, str(exc), exc.status)

        bearer = request.headers.get("authorization", "")

        async def call_model(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            return await model_call(bearer, messages, tools)

        try:
            outcome = await agent.converse(text.strip()[: rm.MAX_BODY], history, model_call=call_model,
                                           find_tools=find_tools,
                                           use_tool=lambda server, tool, args: use_tool(subject, server, tool, args))
        except rm.RoomError:
            raise
        except RuntimeError as exc:              # the model endpoint's own refusal (cap, policy, sign-in, upstream)
            log.exception("room agent: the model refused the answer for room %s", room_id)
            why = str(exc).strip()[:300] or "the model did not answer"
            return _error(request, f"@kell could not answer: {why}", 502)
        except Exception:  # noqa: BLE001 -- a failed answer posts nothing, rather than half a reply
            log.exception("room agent: the answer failed for room %s", room_id)
            return _error(request, "the room agent could not answer just now; nothing was posted", 502)

        reply = outcome.text.strip() or "I do not have an answer for that."
        for link in dict.fromkeys(outcome.consent_links):
            reply += f"\n\nAllow kell to use that tool here (15 minutes): {link}"
        try:
            appended = await rm.append_events(pool, subject, room_id,
                                              [rm.agent_event(agent.SELF_HANDLE, reply)])
        except rm.RoomError as exc:
            return _error(request, str(exc), exc.status)
        posted = appended["accepted"][0] if appended["accepted"] else None
        return _json(request, {"posted": posted is not None, "text": reply, "tools": outcome.tools_used, "event": posted})
