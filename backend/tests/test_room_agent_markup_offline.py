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


def test_the_tools_are_offered_on_every_turn_and_the_prompt_says_how_to_use_them() -> None:
    assert [t["function"]["name"] for t in ra.tool_specs([])] == ["discover_tools", "use_tool"]
    for cands in ([], [{"server": "web", "about": "search"}]):
        s = ra._system(cands)
        assert ra.TOOLS_ON in s and "discover_tools" in s
    assert "web: search" in ra._system([{"server": "web", "about": "search"}])


def test_a_call_written_in_any_known_markup_form_is_dropped() -> None:
    for form in ("<call:use_tool server=\"kell\" tool=\"search\" arguments='{\"query\": \"pdf\"}'/>", '<call>x</call>',
                 '<function_call name="x"/>', '<tool_code>print(1)</tool_code>'):
        assert ra.strip_tool_markup(f"Ok. {form} Done.") == "Ok.  Done."


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


def test_the_instructions_a_person_gave_an_agent_reach_its_prompt() -> None:
    s = ra._system([], "@reviewer", "Review my pull requests. Be blunt.")
    assert "Review my pull requests. Be blunt." in s and s.startswith("You are @reviewer, an instance of kell,")
    assert "asked you to do this" not in ra._system([], "@reviewer", "   ")
    assert len(ra._system([], "@x", "a" * 5000)) < len(ra._system([], "@x")) + 1700


def test_discover_tools_runs_the_search_and_a_server_listing_and_use_tool_still_runs() -> None:
    seen: list[tuple] = []

    async def discover(need):
        seen.append(("discover", need)); return '{"external": {"tools": [{"server": "pdf-tools", "via_kell": true}]}}'

    async def tools_of(server):
        seen.append(("tools_of", server)); return '{"tools": [{"name": "merge"}]}'

    async def use_tool(server, name, args):
        seen.append(("use", server, name, args)); return "merged"

    def call(fn_name, **args):
        import json
        return {"id": "1", "function": {"name": fn_name, "arguments": json.dumps(args)}}

    async def go():
        used: list[str] = []
        r = []
        for c in (call("discover_tools", need="edit a pdf"), call("discover_tools", server="pdf-tools"),
                  call("use_tool", server="pdf-tools", name="merge", arguments={"a": 1}), call("use_tool", name="pdf-tools/merge"),
                  call("discover_tools"), call("use_tool", name="merge")):
            r.append(await ra._run_call(c, use_tool=use_tool, discover=discover, tools_of=tools_of, used=used))
        return r, used

    r, used = asyncio.run(go())
    assert seen == [("discover", "edit a pdf"), ("tools_of", "pdf-tools"), ("use", "pdf-tools", "merge", {"a": 1}), ("use", "pdf-tools", "merge", {})]
    assert r[2] == "merged" and r[4].startswith("REFUSED") and r[5].startswith("REFUSED")
    assert used == ["discover_tools", "discover_tools", "pdf-tools/merge", "pdf-tools/merge", "discover_tools"]


def test_the_agent_is_told_todays_date_in_the_persons_time_zone() -> None:
    from datetime import datetime, timezone

    moment = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert ra.now_line("", moment) == "Today is Monday, 02 November 2026; the time is 23:30 (UTC)."
    assert ra.now_line("Asia/Kolkata", moment) == "Today is Tuesday, 03 November 2026; the time is 05:00 (Asia/Kolkata)."
    assert "(UTC)" in ra.now_line("Not/AZone", moment) and "(UTC)" in ra.now_line("x" * 80, moment)
    assert "Today is" in ra._system([], "@kell", "", "Asia/Kolkata")
