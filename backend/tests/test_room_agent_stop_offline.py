"""The Stop button reaches the server: POST /v1/rooms/{id}/agent/stop cancels the caller's running answer, and nothing is posted afterwards.
An answer keeps running when only the connection closes (a closed tab must not lose a paid-for reply), so Stop has to be said explicitly.
No network, no database: a model stream that never finishes."""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from app.mcp_server import room_agent_routes
from tests.test_room_agent_route_offline import ROOM, FakeServer, Gate, _pool, _request


def _stop_request(body: dict, room: str = ROOM) -> Request:
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": f"/v1/rooms/{room}/agent/stop", "headers": [(b"authorization", b"Bearer t")],
                    "path_params": {"room_id": room}, "client": ("127.0.0.1", 1), "query_string": b"", "server": ("t", 80)}, receive)


async def _find(_need):
    return []


async def _use(*_a):
    return ""


def _routes(model_stream, pool, gate=None):
    server = FakeServer()
    room_agent_routes.register(server, gate or Gate(), lambda: pool, model_call=lambda *a: _find(""), find_tools=_find, use_tool=_use,
                               model_stream=model_stream)
    return server.routes["/v1/rooms/{room_id}/agent"], server.routes["/v1/rooms/{room_id}/agent/stop"]


async def _slow_model(authorization, messages, tools):
    yield {"choices": [{"delta": {"content": "Thinking about tea"}}]}
    await asyncio.sleep(60)                                    # a long essay: far longer than this test lasts
    yield {"choices": [{"delta": {"content": " and more"}}]}


async def _first_frame(response) -> str:
    async for frame in response.body_iterator:
        if "delta" in str(frame):
            return str(frame)
    return ""


@pytest.mark.asyncio
async def test_stop_cancels_the_running_answer_and_nothing_is_posted():
    pool = _pool()
    agent, stop = _routes(_slow_model, pool)
    response = await agent(_request({"text": "write a long essay about tea"}, accept="text/event-stream"))
    assert "Thinking about tea" in await _first_frame(response)
    assert room_agent_routes._BY_ROOM                              # the answer is registered as running

    stopped = await stop(_stop_request({}))
    assert stopped.status_code == 200 and json.loads(stopped.body.decode()) == {"stopped": 1}

    rest = [f async for f in response.body_iterator]               # the stream ends instead of waiting a minute for the model
    assert not any('"done"' in str(f) for f in rest)
    await asyncio.sleep(0)
    assert not [a for s, a in pool.conn.log if "WITH bump AS" in s]   # no reply was posted into the room
    assert room_agent_routes._BY_ROOM == {}                        # and the registry is clean


@pytest.mark.asyncio
async def test_stop_only_touches_the_callers_own_answers_in_that_room():
    pool = _pool()
    agent, stop = _routes(_slow_model, pool)
    response = await agent(_request({"text": "write a long essay about tea"}, accept="text/event-stream"))
    await _first_frame(response)

    other_person = _routes(_slow_model, pool, gate=Gate("person-2"))[1]
    assert json.loads((await other_person(_stop_request({}))).body.decode()) == {"stopped": 0}        # someone else's Stop does nothing
    assert json.loads((await stop(_stop_request({}, room="22222222-2222-4222-8222-222222222222"))).body.decode()) == {"stopped": 0}   # another room
    assert json.loads((await stop(_stop_request({"agent": "helper"}))).body.decode()) == {"stopped": 0}  # another agent's name

    assert json.loads((await stop(_stop_request({"agent": "@kell"}))).body.decode()) == {"stopped": 1}
    [f async for f in response.body_iterator]


@pytest.mark.asyncio
async def test_stop_needs_a_signed_in_person():
    agent, stop = _routes(_slow_model, _pool(), gate=Gate(subject=None))
    assert (await stop(_stop_request({}))).status_code == 401
