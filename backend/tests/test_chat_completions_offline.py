"""The OpenAI-compatible orchestrator surface (app/providers/chat.py, app/api/chat_completions.py).
Offline: the "provider" is an httpx MockTransport, the organisation ledger is faked, no network and no database."""
from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat_completions as api
from app.api.deps import enforce_limits
from app.providers import adapters, chat, registry
from app.providers.types import Connection, ProviderCallDenied, ProviderCallFailed, UnitSpec
from app.services import org_governance as gov
from app.services.access import AccessScope

SECRET = "sk-chat-secret-4242"
SCOPE = AccessScope.for_org_member("u1", ["org-a"])


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv(registry.FILE_ENV, raising=False)
    monkeypatch.setattr(registry, "_STORES", {})
    monkeypatch.setattr(registry, "_file_cache", (None, []))
    monkeypatch.setenv("CHAT_TEST_KEY", SECRET)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])


def _conn(cid="chat-c1", model="glm-x", provider_model="vendor/glm-x", **kw):
    base = dict(connection_id=cid, kind="openai_compatible", base_url=f"https://{cid}.example.com/v1", provider="vendor",
                units=(UnitSpec(model=model, provider_model=provider_model, input_per_mtok=1.0, output_per_mtok=2.0,
                                tier="standard"),),
                credential_ref="env:CHAT_TEST_KEY", allowed_data_classes=("USER_PRIVATE",))
    base.update(kw)
    return Connection(**base)


def _serve(monkeypatch, *conns, handler):
    monkeypatch.setattr(registry, "_STORES", {"t": registry.StaticConnectionStore(conns)})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(adapters, "http_client", lambda **kw: httpx.AsyncClient(transport=transport, **kw))


def _reply(content="hello", tool_calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "object": "chat.completion", "model": "vendor/glm-x",
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20}}


TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "d",
                                           "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
CALL = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}


def _params(**kw):
    raw = {"model": "glm-x", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS}
    raw.update(kw)
    return chat.parse_chat_body(raw)


def _chat(params, *, governed=False, **kw):
    return run(chat.chat(None, SCOPE, params, actor="u1", tenant_id="org-a", org_id=None, governed=governed, **kw))


class FakeGov:
    """Stands in for org_governance's three ledger calls and remembers the order they happened in."""

    def __init__(self, monkeypatch, events):
        self.events, self.settled, self.failed = events, [], []
        monkeypatch.setattr(gov, "reserve_call", self.reserve)
        monkeypatch.setattr(gov, "settle_call", self.settle)
        monkeypatch.setattr(gov, "fail_call", self.fail)

    async def reserve(self, pool, **kw):
        self.events.append("reserve")
        return gov.Reservation("ledger-1", kw["org_id"], 1)

    async def settle(self, pool, reservation, **kw):
        self.events.append("settle")
        self.settled.append(kw)

    async def fail(self, pool, reservation, **kw):
        self.events.append("fail")
        self.failed.append(kw)


# ------------------------------------------------------------------ the request body

def test_only_allowlisted_fields_are_forwarded_and_the_rest_is_dropped():
    p = _params(temperature=0.2, top_p=0.9, seed=3, stop=["x"], tool_choice="auto", user="alice", store=True,
                reasoning_effort="high", max_completion_tokens=900, frequency_penalty=0.1)
    assert set(p.body) == {"messages", "tools", "temperature", "top_p", "seed", "stop", "tool_choice", "frequency_penalty"}
    assert p.max_tokens == 900 and p.unit == "glm-x|direct" and p.stream is False


