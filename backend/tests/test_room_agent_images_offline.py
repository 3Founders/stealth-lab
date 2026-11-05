"""A picture a tool makes (kell/images) reaches the room: it is stored as a room file and the posted answer carries its card. Before this the picture
was dropped ("images stay out of a room message for now") and the model still told the person it had drawn one (2026-11-05 basics check).
No network, no database: a fake model, a fake tool that "makes" a picture, and a fake file store."""
from __future__ import annotations

import json

import pytest

from app.mcp_server import room_agent_routes
from tests.test_room_agent_route_offline import _body, _pool, _request, _route

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


def _tc(fname, **args):
    return {"id": "c_" + fname, "type": "function", "function": {"name": fname, "arguments": json.dumps(args)}}


def _reply(content="", tool_calls=None):
    return {"choices": [{"message": {"content": content, **({"tool_calls": tool_calls} if tool_calls else {})}}]}


class _Model:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    async def __call__(self, authorization, messages, tools):
        self.seen.append(messages)
        return self.replies.pop(0)


def test_file_token_is_the_card_format_the_page_reads():
    tok = room_agent_routes.file_token({"id": "11111111-1111-4111-8111-111111111111", "name": "kell-image-1.png", "size": 48, "mime": "image/png"})
    assert tok == "[[file:11111111-1111-4111-8111-111111111111|kell-image-1.png|48|image/png]]"
    assert "|" not in room_agent_routes.file_token({"id": "x", "name": "a|b\n.png", "size": 1, "mime": "image/png"}).split("|", 1)[1].split("|")[0]


@pytest.mark.asyncio
async def test_a_picture_the_tool_made_is_stored_and_its_card_is_in_the_posted_answer(monkeypatch):
    stored: list[tuple[str, bytes]] = []

    async def fake_upload(pool, sub, room_id, name, data):
        stored.append((name, data))
        return {"id": "22222222-2222-4222-8222-222222222222", "name": name, "size": len(data), "mime": "image/png"}

    monkeypatch.setattr("app.services.room_files.upload", fake_upload)

    async def use_tool(subject, server_name, tool, args):
        room_agent_routes.IMAGES.get().append((PNG, "png"))        # what server._room_agent_use does with an image the gateway returned
        return "Done: 1 picture made. kell attaches it to your answer by itself."

    model = _Model(_reply(tool_calls=[_tc("use_tool", server="kell/images", name="generate_image", arguments={"prompt": "a minimal fox logo"})]),
                   _reply("Here is a minimal orange fox logo."))
    pool = _pool()
    route = _route(model, pool=pool, use_tool=use_tool)
    r = await route(_request({"text": "hello, please draw a minimal fox logo for me", "canvas": None}))
    assert r.status_code == 200
    body = _body(r)
    assert body["posted"] is True
    assert body["text"].startswith("Here is a minimal orange fox logo.")
    assert "[[file:22222222-2222-4222-8222-222222222222|kell-image-1.png|48|image/png]]" in body["text"]
    assert stored == [("kell-image-1.png", PNG)]
    posted = [a for s, a in pool.conn.log if "WITH bump AS" in s]
    assert posted and "[[file:22222222" in str(posted[0])           # the card is in what went into the room


@pytest.mark.asyncio
async def test_a_picture_that_cannot_be_stored_does_not_lose_the_answer(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("storage down")

    monkeypatch.setattr("app.services.room_files.upload", broken)

    async def use_tool(subject, server_name, tool, args):
        room_agent_routes.IMAGES.get().append((PNG, "png"))
        return "Done: 1 picture made."

    model = _Model(_reply(tool_calls=[_tc("use_tool", server="kell/images", name="generate_image", arguments={"prompt": "fox"})]),
                   _reply("I drew a fox."))
    route = _route(model, pool=_pool(), use_tool=use_tool)
    body = _body(await route(_request({"text": "hello, please draw a fox for me"})))
    assert body["posted"] is True and body["text"].startswith("I drew a fox.") and "could not be attached" in body["text"]


@pytest.mark.asyncio
async def test_a_long_piece_says_it_is_being_written_before_the_cheap_model_returns():
    from app.services import room_agent as agent
    from tests.test_room_agent_offline import _never, _some_tools

    async def stream(messages, tools):
        if sum(1 for m in messages if m.get("role") == "tool") == 0:
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "call_model", "arguments": json.dumps({"prompt": "a long essay"})}}]}}]}
        else:
            yield {"choices": [{"delta": {"content": "Done."}}]}

    async def cheap(prompt):
        return "word " * 400

    seen = []
    async for kind, value in agent.stream_answer("please write a long essay about tea", [], model_stream=stream, find_tools=_some_tools, use_tool=_never,
                                                 call_cheap=cheap, skip_search=True):
        seen.append((kind, value))
    deltas = [v for k, v in seen if k == "delta"]
    assert deltas and deltas[0].startswith("Writing it now") and seen[-1][0] == "final"
    assert "Writing it now" not in seen[-1][1].text            # only the live bubble says it; the posted answer does not

