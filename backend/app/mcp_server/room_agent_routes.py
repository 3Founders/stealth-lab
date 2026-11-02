"""POST /v1/rooms/{room_id}/agent: the built-in room agent (@kell) answers in a room.

The website calls this when a person mentions @kell. The server reads the room's recent messages, asks kell's router which
tools fit, answers with the model (at most a few rounds of tool calls through the same gateway as use_tool: consent, public
data only, secret scan, rate limit, audit), and posts the reply into the room as @kell, so it syncs to every device like any
other message. Nothing is posted when the model or the gateway fails.

Streaming: with `Accept: text/event-stream` the answer arrives as `data: {"delta": "..."}` frames while it is written, then
one `data: {"done": true, "text", "event"}` when it has been posted (or `data: {"error": "..."}`). Without it the call is
the same but buffered, which is what older clients and the tests use.

Who may call: a signed-in person (the same website-session gate as the rooms routes), and only for a room they are in. The
model call is forwarded to this deployment's own /v1/chat/completions with the caller's own bearer, so the pilot's policy,
budget and data-class checks apply unchanged (PUBLIC_SOURCE).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.mcp_server.room_routes import _error, _json, cors_headers
from app.services import room_agent as agent
from app.services import rooms as rm

log = logging.getLogger(__name__)

_INSTANCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")

ModelCall = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], Awaitable[dict[str, Any]]]
ModelStream = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], AsyncIterator[dict[str, Any]]]
FindTools = Callable[[str], Awaitable[list[dict[str, Any]]]]
UseTool = Callable[[str, str, str, dict[str, Any]], Awaitable[Any]]


def _sse(obj: Any) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


def register(server: Any, gate: Any, pool_getter: Callable[[], Any], *, model_call: ModelCall,
             find_tools: FindTools, use_tool: UseTool, model_stream: ModelStream | None = None, recent: int = 12,
             discover: Any = None, tools_of: Any = None) -> None:
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
        # Every agent is kell: the default one is @kell, any other name is another instance of it.
        raw_name = str(data.get("agent") or "kell").strip().lstrip("@").lower()
        if not _INSTANCE_NAME.match(raw_name):
            return _error(request, "an agent name is 1 to 40 letters, numbers, dots, dashes or underscores", 400)
        handle = f"@{raw_name}"
        instructions = data.get("instructions") if isinstance(data.get("instructions"), str) else ""
        try:
            history = agent.transcript(await rm.recent_events(pool, subject, room_id, recent), self_handle=handle)
        except rm.RoomError as exc:
            return _error(request, str(exc), exc.status)

        bearer = request.headers.get("authorization", "")

        async def finish(outcome: agent.AgentOutcome) -> tuple[str, Any, str | None]:
            reply = outcome.text.strip() or "I do not have an answer for that."
            for link in dict.fromkeys(outcome.consent_links):
                reply += f"\n\nAllow kell to use that tool here (15 minutes): {link}"
            try:
                appended = await rm.append_events(pool, subject, room_id,
                                                  [rm.agent_event(handle, reply)])
            except rm.RoomError as exc:
                return reply, None, str(exc)
            return reply, (appended["accepted"][0] if appended["accepted"] else None), None

        if model_stream is not None and "text/event-stream" in (request.headers.get("accept") or ""):
            async def frames() -> AsyncIterator[str]:
                final: agent.AgentOutcome | None = None
                try:
                    async for kind, value in agent.stream_answer(
                        text.strip()[: rm.MAX_BODY], history,
                        model_stream=lambda messages, tools: model_stream(bearer, messages, tools),
                        find_tools=find_tools,
                        use_tool=lambda server_name, tool, args: use_tool(subject, server_name, tool, args),
                        handle=handle, instructions=instructions,
                        discover=discover, tools_of=(lambda server_name: tools_of(subject, server_name)) if tools_of else None,
                    ):
                        if kind == "delta":
                            yield _sse({"delta": value})
                        else:
                            final = value
                except RuntimeError as exc:          # the model endpoint's own refusal
                    log.exception("room agent: the model refused the answer for room %s", room_id)
                    yield _sse({"error": f"{handle} could not answer: {str(exc).strip()[:300]}"})
                    return
                except rm.RoomError as exc:
                    yield _sse({"error": str(exc)})
                    return
                except Exception:  # noqa: BLE001
                    log.exception("room agent: the stream failed for room %s", room_id)
                    yield _sse({"error": "the room agent could not answer just now; nothing was posted"})
                    return
                reply, accepted, why = await finish(final or agent.AgentOutcome(text=""))
                if why is not None:
                    yield _sse({"error": why})
                    return
                yield _sse({"done": True, "text": reply, "event": accepted,
                            "tools": (final.tools_used if final else [])})

            return StreamingResponse(frames(), media_type="text/event-stream",
                                     headers={**cors_headers(request), "Cache-Control": "no-store",
                                              "X-Accel-Buffering": "no"})

        try:
            outcome = await agent.converse(text.strip()[: rm.MAX_BODY], history, model_call=lambda m, t: model_call(bearer, m, t),
                                           find_tools=find_tools,
                                           use_tool=lambda server_name, tool, args: use_tool(subject, server_name, tool, args),
                                           handle=handle, instructions=instructions, discover=discover,
                                           tools_of=(lambda server_name: tools_of(subject, server_name)) if tools_of else None)
        except rm.RoomError:
            raise
        except RuntimeError as exc:              # the model endpoint's own refusal (cap, policy, sign-in, upstream)
            log.exception("room agent: the model refused the answer for room %s", room_id)
            why = str(exc).strip()[:300] or "the model did not answer"
            return _error(request, f"{handle} could not answer: {why}", 502)
        except Exception:  # noqa: BLE001 -- a failed answer posts nothing, rather than half a reply
            log.exception("room agent: the answer failed for room %s", room_id)
            return _error(request, "the room agent could not answer just now; nothing was posted", 502)

        reply, accepted, why = await finish(outcome)
        if why is not None:
            return _error(request, why, 400)
        return _json(request, {"posted": accepted is not None, "text": reply, "tools": outcome.tools_used, "event": accepted})