@pytest.mark.parametrize("patch,message", [
    ({"model": ""}, "model is required"),
    ({"model": "m|agent"}, "direct models"),
    ({"messages": []}, "non-empty"),
    ({"messages": [{"role": "robot", "content": "x"}]}, "role"),
    ({"tools": "nope"}, "tools"),
    ({"n": 2}, "n must be 1"),
    ({"temperature": "hot"}, "temperature"),
    ({"max_tokens": 0}, "max_tokens"),
    ({"stream": "yes"}, "stream"),
])
def test_bad_bodies_are_refused_with_a_reason(patch, message):
    with pytest.raises(chat.ChatRequestInvalid, match=message):
        _params(**patch)


def test_a_huge_max_tokens_is_lowered_to_the_server_limit_not_refused():
    """Claude Code sends max_tokens above 16000; a ceiling from the client must not make the request fail."""
    assert _params(max_tokens=10**9).max_tokens == chat.service.MAX_OUTPUT_TOKENS
    with pytest.raises(chat.ChatRequestInvalid):
        chat.parse_chat_body(["not", "an", "object"])


# ------------------------------------------------------------------ non-streaming

def test_tool_calls_come_back_untouched_and_the_provider_sees_the_translated_body(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers.get("authorization"), json.loads(request.content)
        return httpx.Response(200, json=_reply(content=None, tool_calls=[CALL]))
    _serve(monkeypatch, _conn(request_extras={"provider": {"zdr": True}}), handler=handler)
    out = _chat(_params(temperature=0.1, user="alice"), data_class="USER_PRIVATE")
    assert out.json["choices"][0]["message"]["tool_calls"] == [CALL]
    assert seen["url"] == "https://chat-c1.example.com/v1/chat/completions" and seen["auth"] == f"Bearer {SECRET}"
    body = seen["body"]
    assert body["model"] == "vendor/glm-x" and body["max_tokens"] == 4096 and body["tools"] == TOOLS
    assert body["provider"] == {"zdr": True} and "user" not in body and "stream" not in body
    assert out.unit == "glm-x|direct" and out.usage["tokens_in"] == 100 and out.usage["tokens_out"] == 20
    assert out.usage["cost_source"] == "declared" and out.usage["cost_usd"] == pytest.approx((100 + 40) / 1e6)
    assert SECRET not in json.dumps(out.json)


def test_the_ledger_reserves_before_the_call_and_settles_after_with_real_usage(monkeypatch):
    events = []
    fake = FakeGov(monkeypatch, events)

    def handler(request):
        events.append("send")
        return httpx.Response(200, json=_reply(usage={"prompt_tokens": 50, "completion_tokens": 5, "cost": 0.0042}))
    _serve(monkeypatch, _conn(), handler=handler)
    _chat(_params(), governed=True)
    assert events == ["reserve", "send", "settle"]
    s = fake.settled[0]
    assert s["tokens_input_fresh"] == 50 and s["tokens_output"] == 5 and s["cost_usd"] == 0.0042 and s["cost_source"] == "provider"


def test_a_provider_failure_releases_the_hold_and_an_odd_reply_is_not_billed_as_success(monkeypatch):
    events = []
    fake = FakeGov(monkeypatch, events)
    _serve(monkeypatch, _conn(), handler=lambda r: httpx.Response(400, json={"error": "bad tools"}))
    with pytest.raises(ProviderCallFailed, match="400"):
        _chat(_params(), governed=True)
    assert events == ["reserve", "fail"] and not fake.settled
    events.clear()
    _serve(monkeypatch, _conn(), handler=lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ProviderCallFailed, match="reply shape"):
        _chat(_params(), governed=True)
    assert events == ["reserve", "fail"]


def test_a_data_class_the_connection_is_not_approved_for_is_refused_before_anything_is_sent(monkeypatch):
    sent = []
    _serve(monkeypatch, _conn(), handler=lambda r: sent.append(r) or httpx.Response(200, json=_reply()))
    with pytest.raises(ProviderCallDenied, match="not approved"):
        _chat(_params(), data_class="CONFIDENTIAL_DATA")
    with pytest.raises(ProviderCallDenied, match="unknown data_class"):
        _chat(_params(), data_class="TOP_SECRET")
    assert sent == []


def test_an_unknown_model_looks_the_same_as_one_that_is_not_yours(monkeypatch):
    _serve(monkeypatch, _conn(), handler=lambda r: httpx.Response(200, json=_reply()))
    with pytest.raises(ProviderCallDenied, match="no connection available to you offers 'other|direct'"):
        _chat(_params(model="other"))


# ------------------------------------------------------------------ failover

def test_a_down_endpoint_fails_over_to_the_next_endpoint_of_the_same_model(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(503, text="down") if "fo-a" in request.url.host else httpx.Response(200, json=_reply("ok"))
    _serve(monkeypatch, _conn("fo-a"), _conn("fo-b"), handler=handler)
    out = _chat(_params())
    assert out.connection_id == "fo-b" and calls == ["fo-a.example.com", "fo-b.example.com"]


def test_a_down_model_falls_back_to_the_named_next_model_but_a_bad_request_does_not(monkeypatch):
    def handler(request):
        model = json.loads(request.content)["model"]
        return httpx.Response(503, text="down") if model == "vendor/one" else httpx.Response(200, json=_reply("two"))
    _serve(monkeypatch, _conn("fm-a", model="one", provider_model="vendor/one"),
           _conn("fm-b", model="two", provider_model="vendor/two"), handler=handler)
    out = _chat(_params(model="one"), fallback_models=["two"])
    assert out.unit == "two|direct" and out.requested_unit == "one|direct" and out.fell_back[0]["unit"] == "one|direct"
    _serve(monkeypatch, _conn("fm-c", model="one", provider_model="vendor/one"),
           _conn("fm-d", model="two", provider_model="vendor/two"), handler=lambda r: httpx.Response(400, text="bad"))
    with pytest.raises(ProviderCallFailed, match="400"):
        _chat(_params(model="one"), fallback_models=["two"])


# ------------------------------------------------------------------ streaming

SSE = (b'data: {"choices":[{"delta":{"role":"assistant","content":"Hel"}}]}\n\n'
       b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
       b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read_file","arguments":"{\\"path\\":\\"a\\"}"}}]}}]}\n\n'
       b'data: {"choices":[],"usage":{"prompt_tokens":70,"completion_tokens":9}}\n\n'
       b"data: [DONE]\n\n")


async def _drain(stream):
    return "".join([line async for line in stream])


def test_a_stream_is_relayed_unchanged_asks_for_usage_and_settles_on_the_final_chunk(monkeypatch):
    events, seen = [], {}
    fake = FakeGov(monkeypatch, events)

    def handler(request):
        seen["body"] = json.loads(request.content)
        events.append("send")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=SSE)
    _serve(monkeypatch, _conn(), handler=handler)
    out = _chat(_params(stream=True), governed=True)
    assert out.stream is not None and out.json is None and events == ["reserve", "send"]     # nothing settled yet
    text = run(_drain(out.stream))
    assert text == SSE.decode()
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {"include_usage": True}
    assert events == ["reserve", "send", "settle"]
    assert fake.settled[0]["tokens_input_fresh"] == 70 and fake.settled[0]["tokens_output"] == 9
    assert fake.settled[0]["cost_source"] == "declared" and out.usage["completed"] is True


def test_a_stream_with_no_usage_is_settled_at_the_worst_case_never_at_zero(monkeypatch):
    events = []
    fake = FakeGov(monkeypatch, events)
    no_usage = SSE.replace(b'data: {"choices":[],"usage":{"prompt_tokens":70,"completion_tokens":9}}\n\n', b"")
    _serve(monkeypatch, _conn(), handler=lambda r: httpx.Response(200, content=no_usage))
    out = _chat(_params(stream=True), governed=True)
    run(_drain(out.stream))
    assert fake.settled[0]["cost_usd"] is None and fake.settled[0]["cost_source"] is None     # settle_call then records the hold


def test_a_stream_that_is_refused_before_the_first_byte_fails_over_and_releases_nothing_twice(monkeypatch):
    events = []
    FakeGov(monkeypatch, events)

    def handler(request):
        if "st-a" in request.url.host:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, content=SSE)
    _serve(monkeypatch, _conn("st-a"), _conn("st-b"), handler=handler)
    out = _chat(_params(stream=True), governed=True)
    assert out.connection_id == "st-b" and events == ["reserve", "fail", "reserve"]
    run(_drain(out.stream))
    assert events == ["reserve", "fail", "reserve", "settle"]


