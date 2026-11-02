"""Room files: what is refused, how types are decided, and who can fetch (no database: a hand-rolled fake pool)."""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import room_files as rf
from app.services.rooms import RoomError

ROOM = uuid.uuid4()
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


class Pool:
    """Just enough of asyncpg's pool for room_files: one room, its members, and a files table."""

    def __init__(self, members=("alice",)):
        self.members, self.files = set(members), {}

    async def fetchrow(self, sql, *a):
        if "FROM rooms r JOIN room_members" in sql:
            return {"id": ROOM, "my_role": "member", "title": "#x", "mode": "open", "created_at": None, "next_seq": 0} if a[1] in self.members else None
        if "FROM room_files WHERE room_id = $1 AND sha256" in sql:
            return next((f for f in self.files.values() if f["sha256"] == a[1]), None)
        if sql.startswith("INSERT INTO room_files"):
            row = {"id": a[0], "room_id": a[1], "uploader": a[2], "sha256": a[3], "size": a[4], "mime": a[5], "name": a[6], "state": "clean",
                   "scanned": False, "data": a[7], "expires_at": a[8]}
            self.files[a[0]] = row
            return row
        if "FROM room_files WHERE id = $1" in sql:
            return self.files.get(a[0])

    async def fetchval(self, sql, *a):
        return len(self.files)


def run(coro):
    return asyncio.run(coro)


def test_the_type_comes_from_the_bytes_not_the_name() -> None:
    assert rf.sniff(PNG, "notes.txt") == "image/png"
    assert rf.sniff(b"%PDF-1.7 ...", "a.png") == "application/pdf"
    assert rf.sniff(b"hello world", "x.bin") == "text/plain"
    assert rf.sniff(b"PK\x03\x04....", "plan.docx").endswith("wordprocessingml.document")
    assert rf.sniff(bytes(range(256)) * 4, "x.dat") == "application/octet-stream"


def test_programs_and_empty_and_oversize_files_are_refused() -> None:
    for name, data in [("setup.exe", b"MZ" + b"\x00" * 50), ("run.sh", b"#!/bin/sh\necho hi"), ("a.bat", b"echo hi"), ("img.png", b"MZ\x90\x00")]:
        with pytest.raises(RoomError):
            rf.check(name, data)
    with pytest.raises(RoomError):
        rf.check("empty.txt", b"")
    with pytest.raises(RoomError) as e:
        rf.check("big.bin", b"a" * (rf.MAX_BYTES + 1))
    assert e.value.status == 413


def test_names_are_cleaned() -> None:
    assert rf.safe_name("../../etc/passwd") == "passwd"
    assert rf.safe_name("C:\\Users\\x\\report final.pdf") == "report final.pdf"
    assert rf.safe_name("bad<name>?.txt") == "badname.txt"
    assert rf.safe_name("   ") == "file"
    assert len(rf.safe_name("a" * 300 + ".pdf")) <= 120 and rf.safe_name("a" * 300 + ".pdf").endswith(".pdf")


def test_upload_stores_once_and_only_members_may_upload_or_fetch() -> None:
    pool = Pool()
    first = run(rf.upload(pool, "alice", str(ROOM), "pic.png", PNG))
    assert first["mime"] == "image/png" and first["inline"] is True and first["scanned"] is False and first["duplicate"] is False
    again = run(rf.upload(pool, "alice", str(ROOM), "same.png", PNG))
    assert again["id"] == first["id"] and again["duplicate"] is True and len(pool.files) == 1
    meta, data = run(rf.fetch(pool, "alice", str(ROOM), first["id"]))
    assert data == PNG and meta["sha256"] == hashlib.sha256(PNG).hexdigest()
    for who in ("mallory",):
        with pytest.raises(RoomError) as e:
            run(rf.upload(pool, who, str(ROOM), "x.png", PNG))
        assert e.value.status == 404
        with pytest.raises(RoomError) as e:
            run(rf.fetch(pool, who, str(ROOM), first["id"]))
        assert e.value.status == 404


def test_an_expired_file_is_gone() -> None:
    pool = Pool()
    f = run(rf.upload(pool, "alice", str(ROOM), "a.txt", b"hello"))
    pool.files[uuid.UUID(f["id"])]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(RoomError) as e:
        run(rf.fetch(pool, "alice", str(ROOM), f["id"]))
    assert e.value.status == 410


def test_only_media_and_pdf_are_inline_and_everything_is_nosniff() -> None:
    doc = {"name": 'a"b.docx', "mime": "application/zip", "inline": False}
    h = rf.download_headers(doc)
    assert h["Content-Disposition"].startswith("attachment") and h["Content-Type"] == "application/octet-stream"
    assert h["X-Content-Type-Options"] == "nosniff" and "sandbox" in h["Content-Security-Policy"]
    assert rf.download_headers({"name": "p.png", "mime": "image/png", "inline": True})["Content-Disposition"].startswith("inline")
    assert "svg" not in "".join(rf.INLINE) and "html" not in "".join(rf.INLINE)
