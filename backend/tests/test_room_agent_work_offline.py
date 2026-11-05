"""A task goes through discover_tools first (the first round is forced), and making something runs on the cheapest model through call_model,
with the result delivered as a canvas block (app/services/room_agent.py). No network: fake model, router and cheap model."""
from __future__ import annotations

import json

import pytest

from app.services import room_agent
from tests.test_room_agent_offline import Model, _never, _reply, _some_tools


def _tc(name, **args):
    return {"id": "c_" + name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.asyncio
async def test_a_task_searches_first_then_the_cheap_model_makes_the_slides_and_they_land_in_a_canvas():
    model = Model(_reply(tool_calls=[_tc("discover_tools", need="make slides")]),
                  _reply(tool_calls=[_tc("call_model", prompt="one self-contained html deck about kell", canvas_title="Kell deck", kind="html")]),
                  _reply("Here are your slides."))
    asked: list[str] = []

    async def discover(need):
        asked.append(need)
        return json.dumps({"tools": [], "note": "no tool found for that"})

    async def cheap(prompt):
        asked.append(prompt)
        return "```html\n<!doctype html><section>one</section>\n```"

    out = await room_agent.converse("make aesthetic slides for me", [], model_call=model, find_tools=_some_tools, use_tool=_never,
                                    discover=discover, call_cheap=cheap)
    assert asked[0] == "make slides" and "html deck" in asked[1]
    assert out.tools_used == ["discover_tools", "call_model"]
    assert [t["function"]["name"] for t in model.seen[0][1]] == ["discover_tools"]          # the first round can only search
    assert out.text.startswith("Here are your slides.")
    assert '```canvas title="Kell deck" kind=html\n<!doctype html><section>one</section>\n```' in out.text   # the model's own fence is removed


@pytest.mark.asyncio
async def test_a_failed_cheap_model_is_text_the_agent_can_act_on():
    model = Model(_reply(tool_calls=[_tc("call_model", prompt="write it", canvas_title="X", kind="document")]), _reply("I could not make it."))

    async def cheap(prompt):
        raise RuntimeError("no connection available")

    out = await room_agent.converse("write a long memo about x", [], model_call=model, find_tools=_some_tools, use_tool=_never, call_cheap=cheap)
    assert "FAILED: call_model: no connection available" in model.seen[1][0][-1]["content"]
    assert "canvas" not in out.text


def test_small_talk_does_not_force_a_search():
    assert room_agent.wants_work("make aesthetic slides for me")
    assert not room_agent.wants_work("thanks!") and not room_agent.wants_work("hi") and not room_agent.wants_work("ok")


@pytest.mark.asyncio
async def test_a_request_to_make_something_must_run_a_tool_after_the_search_until_one_has_worked():
    model = Model(_reply(tool_calls=[_tc("discover_tools", need="slides")]),
                  _reply(tool_calls=[_tc("call_model", prompt="deck", canvas_title="D", kind="html")]),
                  _reply("Made it."))

    async def discover(need):
        return json.dumps({"tools": []})

    async def cheap(prompt):
        return "<!doctype html><section>x</section>"

    out = await room_agent.converse("make aesthetic slides for me", [], model_call=model, find_tools=_some_tools, use_tool=_never,
                                    discover=discover, call_cheap=cheap)
    first, second, third = (t for _m, t in model.seen[:3])
    assert [x["function"]["name"] for x in first] == ["discover_tools"] and not any(x.get("_required") for x in first)
    assert any(x.get("_required") for x in second)                      # after the search, some tool has to be called
    assert not any(x.get("_required") for x in third)                   # once call_model worked, the model may just answer
    assert out.text.startswith("Made it.")


@pytest.mark.asyncio
async def test_questions_and_tool_requests_are_not_forced_to_make_anything():
    assert not room_agent.makes_something("find a tool that converts a pdf to text")
    assert not room_agent.makes_something("summarise the main causes of the french revolution")
    assert room_agent.makes_something("write a python script that renames files by date")


@pytest.mark.asyncio
async def test_what_the_cheap_model_makes_goes_to_a_canvas_even_when_the_agent_gave_no_title():
    model = Model(_reply(tool_calls=[_tc("discover_tools", need="memo")]),
                  _reply(tool_calls=[_tc("call_model", prompt="write the memo")]),                  # no canvas_title, no kind
                  _reply("Here is the memo."))

    async def discover(need):
        return json.dumps({"tools": []})

    memo = "# Four Day Week Memo\n\n" + "A paragraph of the memo. " * 60

    async def cheap(prompt):
        return memo

    out = await room_agent.converse("write a short memo about a four day week", [], model_call=model, find_tools=_some_tools, use_tool=_never,
                                    discover=discover, call_cheap=cheap)
    assert '```canvas title="Four Day Week Memo" kind=document' in out.text and "A paragraph of the memo." in out.text


def test_html_or_a_long_piece_the_agent_wrote_itself_is_moved_into_a_canvas():
    page = "```html\n<!doctype html><html><head><title>Crumb</title></head><body>" + "<p>bread</p>" * 40 + "</body></html>\n```"
    out = room_agent.canvasify("Here is your page.\n\n" + page, False)
    assert 'canvas title="Crumb" kind=html' in out and "```html" not in out and out.startswith("Here is your page.")
    long_memo = "# Plan\n\n" + "Words about the plan. " * 80
    assert 'canvas title="Plan" kind=document' in room_agent.canvasify(long_memo, True)
    assert room_agent.canvasify(long_memo, False) == long_memo                         # an ordinary long answer to a question stays in the chat
    already = '```canvas title="X" kind=html\n<p>x</p>\n```'
    assert room_agent.canvasify(already, True) == already


@pytest.mark.asyncio
async def test_with_a_canvas_open_a_change_is_answered_directly_with_no_forced_search_or_tool():
    model = Model(_reply('Shortened.\n\n```canvas title="Plan" kind=document\n# Plan\nShort.\n```'))
    out = await room_agent.converse('@kell Edit "Plan": make it shorter', [], model_call=model, find_tools=_some_tools, use_tool=_never, skip_search=True)
    names = [t["function"]["name"] for t in model.seen[0][1]]
    assert names == ["discover_tools", "use_tool", "call_model"] and not any(t.get("_required") for t in model.seen[0][1])
    assert out.tools_used == [] and 'canvas title="Plan"' in out.text


@pytest.mark.asyncio
async def test_a_short_piece_the_cheap_model_writes_stays_in_the_chat_unless_a_document_was_asked_for():
    """2026-11-05 basics check: a ten-line Python function landed in a canvas. A short piece now goes back to the model for its chat answer."""
    fn = "def reverse(s):\n    return s[::-1]\n"

    async def cheap(prompt):
        return fn

    for ask, in_canvas in (("please could you make a python function that reverses a string", False),
                           ("make a short document with a python function that reverses a string", True)):
        model = Model(_reply(tool_calls=[_tc("call_model", prompt="the function", canvas_title="Reverse", kind="code")]), _reply("Here it is."))
        out = await room_agent.converse(ask, [], model_call=model, find_tools=_some_tools, use_tool=_never, call_cheap=cheap, skip_search=True)
        assert ("```canvas" in out.text) is in_canvas, ask
        if not in_canvas:
            assert "def reverse" in model.seen[1][0][-1]["content"]                 # the model got the code back to put in its answer


@pytest.mark.asyncio
async def test_a_plain_question_skips_the_router_the_judge_and_the_forced_search():
    """2026-11-05: 9 to 18 s before the first word on short replies, most of it the router, the judge and a forced search round."""
    called: list[str] = []

    async def find_tools(text):
        called.append("router")
        return []

    async def classify(text, context):
        called.append("judge")
        return {"kind": "tool", "tool": None}

    model = Model(_reply("It is 5 November 2026."))
    out = await room_agent.converse("What is today's date?", [], model_call=model, find_tools=find_tools, use_tool=_never, classify=classify)
    assert called == [] and out.text == "It is 5 November 2026."
    assert not any(t.get("_required") for t in model.seen[0][1])


def test_what_needs_the_outside_world_keeps_the_full_path():
    plain = ["What is today's date?", "explain recursion simply", "write a python function that reverses a string",
             "How do I center a div?", "When was the Eiffel Tower built?", "Convert 5 km to miles", "what is 2+2?"]
    full = ["what is one top news headline in India today?", "What is the btc price now?", "what does this PDF say?",
            "what did we decide in #design?", "who won the match last night", "what is the latest iPhone", "what is the price of gold",
            "what is the weather in Pune", "convert 100 usd to inr", "make me slides about cats", "write an essay on climate",
            "what tools can turn a pdf into text?", "what is in this picture?", "draw a fox logo"]
    assert all(room_agent.plainly_answerable(t) for t in plain), [t for t in plain if not room_agent.plainly_answerable(t)]
    assert not any(room_agent.plainly_answerable(t) for t in full), [t for t in full if room_agent.plainly_answerable(t)]
