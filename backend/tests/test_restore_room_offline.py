"""Restoring a deleted room must restore its membership too (scripts/restore_room.py).

`delete_room` deletes room_members outright and keeps only the rooms row as a tombstone, so clearing `deleted_at`
alone returns a room that `list_rooms` never shows -- its owner is told "this room was not found on your account"
about a room sitting right there. This pins the rule without a database: the shape of the row restored must match
what `create_room` writes, which is what makes the room visible again.
"""
from __future__ import annotations

import pathlib
import re

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "restore_room.py"


def test_restoring_re_adds_the_owner_membership_not_just_undeletes():
    src = SCRIPT.read_text(encoding="utf-8")
    # The un-delete ...
    assert re.search(r"UPDATE rooms SET deleted_at = NULL", src), "the room must be un-deleted"
    # ... and the row that makes it a room for its owner, in the shape create_room writes.
    assert re.search(r"INSERT INTO room_members \(id, room_id, member_sub, role, added_by\)", src)
    assert "'owner'" in src


def test_it_refuses_someone_else_s_room():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "belongs to" in src and "refusing" in src, "restoring must never touch another person's room"


def test_it_is_a_dry_run_unless_apply_is_passed():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "--apply" in src
    assert "dry run" in src


def test_it_admits_what_it_cannot_bring_back():
    """The messages went with the room; claiming otherwise would be worse than the notice being slow to clear."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "not recovered" in src
