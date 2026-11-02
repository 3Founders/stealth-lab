"""@kell must never post tool-call markup as its answer (seen in a room: a model printed <invoke name="use_tool"> as text)."""
from __future__ import annotations

import asyncio

from app.services import room_agent as ra

CALL = '<invoke name="use_tool"><parameter name="server">web</parameter></invoke>'


def test_strip_removes_a_call_written_as_text() -> None:
    assert ra.strip_tool_markup(f"Sure.\n\n{CALL}\n\nDone.") == "Sure.\n\n\n\nDone."
    assert ra.strip_tool_markup(f"<function_calls>{CALL}</function_calls>") == ""
    assert ra.strip_tool_markup("a < b and c > d") == "a < b and c > d"          # plain angle brackets stay
    assert ra.strip_tool_markup("open <invoke name=") == "open"                  # an unclosed block is dropped


def test_filter_holds_back_a_half_tag_across_chunks() -> None:
    f = ra.MarkupFilter()
    out = "".join(f.feed(p) for p in ["Hello <inv", "oke name=\"x\">", "..</inv", "oke> there <3 ok"]) + f.flush()
    assert out == "Hello  there <3 ok"


def test_system_prompt_says_no_tools_when_none_offered() -> None:
    assert ra.TOOLS_OFF in ra._system([])
    assert "use_tool" not in ra._system([]).split(ra.TOOLS_OFF)[1]
    assert ra.TOOLS_ON in ra._system([{"server": "web", "about": "search"}])


def test_stream_answer_drops_markup_but_keeps_prose() -> None:
    async def model_stream(messages, tools):
        for piece in ["Looking. ", "<invoke name=\"use_tool\">", "x</invoke>", "Nothing to run."]:
            yield {"choices": [{"delta": {"content": piece}}]}

    async def find_tools(_):
        return []

    async def use_tool(*_a):
        raise AssertionError("no tool should run")

    async def go():
        deltas, final = [], None
        async for kind, v in ra.stream_answer("hi", [], model_stream=model_stream, find_tools=find_tools, use_tool=use_tool):
            if kind == "delta":
                deltas.append(v)
            else:
                final = v
        return "".join(deltas), final

    shown, final = asyncio.run(go())
    assert "<invoke" not in shown and "invoke" not in final.text
    assert final.text == "Looking. Nothing to run."


def test_another_agent_is_an_instance_of_kell() -> None:
    s = ra._system([], "@scout")
    assert s.startswith("You are @scout, an instance of kell,") and "@kell, the built-in" not in s
    assert ra._system([], "@kell").startswith("You are @kell, the built-in agent")
    # its own earlier answers are its assistant turns; another instance's are not
    events = [{"kind": "msg", "body": "hi", "sender": {"kind": "agent", "handle": "@scout"}},
              {"kind": "msg", "body": "yo", "sender": {"kind": "agent", "handle": "@kell"}}]
    assert [t["role"] for t in ra.transcript(events, "@scout")] == ["assistant", "user"]
