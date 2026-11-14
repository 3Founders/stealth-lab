"""room_read(before=...) reads older messages (2026-11-14), the way a connected agent asks for more context than its task's
default history. No database or MCP client."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.mcp_server import server


class Ctx:
    request_context = SimpleNamespace(lifespan_context={"pool": object()})


def test_before_reads_older_messages(monkeypatch):
    async def seen(ctx):
        return None

    async def earlier(pool, sub, room, before, limit, query):
        assert (sub, room, before, limit, query) == ("alice", "room-1", 10, 5, "budget")
        return [{"seq": 7 + i, "kind": "msg", "body": f"message {i}"} for i in range(5)]

    monkeypatch.setattr(server, "_seen_agent", seen)
    monkeypatch.setattr(server, "_kell_owner", lambda: "alice")
    monkeypatch.setattr("app.services.rooms.earlier_events", earlier)

    out = json.loads(asyncio.run(server.room_read(Ctx(), "room-1", before=10, limit=5, query="budget")))
    assert out["events"][0]["seq"] == 7
    assert out["next"] == 7 and out["more"] is True


def test_a_task_picture_comes_back_as_image_content(monkeypatch):
    async def fetch(pool, sub, room, file_id):
        assert (sub, room, file_id) == ("alice", "room-1", "pic")
        return {"mime": "image/png"}, b"\x89PNG fake"

    monkeypatch.setattr(server, "_kell_owner", lambda: "alice")
    monkeypatch.setattr("app.services.room_files.fetch", fetch)

    task = {"room_id": "room-1", "files": [{"id": "pic", "name": "shot.png", "mime": "image/png"},
                                           {"id": "doc", "name": "a.pdf", "mime": "application/pdf"}]}
    images = asyncio.run(server._task_images(Ctx(), task))
    assert len(images) == 1                                             # the PDF is text, not an image block
