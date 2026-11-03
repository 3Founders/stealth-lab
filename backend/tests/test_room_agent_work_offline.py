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
