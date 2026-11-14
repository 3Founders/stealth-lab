"""What a claimed task carries from its room (2026-11-14): the room id, the last messages, and the files they name, so a
connected agent can follow a conversation and open a picture without asking the person to repeat it. No database or model."""
from __future__ import annotations

import asyncio

import pytest

from app.services import delegate as dg

ROOM = "11111111-1111-1111-1111-111111111111"
FILE = "22222222-2222-2222-2222-222222222222"


def test_task_json_carries_the_room_id():
    row = {
        "id": "t1", "agent_id": "a1", "room": "#general", "from_handle": "@alice", "body": "hello",
        "locators": "[]", "depth": 0, "status": "queued", "created_at": None, "claimed_at": None,
        "finished_at": None, "result_text": None, "result_check": None, "room_id": ROOM,
    }
    task = dg._task_json(row)
    assert task["room_id"] == ROOM and task["posted"] is True


def test_a_claimed_task_gets_history_and_file_metadata(monkeypatch):
    events = [
        {"kind": "msg", "seq": 4, "body": "the budget is 4200", "sender": {"handle": "@alice"}},
        {"kind": "msg", "seq": 5, "body": f"look [[file:{FILE}|shot.png|5|image/png]]", "sender": {"handle": "@bob"}},
        {"kind": "system", "seq": 6, "body": "joined", "sender": {"handle": "@bob"}},
    ]

    async def recent(pool, sub, room, limit):
        assert (sub, room, limit) == ("alice", ROOM, dg.TASK_HISTORY)
        return events

    monkeypatch.setattr("app.services.rooms.recent_events", recent)
    task = {"room_id": ROOM, "text": "what is in the picture?"}
    asyncio.run(dg._attach_room_context(object(), "alice", task))

    assert [h["seq"] for h in task["history"]] == [4, 5]                 # system events are not chat
    assert task["history"][0]["text"] == "the budget is 4200"
    assert "room_read" in task["history_note"] and "before=4" in task["history_note"]
    assert task["files"] == [{"id": FILE, "name": "shot.png", "mime": "image/png"}]
    assert "room_file" in task["files_note"]