def test_a_stream_that_dies_after_output_is_settled_not_released_and_the_client_gets_an_error_event(monkeypatch):
    events = []
    FakeGov(monkeypatch, events)

    class Dying(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError("connection reset")

    _serve(monkeypatch, _conn(), handler=lambda r: httpx.Response(200, stream=Dying()))
    out = _chat(_params(stream=True), governed=True)
    text = run(_drain(out.stream))
    assert "partial" in text and "upstream_error" in text
    assert events == ["reserve", "settle"] and out.usage["completed"] is False


# ------------------------------------------------------------------ the HTTP surface

def _client(monkeypatch, *conns, handler):
    _serve(monkeypatch, *conns, handler=handler)
    app = FastAPI()
    app.include_router(api.router)
    app.state.pool = None
    app.dependency_overrides[enforce_limits] = lambda: "test-key"
    return TestClient(app)


def test_the_endpoint_answers_in_the_openai_shape_with_routing_headers(monkeypatch):
    client = _client(monkeypatch, _conn(), handler=lambda r: httpx.Response(200, json=_reply(content=None, tool_calls=[CALL])))
    r = client.post("/v1/chat/completions", json={"model": "glm-x", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["tool_calls"] == [CALL]
    assert r.headers["x-stealthlab-unit"] == "glm-x|direct" and r.headers["x-stealthlab-connection"] == "chat-c1"
    assert float(r.headers["x-stealthlab-cost-usd"]) > 0


def test_the_endpoint_streams_server_sent_events(monkeypatch):
    client = _client(monkeypatch, _conn(), handler=lambda r: httpx.Response(200, content=SSE))
    with client.stream("POST", "/v1/chat/completions", json={"model": "glm-x", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "".join(r.iter_text())
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert body == SSE.decode()


def test_errors_use_the_openai_envelope_and_sensible_statuses(monkeypatch):
    client = _client(monkeypatch, _conn(), handler=lambda r: httpx.Response(503, text="down"))
    ok_body = {"model": "glm-x", "messages": [{"role": "user", "content": "hi"}]}
    bad = client.post("/v1/chat/completions", json={"model": "glm-x"})
    assert bad.status_code == 400 and bad.json()["error"]["type"] == "invalid_request_error"
    assert client.post("/v1/chat/completions", content=b"{not json").status_code == 400
    assert client.post("/v1/chat/completions", json={**ok_body, "model": "nope"}).status_code == 404
    assert client.post("/v1/chat/completions", json=ok_body, headers={"X-Stealthlab-Data-Class": "CONFIDENTIAL_DATA"}).status_code == 403
    down = client.post("/v1/chat/completions", json=ok_body)
    assert down.status_code == 502 and SECRET not in down.text


def test_models_lists_what_the_caller_can_use_with_the_data_handling_declarations(monkeypatch):
    client = _client(monkeypatch, _conn(zdr=True, region="us"), handler=lambda r: httpx.Response(200, json=_reply()))
    data = client.get("/v1/models").json()
    assert data["object"] == "list" and [m["id"] for m in data["data"]] == ["glm-x"]
    info = data["data"][0]["stealthlab"]
    assert info["compliance"]["zdr"] is True and info["compliance"]["region"] == "us" and info["input_per_mtok"] == 1.0
