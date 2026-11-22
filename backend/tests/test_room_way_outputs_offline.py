"""A Way run's output becoming a Room file (services/room_files.py::adopt_way_outputs).

A room and a run keep files in different places — the room in Postgres, the run in object storage — so the bridge is
the one place the two meet. What it must guarantee: the room's safety rules still apply to bytes a Way produced
(a Way is written by a stranger too), and one refused file never loses the others.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import room_files


class FakePool:
    """Just enough of asyncpg for room_files.upload: the membership check, then one INSERT .. RETURNING *."""

    def __init__(self) -> None:
        self.inserted: list[dict] = []

    async def fetchrow(self, sql: str, *args):
        if "FROM rooms" in sql or "room_members" in sql:
            return {"id": args[1] if len(args) > 1 else "room-1"}
        if "room_files WHERE room_id = $1 AND sha256" in sql:
            return None
        if sql.strip().startswith("INSERT INTO room_files"):
            row = {"id": "f1", "room_id": args[1], "uploader": args[2], "sha256": args[3], "size": args[4],
                   "mime": args[5], "name": args[6], "state": "clean", "scanned": False, "storage": "db",
                   "expires_at": None}
            self.inserted.append(row)
            return row
        return None

    async def fetchval(self, sql: str, *args):
        return 0


class FakeStore:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    async def get(self, key: str) -> bytes:
        if key not in self.blobs:
            raise KeyError(key)
        return self.blobs[key]


@pytest.fixture(autouse=True)
def _no_membership_lookup(monkeypatch):
    """The membership check is another module's rule; this test is about the bridge."""
    async def _member(pool, sub, room_id):
        return {"id": room_id}
    monkeypatch.setattr(room_files, "_member_room", _member)


def test_a_run_file_becomes_a_room_file():
    pool = FakePool()
    store = FakeStore({"s3://b/1": b"# Review\n\nbody"})
    got = asyncio.run(room_files.adopt_way_outputs(
        pool, room_id="room-1", uploader="u1", run_id="r1",
        files=[{"name": "literaturereview.md", "object_key": "s3://b/1"}], store=store))
    assert len(got) == 1 and got[0]["name"] == "literaturereview.md"
    assert pool.inserted[0]["room_id"] == "room-1"


def test_a_missing_object_is_skipped_without_losing_the_others():
    pool = FakePool()
    store = FakeStore({"s3://b/ok": b"fine"})
    got = asyncio.run(room_files.adopt_way_outputs(
        pool, room_id="room-1", uploader="u1", run_id="r1",
        files=[{"name": "gone.md", "object_key": "s3://b/missing"},
               {"name": "ok.md", "object_key": "s3://b/ok"}], store=store))
    assert any(g.get("skipped") for g in got)
    assert any(g.get("name") == "ok.md" and not g.get("skipped") for g in got)


def test_a_program_produced_by_a_way_is_still_refused_by_the_room():
    """Open contributions mean the Way's author is a stranger; the room's rules are not relaxed for them."""
    pool = FakePool()
    store = FakeStore({"s3://b/x": b"#!/bin/sh\nrm -rf /\n"})
    got = asyncio.run(room_files.adopt_way_outputs(
        pool, room_id="room-1", uploader="u1", run_id="r1",
        files=[{"name": "evil.sh", "object_key": "s3://b/x"}], store=store))
    assert got and got[0].get("skipped"), "a shell script must not be adopted as a room file"
    assert pool.inserted == []


def test_nothing_to_adopt_is_not_an_error():
    got = asyncio.run(room_files.adopt_way_outputs(
        FakePool(), room_id="room-1", uploader="u1", run_id="r1", files=[], store=FakeStore({})))
    assert got == []
