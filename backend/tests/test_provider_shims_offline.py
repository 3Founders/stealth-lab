"""Anthropic Messages and OpenAI Responses on top of the governed chat path (app/providers/shims.py and the routes in
app/api/chat_completions.py). Offline: fake provider via httpx.MockTransport, no network, no database."""
from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat_completions as api
from app.api.deps import enforce_limits
from app.providers import adapters, chat, registry, shims
from app.providers.types import Connection, UnitSpec

SECRET = "sk-shim-secret-777"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv(registry.FILE_ENV, raising=False)
    monkeypatch.setattr(registry, "_STORES", {})
    monkeypatch.setattr(registry, "_file_cache", (None, []))
    monkeypatch.setenv("SHIM_TEST_KEY", SECRET)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])


def _client(monkeypatch, handler):
    conn = Connection(connection_id="shim-c1", kind="openai_compatible", base_url="https://shim-c1.example.com/v1",
                      provider="vendor", credential_ref="env:SHIM_TEST_KEY", allowed_data_classes=("USER_PRIVATE",),
                      units=(UnitSpec(model="glm-x", provider_model="vendor/glm-x", input_per_mtok=1.0, output_per_mtok=2.0),))
    monkeypatch.setattr(registry, "_STORES", {"t": registry.StaticConnectionStore([conn])})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(adapters, "http_client", lambda **kw: httpx.AsyncClient(transport=transport, **kw))
    app = FastAPI()
    app.include_router(api.router)
    app.state.pool = None
    app.dependency_overrides[enforce_limits] = lambda: "k"
    return TestClient(app)


def _chunk(delta=None, finish=None, usage=None):
    obj = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if delta is not None or finish else []}
    if usage:
        obj["usage"] = usage
    return f"data: {json.dumps(obj)}\n\n"


TEXT_AND_CALL = (
    _chunk({"role": "assistant", "content": "Let me "}) + _chunk({"content": "look."})
    + _chunk({"tool_calls": [{"index": 0, "id": "call_9", "type": "function", "function": {"name": "read_file", "arguments": ""}}]})
    + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":"}}]})
    + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": " \"a.py\"}"}}]})
    + _chunk({}, finish="tool_calls") + _chunk(usage={"prompt_tokens": 40, "completion_tokens": 11}) + "data: [DONE]\n\n")


async def _lines(text):
    for line in text.splitlines(keepends=True):
        yield line


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        name = next((l[7:] for l in block.splitlines() if l.startswith("event: ")), None)
        data = next((l[6:] for l in block.splitlines() if l.startswith("data: ")), None)
        out.append((name, json.loads(data)))
    return out


async def _collect(gen):
    return "".join([part async for part in gen])


# ------------------------------------------------------------------ Anthropic Messages -> chat

