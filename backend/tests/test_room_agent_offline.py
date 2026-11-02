"""The built-in room agent's loop, offline: a plain answer, one round of a gateway tool, the consent link coming back as a
sentence, a bad tool call refused, and the round limit. No model, network or database."""
from __future__ import annotations

import json

import pytest

from app.services import room_agent


def _reply(content="", tool_calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": tool_calls or []}}]}


def _call(server="io.github.upstash/context7", name="get_docs", args=None, cid="call_1"):
    return {"id": cid, "type": "function",
            "function": {"name": "use_tool", "arguments": json.dumps({"server": server, "name": name, "arguments": args or {}}) }}


class Model:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    async def __call__(self, messages, tools):
        self.seen.append((messages, tools))
        return self.replies.pop(0)


async def _no_tools(need):
    return []


async def _some_tools(need):
    return [{"server": "io.github.upstash/context7", "about": "current docs for a library"}]


async def _never(server, name, args):
    raise AssertionError("no tool should run")


@pytest.mark.asyncio
async def test_stream_answer_yields_the_text_as_it_arrives_then_the_final_answer():
    async def model_stream(messages, tools):
        for piece in ["Hel", "lo", " there"]:
            yield {"choices": [{"delta": {"content": piece}}]}

    events = [e async for e in room_agent.stream_answer(
        "hi", [], model_stream=model_stream, find_tools=_no_tools, use_tool=_never)]
    assert [k for k, _ in events] == ["delta", "delta", "delta", "final"]
    assert "".join(v for k, v in events if k == "delta") == "Hello there"
    final = events[-1][1]
    assert final.text == "Hello there" and final.tools_used == []


@pytest.mark.asyncio
async def test_stream_answer_runs_a_tool_round_then_streams_the_next_round():
    rounds = {"n": 0}

    async def model_stream(messages, tools):
        rounds["n"] += 1
        if rounds["n"] == 1:
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "use_tool", "arguments": json.dumps({"server": "io.github.upstash/context7", "name": "get_docs", "arguments": {}})}}]}}]}
        else:
            yield {"choices": [{"delta": {"content": "From the docs: v15."}}]}

    async def use_tool(server, name, args):
        return json.dumps({"result": "v15 notes"})

    events = [e async for e in room_agent.stream_answer(
        "latest next docs?", [], model_stream=model_stream, find_tools=_some_tools, use_tool=use_tool)]
    assert events[-1][0] == "final" and events[-1][1].text == "From the docs: v15."
    assert events[-1][1].tools_used == ["io.github.upstash/context7/get_docs"]


@pytest.mark.asyncio
async def test_a_plain_answer_uses_no_tool_but_the_tools_are_still_offered():
    model = Model(_reply("three ways to do it: ..."))
    out = await room_agent.converse("how do I do x?", [], model_call=model, find_tools=_no_tools, use_tool=_never)
    assert out.text.startswith("three ways") and out.tools_used == [] and out.consent_links == []
    messages, tools = model.seen[0]
    assert [t["function"]["name"] for t in tools] == ["discover_tools", "use_tool"] and messages[0]["role"] == "system" and messages[-1] == {"role": "user", "content": "how do I do x?"}


@pytest.mark.asyncio
async def test_a_router_candidate_is_offered_and_the_answer_follows_the_tool_result():
    model = Model(_reply(tool_calls=[_call()]), _reply("Context7 says: use the v15 API."))
    used = []

    async def use_tool(server, name, args):
        used.append((server, name, args))
        return json.dumps({"server": server, "tool": name, "result": "v15 notes", "is_error": False})

    out = await room_agent.converse("latest next.js docs?", [], model_call=model, find_tools=_some_tools, use_tool=use_tool)
    assert out.text == "Context7 says: use the v15 API." and used == [("io.github.upstash/context7", "get_docs", {})]
    assert out.tools_used == ["io.github.upstash/context7/get_docs"]
    first_messages, first_tools = model.seen[0]
    assert "use_tool" in [t["function"]["name"] for t in first_tools]
    assert "context7" in first_messages[0]["content"].lower()                  # the router's short list is in the prompt
    tool_message = model.seen[1][0][-1]
    assert tool_message["role"] == "tool" and "v15 notes" in tool_message["content"]


@pytest.mark.asyncio
async def test_a_first_call_that_needs_consent_puts_the_link_in_the_answer():
    model = Model(_reply(tool_calls=[_call()]), _reply("I need your permission first."))
    link = "https://kell.example/tools/consent/abc"

    async def use_tool(server, name, args):
        return json.dumps({"consent_required": True, "server": server, "link": link})

    out = await room_agent.converse("read the docs", [], model_call=model, find_tools=_some_tools, use_tool=use_tool)
    assert out.consent_links == [link] and out.text == "I need your permission first."


@pytest.mark.asyncio
async def test_a_bad_tool_call_is_refused_as_text_and_the_model_continues():
    model = Model(_reply(tool_calls=[_call(server="", name="")]), _reply("I will answer without a tool."))
    out = await room_agent.converse("hi", [], model_call=model, find_tools=_some_tools, use_tool=_never)
    assert out.text == "I will answer without a tool."
    assert "REFUSED" in model.seen[1][0][-1]["content"]


@pytest.mark.asyncio
async def test_tool_rounds_stop_at_the_limit():
    replies = [_reply(tool_calls=[_call(cid=f"call_{i}")]) for i in range(room_agent.MAX_ROUNDS + 1)]
    model = Model(*replies)

    async def use_tool(server, name, args):
        return "still working"

    out = await room_agent.converse("loop please", [], model_call=model, find_tools=_some_tools, use_tool=use_tool)
    assert len(model.seen) == room_agent.MAX_ROUNDS + 1
    assert out.tools_used.count("io.github.upstash/context7/get_docs") == room_agent.MAX_ROUNDS


def test_transcript_keeps_the_room_in_order_and_marks_this_agent_as_the_assistant():
    events = [
        {"kind": "msg", "body": "hello", "sender": {"kind": "person", "handle": "@person", "name": "Asha"}},
        {"kind": "msg", "body": "hi Asha", "sender": {"kind": "agent", "handle": "@kell"}},
        {"kind": "system", "body": "Asha joined", "sender": {"kind": "person", "handle": "@person"}},
        {"kind": "msg", "body": "what now?", "sender": {"kind": "agent", "handle": "@bob/opencode"}},
    ]
    turns = room_agent.transcript(events)
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[0]["content"] == "Asha (@person): hello"
    assert turns[2]["content"] == "@bob/opencode (@bob/opencode): what now?"
