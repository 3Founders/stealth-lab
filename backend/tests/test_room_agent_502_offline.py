"""The "@kell could not answer: the model endpoint answered 502" incident: the agent asked its own model endpoint through the public hosting edge,
a provider out of credit (HTTP 402) came back as a bare 502, and a failure after some text was shown threw the shown text away.
Each part of the fix is pinned here. No network: fake clients and fake streams."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.mcp_server import room_agent_routes
from app.mcp_server import server as sv
from app.services import room_agent
from tests.test_room_agent_offline import _never, _some_tools


def test_the_agent_asks_its_own_endpoint_on_this_machine(monkeypatch):
    monkeypatch.delenv("KELL_API_BASE", raising=False)
    monkeypatch.setenv("PORT", "9123")
    assert sv._self_base() == "http://127.0.0.1:9123"
    monkeypatch.setenv("KELL_API_BASE", "https://models.example.org/")
    assert sv._self_base() == "https://models.example.org"


def test_a_model_without_credit_is_skipped_for_a_while_but_never_all_of_them(monkeypatch):
    monkeypatch.setenv("KELL_ROOM_AGENT_MODEL", "main")
    monkeypatch.setenv("KELL_ROOM_AGENT_FALLBACKS", "second")
    sv._MODEL_DOWN.clear()
    assert sv._room_agent_model_chain() == ["main", "second"]
    assert sv._out_of_credit('general-compute: HTTP 402: {"error":{"message":"Credit balance is empty"}}')
    import time
    sv._MODEL_DOWN["main"] = time.monotonic() + 60
    assert sv._room_agent_model_chain() == ["second"]
    sv._MODEL_DOWN["second"] = time.monotonic() + 60
    assert sv._room_agent_model_chain() == ["main", "second"]          # nothing left to skip to: try them anyway
    sv._MODEL_DOWN.clear()


def test_a_gateway_status_with_no_message_is_called_a_brief_outage():
    assert "briefly unavailable (502)" in sv._friendly_status(502, "")
    assert sv._friendly_status(502, "every endpoint failed: HTTP 402") == "every endpoint failed: HTTP 402"
    assert sv._friendly_status(500, "") == "the model service answered 500"


class _Client:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    async def post(self, url, headers=None, json=None):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(status, body):
    return httpx.Response(status, text=body, request=httpx.Request("POST", "http://x"))


@pytest.mark.asyncio
async def test_a_gateway_502_is_retried_and_a_real_refusal_is_not(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    c = _Client([_resp(502, "<html>Bad Gateway</html>"), _resp(502, "<html>Bad Gateway</html>"), _resp(200, "{}")])
    assert (await sv._post_retry(c, "http://x", {}, {})).status_code == 200 and c.calls == 3
    kell = _Client([_resp(502, json.dumps({"error": {"message": "every endpoint failed: HTTP 402"}}))])
    assert (await sv._post_retry(kell, "http://x", {}, {})).status_code == 502 and kell.calls == 1      # kell's own error: no retry
    dropped = _Client([httpx.ConnectError("x"), httpx.ReadTimeout("x"), httpx.ConnectError("x")])
    with pytest.raises(RuntimeError, match="could not be reached"):
        await sv._post_retry(dropped, "http://x", {}, {})
    assert dropped.calls == 3


@pytest.mark.asyncio
async def test_an_answer_cut_off_midway_keeps_what_was_shown():
    async def stream(messages, tools):
        yield {"choices": [{"delta": {"content": "The first half of the answer. "}}]}
        raise RuntimeError("the connection to the model service dropped while it was answering")

    events = [e async for e in room_agent.stream_answer("tell me about x in detail", [], model_stream=stream, find_tools=_some_tools, use_tool=_never)]
    kinds = [k for k, _ in events]
    assert kinds == ["delta", "final"]
    final = events[-1][1]
    assert final.text.startswith("The first half of the answer.") and "cut off" in final.text and "Ask me again" in final.text


@pytest.mark.asyncio
async def test_a_failure_before_anything_was_shown_is_still_an_error():
    async def stream(messages, tools):
        raise RuntimeError("the model service was briefly unavailable (502); ask again in a moment")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="briefly unavailable"):
        async for _ in room_agent.stream_answer("hello there friend", [], model_stream=stream, find_tools=_some_tools, use_tool=_never):
            pass


@pytest.mark.asyncio
async def test_the_stream_to_the_browser_sends_a_comment_while_the_model_is_silent(monkeypatch):
    from tests.test_room_agent_route_offline import _pool, _request, _route

    monkeypatch.setattr(room_agent_routes, "HEARTBEAT_S", 0.05)

    async def slow_stream(authorization, messages, tools):
        await asyncio.sleep(0.3)
        yield {"choices": [{"delta": {"content": "Hello."}}]}

    route = _route(pool=_pool(), model_stream=slow_stream)
    r = await route(_request({"text": "hi"}, accept="text/event-stream"))
    frames = []
    async for chunk in r.body_iterator:
        frames.append(chunk if isinstance(chunk, str) else chunk.decode())
    text = "".join(frames)
    assert ": keep-alive" in text and '"delta": "Hello."' in text and '"done": true' in text