def test_messages_request_becomes_a_chat_request_with_tool_results_before_the_user_text():
    body = shims.anthropic_to_chat({
        "model": "glm-x", "max_tokens": 500, "stream": True, "stop_sequences": ["END"], "temperature": 0.3,
        "system": [{"type": "text", "text": "You are a coder.", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "read_file", "description": "read", "input_schema": {"type": "object", "properties": {}}},
                  {"type": "web_search_20250305", "name": "web_search"}],
        "tool_choice": {"type": "any"},
        "messages": [
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "ok"},
                                              {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a"}}]},
            {"role": "user", "content": [{"type": "text", "text": "and then?"},
                                         {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "file body"}]}]}]})
    assert body["messages"][0] == {"role": "system", "content": "You are a coder."}
    assert body["messages"][2]["tool_calls"] == [{"id": "toolu_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}}]
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "toolu_1", "content": "file body"}
    assert body["messages"][4] == {"role": "user", "content": "and then?"}
    assert [t["function"]["name"] for t in body["tools"]] == ["read_file"]          # the server-side tool is dropped
    assert body["tool_choice"] == "required" and body["stop"] == ["END"] and body["stream"] is True and body["max_tokens"] == 500
    chat.parse_chat_body(body)                                                      # and the result is a valid chat body


def test_system_turns_inside_messages_are_hoisted_to_the_front():
    """Claude Code sends {"role": "system"} inside `messages` as well as `system` (found by running the real client)."""
    body = shims.anthropic_to_chat({"model": "m", "max_tokens": 5, "system": [{"type": "text", "text": "top"}], "messages": [
        {"role": "user", "content": "hi"}, {"role": "system", "content": "extra rules"}]})
    assert [m["role"] for m in body["messages"]] == ["system", "system", "user"]
    assert body["messages"][1]["content"] == "extra rules"


@pytest.mark.parametrize("raw,message", [
    ({"model": "m", "messages": [{"role": "user", "content": "x"}]}, "max_tokens"),
    ({"model": "m", "max_tokens": 5, "messages": [{"role": "robot", "content": "x"}]}, "role"),
])
def test_bad_messages_requests_are_refused(raw, message):
    with pytest.raises(chat.ChatRequestInvalid, match=message):
        shims.anthropic_to_chat(raw)


def test_a_chat_reply_becomes_a_messages_reply():
    out = shims.chat_to_anthropic({"choices": [{"message": {"content": "hi", "tool_calls": [
        {"id": "call_1", "function": {"name": "f", "arguments": "{\"a\": 1}"}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3}}, model="glm-x")
    assert out["type"] == "message" and out["role"] == "assistant" and out["model"] == "glm-x"
    assert out["content"] == [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "call_1", "name": "f", "input": {"a": 1}}]
    assert out["stop_reason"] == "tool_use" and out["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert shims.chat_to_anthropic({"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}, model="m")["stop_reason"] == "max_tokens"


def test_a_chat_stream_becomes_the_messages_event_sequence_with_usage_at_the_end():
    events = _events(run(_collect(shims.chat_stream_to_anthropic(_lines(TEXT_AND_CALL), model="glm-x", input_tokens=33))))
    assert [n for n, _ in events] == [
        "message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop",
        "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop", "message_delta", "message_stop"]
    assert events[0][1]["message"]["usage"]["input_tokens"] == 33
    assert events[1][1]["content_block"] == {"type": "text", "text": ""} and events[2][1]["delta"] == {"type": "text_delta", "text": "Let me "}
    start = events[5][1]
    assert start["index"] == 1 and start["content_block"]["type"] == "tool_use" and start["content_block"]["id"] == "call_9"
    assert "".join(e[1]["delta"]["partial_json"] for e in events[6:8]) == "{\"path\": \"a.py\"}"
    assert events[9][1]["delta"]["stop_reason"] == "tool_use" and events[9][1]["usage"]["output_tokens"] == 11


def test_an_upstream_error_event_becomes_a_messages_error_event():
    err = 'data: {"error": {"message": "the upstream stream ended early", "type": "upstream_error"}}\n\n'
    events = _events(run(_collect(shims.chat_stream_to_anthropic(_lines(_chunk({"content": "x"}) + err), model="m", input_tokens=1))))
    assert events[-1][0] == "error" and events[-1][1]["error"]["type"] == "api_error"


# ------------------------------------------------------------------ OpenAI Responses -> chat

def test_responses_request_becomes_a_chat_request():
    body = shims.responses_to_chat({
        "model": "glm-x", "instructions": "be brief", "max_output_tokens": 300, "tool_choice": "auto", "stream": False,
        "tools": [{"type": "function", "name": "shell", "description": "run", "parameters": {"type": "object", "properties": {}}},
                  {"type": "web_search"}],
        "input": [{"role": "developer", "content": [{"type": "input_text", "text": "rules"}]},
                  {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]},
                  {"type": "reasoning", "summary": []},
                  {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{\"cmd\": \"ls\"}"},
                  {"type": "function_call", "call_id": "c2", "name": "shell", "arguments": "{\"cmd\": \"pwd\"}"},
                  {"type": "function_call_output", "call_id": "c1", "output": "a.py"},
                  {"type": "function_call_output", "call_id": "c2", "output": "/repo"}]})
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "system", "user", "assistant", "tool", "tool"]
    assert [c["id"] for c in body["messages"][3]["tool_calls"]] == ["c1", "c2"]          # consecutive calls merge into one turn
    assert body["max_tokens"] == 300 and [t["function"]["name"] for t in body["tools"]] == ["shell"] and body["tool_choice"] == "auto"
    chat.parse_chat_body(body)


def test_a_bare_string_input_works_and_server_held_state_is_refused():
    assert shims.responses_to_chat({"model": "m", "input": "hello"})["messages"] == [{"role": "user", "content": "hello"}]
    with pytest.raises(chat.ChatRequestInvalid, match="previous_response_id"):
        shims.responses_to_chat({"model": "m", "input": "x", "previous_response_id": "resp_1"})
    with pytest.raises(chat.ChatRequestInvalid, match="input"):
        shims.responses_to_chat({"model": "m"})


def test_a_chat_reply_becomes_a_responses_object_with_output_items_and_usage():
    out = shims.chat_to_responses({"choices": [{"message": {"content": "done", "tool_calls": [
        {"id": "call_1", "function": {"name": "shell", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 6}}}, model="glm-x", now=1700000000)
    assert out["object"] == "response" and out["status"] == "completed" and out["model"] == "glm-x"
    assert out["output"][0]["type"] == "message" and out["output"][0]["content"][0] == {"type": "output_text", "text": "done", "annotations": []}
    assert out["output"][1]["type"] == "function_call" and out["output"][1]["call_id"] == "call_1"
    assert out["usage"]["input_tokens"] == 10 and out["usage"]["total_tokens"] == 14 and out["usage"]["input_tokens_details"]["cached_tokens"] == 6


def test_a_chat_stream_becomes_the_responses_event_sequence():
    events = _events(run(_collect(shims.chat_stream_to_responses(_lines(TEXT_AND_CALL), model="glm-x", now=1))))
    names = [n for n, _ in events]
    assert names[:2] == ["response.created", "response.in_progress"] and names[-1] == "response.completed"
    assert names.index("response.output_text.delta") < names.index("response.output_text.done") < names.index("response.function_call_arguments.delta")
    assert [e[1]["sequence_number"] for e in events] == list(range(len(events)))
    assert "".join(e["delta"] for n, e in events if n == "response.output_text.delta") == "Let me look."
    done = next(e for n, e in events if n == "response.function_call_arguments.done")
    assert done["arguments"] == "{\"path\": \"a.py\"}"
    final = events[-1][1]["response"]
    assert [o["type"] for o in final["output"]] == ["message", "function_call"] and final["output"][1]["call_id"] == "call_9"
    assert final["usage"]["input_tokens"] == 40 and final["usage"]["output_tokens"] == 11


# ------------------------------------------------------------------ over HTTP

def _reply(content="hello", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5}}


def test_messages_endpoint_end_to_end_non_streaming_and_streaming(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=TEXT_AND_CALL) if seen["body"].get("stream") else httpx.Response(200, json=_reply("hi there"))
    client = _client(monkeypatch, handler)
    body = {"model": "glm-x", "max_tokens": 100, "system": "sys", "messages": [{"role": "user", "content": "hello"}]}
    r = client.post("/v1/messages", json=body)
    assert r.status_code == 200 and r.json()["content"] == [{"type": "text", "text": "hi there"}] and r.json()["type"] == "message"
    assert seen["body"]["model"] == "vendor/glm-x" and seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    with client.stream("POST", "/v1/messages", json={**body, "stream": True}) as s:
        text = "".join(s.iter_text())
        assert s.headers["content-type"].startswith("text/event-stream")
    assert [n for n, _ in _events(text)][0] == "message_start" and _events(text)[-1][0] == "message_stop"


def test_messages_errors_use_the_anthropic_envelope(monkeypatch):
    client = _client(monkeypatch, lambda r: httpx.Response(200, json=_reply()))
    bad = client.post("/v1/messages", json={"model": "glm-x", "messages": [{"role": "user", "content": "x"}]})
    assert bad.status_code == 400 and bad.json()["type"] == "error" and bad.json()["error"]["type"] == "invalid_request_error"
    missing = client.post("/v1/messages", json={"model": "nope", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]})
    assert missing.status_code == 404 and missing.json()["error"]["type"] == "not_found_error"


def test_count_tokens_is_an_estimate_that_calls_nobody(monkeypatch):
    sent = []
    client = _client(monkeypatch, lambda r: sent.append(r) or httpx.Response(200, json=_reply()))
    r = client.post("/v1/messages/count_tokens", json={"model": "glm-x", "messages": [{"role": "user", "content": "x" * 350}]})
    assert r.status_code == 200 and 90 <= r.json()["input_tokens"] <= 140 and sent == []


def test_responses_endpoint_end_to_end(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=TEXT_AND_CALL) if json.loads(request.content).get("stream") else httpx.Response(
            200, json=_reply(None, [{"id": "call_1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}]))
    client = _client(monkeypatch, handler)
    body = {"model": "glm-x", "input": "run it", "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}]}
    r = client.post("/v1/responses", json=body)
    assert r.status_code == 200 and r.json()["output"][0]["type"] == "function_call" and r.json()["usage"]["total_tokens"] == 17
    with client.stream("POST", "/v1/responses", json={**body, "stream": True}) as s:
        names = [n for n, _ in _events("".join(s.iter_text()))]
    assert names[0] == "response.created" and names[-1] == "response.completed"
    refused = client.post("/v1/responses", json={**body, "previous_response_id": "resp_1"})
    assert refused.status_code == 400 and refused.json()["error"]["type"] == "invalid_request_error"
