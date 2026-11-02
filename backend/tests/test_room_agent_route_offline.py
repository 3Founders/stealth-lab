"""POST /v1/rooms/{id}/agent, end to end without a network or database: the happy path, the model's own refusal being
shown to the person (not a generic failure), and a bad room id refused with its sentence."""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from starlette.requests import Request

from app.mcp_server import room_agent_routes

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
ROOM = "11111111-1111-4111-8111-111111111111"


class FakeConn:
    def __init__(self, answers):
        self.answers, self.log = answers, []

    def _find(self, sql):
        for needle, value in self.answers.items():
            if needle in sql:
                return value() if callable(value) else value
        return None

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql) or []

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def execute(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)


class FakePool:
    def __init__(self, answers=None):
        self.conn = FakeConn(answers or {})

    async def fetch(self, sql, *a):
        return await self.conn.fetch(sql, *a)

    async def fetchrow(self, sql, *a):
        return await self.conn.fetchrow(sql, *a)

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)


class FakeServer:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.routes[path] = fn
            return fn
        return deco


class Gate:
    def __init__(self, subject="person-1"):
        self.subject_value = subject

    async def subject(self, request):
        return (self.subject_value, None) if self.subject_value else (None, ("sign in first", 401))


def _pool(room=ROOM):
    return FakePool({
        "JOIN room_members m": {"id": uuid.UUID(room), "my_role": "owner", "next_seq": 2, "title": "#general",
                                "mode": "open", "created_at": NOW, "owner_sub": "person-1"},
        "FROM users WHERE external_subject": [],
        "ORDER BY seq DESC": [{"id": uuid.uuid4(), "room_id": uuid.UUID(room), "seq": 1, "kind": "msg",
                               "sender_kind": "person", "sender_handle": "@person", "sender_sub": "person-1",
                               "body": "hello kell", "target": None, "reply_to": None, "lamport": 0,
                               "mentions": [], "depth": 0, "device_id": None, "created_at": NOW}],
        "WITH bump AS": {"seq": 2},
    })


def _request(body: dict, room: str = ROOM, accept: str | None = None) -> Request:
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    headers = [(b"authorization", b"Bearer t")] + ([(b"accept", accept.encode())] if accept else [])
    return Request({"type": "http", "method": "POST", "path": f"/v1/rooms/{room}/agent", "headers": headers,
                    "path_params": {"room_id": room}, "client": ("127.0.0.1", 1), "query_string": b"", "server": ("t", 80)}, receive)


def _route(model_call=None, pool=None, gate=None, find_tools=None, use_tool=None, model_stream=None):
    server = FakeServer()
    room_agent_routes.register(
        server, gate or Gate(), lambda: pool or _pool(),
        model_call=model_call or (lambda authorization, messages, tools: _none()),
        find_tools=find_tools or (lambda need: _none()),
        use_tool=use_tool or (lambda subject, server_name, tool, args: _none()),
        model_stream=model_stream,
    )
    return server.routes["/v1/rooms/{room_id}/agent"]


async def _none(*_a):
    return [] if not _a else ""


def _body(response):
    return json.loads(response.body.decode())


@pytest.mark.asyncio
async def test_a_plain_answer_is_posted_into_the_room_as_kell():
    async def model(authorization, messages, tools):
        assert authorization == "Bearer t"
        assert messages[-1]["content"] == "hello kell"
        return {"choices": [{"message": {"content": "Hi - how can I help?"}}]}

    pool = _pool()
    route = _route(model, pool=pool)
    r = await route(_request({"text": "hello kell"}))
    assert r.status_code == 200
    body = _body(r)
    assert body["posted"] is True and body["text"] == "Hi - how can I help?"
    inserts = [a for s, a in pool.conn.log if "WITH bump AS" in s]
    assert inserts and inserts[0][3] == "agent" and inserts[0][4] == "@kell"       # sender kind, handle


@pytest.mark.asyncio
async def test_the_models_own_refusal_is_shown_not_a_generic_failure():
    async def model(authorization, messages, tools):
        raise RuntimeError("the pilot daily cap is reached")

    r = await _route(model)(_request({"text": "hello"}))
    assert r.status_code == 502
    assert "the pilot daily cap is reached" in _body(r)["error"]


@pytest.mark.asyncio
async def test_a_title_instead_of_a_room_id_is_refused_with_its_sentence():
    r = await _route(lambda a, m, t: None)(_request({"text": "hello"}, room="#general"))
    assert r.status_code == 400 and "not a valid room id" in _body(r)["error"]


@pytest.mark.asyncio
async def test_the_stream_sends_deltas_then_done_and_posts_once():
    async def model_stream(authorization, messages, tools):
        for piece in ["Hi", " there"]:
            yield {"choices": [{"delta": {"content": piece}}]}

    pool = _pool()
    route = _route(pool=pool, model_stream=model_stream)
    r = await route(_request({"text": "hi"}, accept="text/event-stream"))
    frames = []
    async for chunk in r.body_iterator:
        frames.append(chunk if isinstance(chunk, str) else chunk.decode())
    payload = "".join(frames)
    assert '"delta": "Hi"' in payload and '"delta": " there"' in payload and '"done": true' in payload
    inserts = [a for s, a in pool.conn.log if "WITH bump AS" in s]
    assert len(inserts) == 1


@pytest.mark.asyncio
async def test_an_empty_message_is_refused_and_nothing_is_posted():
    r = await _route(lambda a, m, t: None)(_request({"text": "  "}))
    assert r.status_code == 400 and "text" in _body(r)["error"]
