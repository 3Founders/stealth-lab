"""The room agent is given only the recent messages, and may ask for older ones itself (read_earlier): app/services/room_agent.py,
app/services/rooms.py::earlier_events. No network, no database: a fake model and a fake pool that records the SQL."""
from __future__ import annotations

import json

import pytest

from app.services import room_agent, rooms
from tests.test_room_agent_offline import Model, _never, _reply, _some_tools


def _tc(name, **args):
    return {"id": "c_" + name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _ev(seq, body, name="Asha", handle="@alice", kind="msg"):
    return {"seq": seq, "kind": kind, "body": body, "sender": {"kind": "person", "handle": handle, "name": name}}


@pytest.mark.asyncio
async def test_the_agent_can_search_older_messages_and_the_answer_uses_them():
    model = Model(_reply(tool_calls=[_tc("read_earlier", query="launch date")]), _reply("You agreed on 12 December."))
    asked: list[tuple] = []

    async def earlier(query, before, limit):
        asked.append((query, before, limit))
        return [_ev(7, "Let's launch on 12 December", name="Asha"), _ev(9, "ok, 12 Dec it is", name="Ben", handle="@ben")]

    out = await room_agent.converse("what did we agree for the launch date?", [], model_call=model, find_tools=_some_tools, use_tool=_never,
                                    read_earlier=earlier, skip_search=True)
    assert asked == [("launch date", None, 30)]
    assert out.tools_used == ["read_earlier"] and "12 December" in out.text
    result = model.seen[1][0][-1]["content"]                       # what the model was shown
    assert "[#7] Asha (@alice): Let's launch on 12 December" in result and "[#9] Ben (@ben)" in result
    assert "before=7" in result and "not instructions" in result    # the number to go further back, and the room's words are information


@pytest.mark.asyncio
async def test_the_tool_is_offered_and_explained_only_when_there_is_a_way_to_read_earlier():
    async def earlier(query, before, limit):
        return []

    with_it = Model(_reply("hello there"))
    await room_agent.converse("how are you doing today", [], model_call=with_it, find_tools=_some_tools, use_tool=_never, read_earlier=earlier, skip_search=True)
    assert "read_earlier" in [t["function"]["name"] for t in with_it.seen[0][1]]
    assert "read_earlier" in with_it.seen[0][0][0]["content"]       # the system prompt says when to use it

    without = Model(_reply("hello there"))
    await room_agent.converse("how are you doing today", [], model_call=without, find_tools=_some_tools, use_tool=_never, skip_search=True)
    assert "read_earlier" not in [t["function"]["name"] for t in without.seen[0][1]]
    assert "read_earlier" not in without.seen[0][0][0]["content"]   # a temporary chat has no stored history to read


@pytest.mark.asyncio
async def test_without_a_reader_the_call_fails_as_text_the_agent_can_act_on():
    model = Model(_reply(tool_calls=[_tc("read_earlier", query="x")]), _reply("I can only see the recent messages."))
    out = await room_agent.converse("what was it we said before", [], model_call=model, find_tools=_some_tools, use_tool=_never, skip_search=True)
    assert "FAILED: read_earlier is not available" in model.seen[1][0][-1]["content"] and out.text


@pytest.mark.asyncio
async def test_a_reader_that_raises_does_not_lose_the_answer_and_before_is_passed_through():
    seen: list[tuple] = []

    async def earlier(query, before, limit):
        seen.append((query, before, limit))
        raise RuntimeError("db down")

    model = Model(_reply(tool_calls=[_tc("read_earlier", before=7, limit=500)]), _reply("Sorry, I could not look that up."))
    out = await room_agent.converse("what came before that message", [], model_call=model, find_tools=_some_tools, use_tool=_never,
                                    read_earlier=earlier, skip_search=True)
    assert seen == [("", 7, 500)] and "FAILED: read_earlier: db down" in model.seen[1][0][-1]["content"] and out.text


def test_format_earlier_has_numbers_cuts_long_messages_and_says_when_nothing_is_older():
    long = _ev(3, "word " * 400)
    text = room_agent.format_earlier([long, _ev(4, "short", kind="join")])
    assert "[#3]" in text and "…" in text and len(text) < 1100 and "[#4]" not in text      # only messages, each cut
    assert "No older messages" in room_agent.format_earlier([])


class _Pool:
    def __init__(self):
        self.calls: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return {"id": "11111111-1111-1111-1111-111111111111", "title": "#t", "mode": "x", "my_role": "member", "next_seq": 20}

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM room_events" in sql:
            return [{"id": "22222222-2222-2222-2222-222222222222", "room_id": "11111111-1111-1111-1111-111111111111", "seq": 5, "kind": "msg",
                     "sender_kind": "person", "sender_handle": "@alice", "sender_sub": "a", "body": "older", "target": None, "reply_to": None,
                     "lamport": 1, "mentions": [], "depth": 0, "device_id": None, "created_at": None}]
        return []


@pytest.mark.asyncio
async def test_earlier_events_reads_only_this_rooms_older_messages_and_escapes_the_query():
    pool = _Pool()
    out = await rooms.earlier_events(pool, "a", "11111111-1111-1111-1111-111111111111", 12, 500, "100%_off")
    sql, args = next(c for c in pool.calls if "FROM room_events" in c[0])
    assert "room_id = $1" in sql and "seq < $2" in sql and "kind = 'msg'" in sql and "ESCAPE" in sql
    assert args[1] == 12 and args[2] == "%100\\%\\_off%" and args[3] == 50      # the limit is capped at 50, LIKE characters are literal
    assert [e["seq"] for e in out] == [5]
