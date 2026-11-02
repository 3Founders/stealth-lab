"""Shared room documents (app/services/room_docs.py): the merge of two edits made at the same time, saves and versions, who may delete,
and canvas blocks an agent writes. No database: a fake pool that understands only the statements the service sends."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.services import room_docs as rd
from app.services.rooms import RoomError

ROOM = uuid.uuid4()
BASE = "# Plan\n\nIntro text.\n\nStep one.\n\nStep two.\n"


class Pool:
    def __init__(self, members=None):
        self.members = members or {"alice": "owner", "bob": "member"}
        self.docs = {}
        self.versions = []

    async def fetchrow(self, sql, *a):
        if "FROM rooms r JOIN room_members" in sql:
            return {"id": ROOM, "my_role": self.members[a[1]], "title": "#x", "mode": "open", "created_at": None, "next_seq": 0} if a[1] in self.members else None
        if sql.startswith("SELECT * FROM room_docs WHERE id"):
            d = self.docs.get(a[0])
            return d if d and not d.get("deleted_at") else None
        if sql.startswith("SELECT * FROM room_docs WHERE room_id = $1 AND lower(title)"):
            return next((d for d in self.docs.values() if d["room_id"] == a[0] and d["title"].lower() == a[1].lower() and not d.get("deleted_at")), None)
        if sql.startswith("UPDATE room_docs SET body"):
            d = self.docs.get(a[0])
            if d and d["version"] == a[4]:
                d.update(body=a[1], version=a[2], updated_by=a[3], updated_at=datetime.now(timezone.utc))
                return {"version": a[2]}
            return None
        if sql.startswith("SELECT body FROM room_doc_versions"):
            return next(({"body": v["body"]} for v in self.versions if v["doc_id"] == a[0] and v["version"] == a[1]), None)
        if sql.startswith("SELECT 1 FROM room_doc_versions WHERE doc_id = $1 AND src"):
            return next((1 for v in self.versions if v["doc_id"] == a[0] and v["src"] == a[1]), None)

    async def fetchval(self, sql, *a):
        return sum(1 for d in self.docs.values() if d["room_id"] == a[0] and not d.get("deleted_at"))

    async def fetch(self, sql, *a):
        if "FROM users" in sql:
            return []
        if sql.startswith("SELECT version, by_handle"):
            return [{"version": v["version"], "by_handle": v["by_handle"], "note": v["note"], "at": None, "size": len(v["body"])} for v in sorted(self.versions, key=lambda v: -v["version"]) if v["doc_id"] == a[0]]
        return []

    async def execute(self, sql, *a):
        if sql.startswith("INSERT INTO room_docs"):
            if any(d["room_id"] == a[1] and d["title"].lower() == a[2].lower() and not d.get("deleted_at") for d in self.docs.values()):
                raise Exception("duplicate key value violates unique constraint room_docs_title_live")
            self.docs[a[0]] = {"id": a[0], "room_id": a[1], "title": a[2], "kind": a[3], "body": a[4], "version": 1, "created_by": a[5], "updated_by": a[5],
                               "updated_at": datetime.now(timezone.utc), "deleted_at": None}
        elif sql.startswith("INSERT INTO room_doc_versions"):
            if len(a) == 7:
                self.versions.append({"doc_id": a[1], "version": a[2], "body": a[3], "by_handle": a[4], "note": a[5], "src": a[6]})
            else:
                self.versions.append({"doc_id": a[1], "version": 1, "body": a[2], "by_handle": a[3], "note": "", "src": a[4]})
        elif sql.startswith("UPDATE room_docs SET deleted_at"):
            self.docs[a[0]]["deleted_at"] = datetime.now(timezone.utc)


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_edits_in_different_lines_both_apply():
    mine = BASE.replace("Step one.", "Step one, by me.")
    theirs = BASE.replace("Step two.", "Step two, by them.")
    merged, clash = rd.merge3(BASE, mine, theirs)
    assert merged == "# Plan\n\nIntro text.\n\nStep one, by me.\n\nStep two, by them.\n" and clash is False


def test_the_same_line_changed_twice_keeps_the_later_save_and_says_so():
    merged, clash = rd.merge3(BASE, BASE.replace("Step one.", "Mine."), BASE.replace("Step one.", "Theirs."))
    assert "Mine." in merged and "Theirs." not in merged and clash is True


def test_adding_at_the_end_does_not_lose_a_change_above():
    merged, clash = rd.merge3(BASE, BASE + "\nAdded.\n", BASE.replace("Step two.", "Step two, theirs."))
    assert merged.endswith("Step two, theirs.\n\nAdded.\n") and clash is False


def test_an_unchanged_side_takes_the_other():
    assert rd.merge3(BASE, BASE, "x") == ("x", False)
    assert rd.merge3(BASE, "y", BASE) == ("y", False)
    assert rd.merge3(BASE, "z", "z") == ("z", False)


def test_create_save_and_versions():
    p = Pool()
    doc = run(rd.create_doc(p, "alice", str(ROOM), "Launch plan", "document", BASE))
    assert doc["version"] == 1
    with pytest.raises(RoomError):
        run(rd.create_doc(p, "alice", str(ROOM), "launch PLAN", "document", "x"))       # one title per room, any case
    r = run(rd.save_doc(p, "alice", str(ROOM), doc["id"], BASE + "More.\n", 1))
    assert r["version"] == 2 and r["merged"] is False
    assert run(rd.save_doc(p, "alice", str(ROOM), doc["id"], BASE + "More.\n", 2))["unchanged"] is True


def test_two_people_saving_from_the_same_version_are_merged_and_nothing_is_lost():
    p = Pool()
    doc = run(rd.create_doc(p, "alice", str(ROOM), "Plan", "document", BASE))
    run(rd.save_doc(p, "alice", str(ROOM), doc["id"], BASE.replace("Step two.", "Step two, alice."), 1))          # alice saves version 2
    r = run(rd.save_doc(p, "bob", str(ROOM), doc["id"], BASE.replace("Step one.", "Step one, bob."), 1))          # bob still edited version 1
    assert r["merged"] is True and r["version"] == 3 and r["clash"] is False
    assert r["body"] == "# Plan\n\nIntro text.\n\nStep one, bob.\n\nStep two, alice.\n"
    assert [v["version"] for v in p.versions] == [1, 2, 3]                                                         # alice's version is still there


def test_a_stranger_cannot_read_and_only_the_maker_or_a_manager_can_delete():
    p = Pool()
    doc = run(rd.create_doc(p, "bob", str(ROOM), "Mine", "document", "x"))
    with pytest.raises(RoomError) as e:
        run(rd.list_docs(p, "mallory", str(ROOM)))
    assert e.value.status == 404
    assert run(rd.delete_doc(p, "alice", str(ROOM), doc["id"])) is True                    # alice owns the room
    p2 = Pool()
    d2 = run(rd.create_doc(p2, "alice", str(ROOM), "Hers", "document", "x"))
    with pytest.raises(RoomError) as e2:
        run(rd.delete_doc(p2, "bob", str(ROOM), d2["id"]))                                  # bob is a plain member
    assert e2.value.status == 403


def test_secrets_are_cut_out_of_a_document():
    p = Pool()
    doc = run(rd.create_doc(p, "alice", str(ROOM), "Keys", "document", "token sk-proj-abcdefghijklmnop1234567890 here AKIAABCDEFGHIJKLMNOP"))
    assert "AKIAABCDEFGHIJKLMNOP" not in p.docs[uuid.UUID(doc["id"])]["body"]


def test_canvas_blocks_in_a_message_become_versions_once():
    p = Pool()
    text = 'Here you go.\n\n```canvas title="Launch plan" kind=document\n# Launch\n\nStep one.\n```\n'
    assert rd.canvas_blocks(text) == [("Launch plan", "document", "# Launch\n\nStep one.")]
    assert run(rd.capture(p, ROOM, "alice", "@kell", "msg-1", text)) == 1
    assert run(rd.capture(p, ROOM, "alice", "@kell", "msg-1", text)) == 0                  # the same message again adds nothing
    text2 = text.replace("Step one.", "Step one, revised.")
    assert run(rd.capture(p, ROOM, "alice", "@kell", "msg-2", text2)) == 1                 # a new message with the same title is a new version
    d = next(iter(p.docs.values()))
    assert d["version"] == 2 and "revised" in d["body"]


def test_who_is_editing_is_shown_to_others_briefly_and_never_stored_in_the_document():
    p = Pool()
    doc = run(rd.create_doc(p, "alice", str(ROOM), "Plan", "document", BASE))
    run(rd.get_doc(p, "bob", str(ROOM), doc["id"]))                                   # bob has it open
    run(rd.save_doc(p, "bob", str(ROOM), doc["id"], BASE + "x\n", 1))                # and just typed
    seen = run(rd.get_doc(p, "alice", str(ROOM), doc["id"]))
    assert seen["present"] == ["Someone"] and seen["editing"] == ["Someone"]
    assert "editing" not in p.docs[uuid.UUID(doc["id"])]                              # presence is not part of the document
