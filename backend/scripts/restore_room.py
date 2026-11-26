"""Restore a deleted room, including the membership `delete_room` removed.

Why this exists: `delete_room` deletes the rooms row's children outright -- room_members, room_events,
room_invites, room_reads -- and keeps only the rooms row as a tombstone. So `UPDATE rooms SET deleted_at = NULL` on
its own produces a room nobody is a member of, which `list_rooms` then never shows: the owner sees "this room was
not found on your account" about a room that is right there in the database (2026-11-26, room 01a208c2).

This does the two things an un-delete needs, and says plainly what it cannot bring back:

  * clears `deleted_at`
  * re-adds the owner's membership, in the same shape `create_room` writes

The room's messages, invites and read-state were deleted with it and are NOT recovered. Its runs are untouched --
`way_runs` is not a child of the delete, so a Way's output and files survive.

Usage (dry run unless --apply):

    python scripts/restore_room.py <room-id> --owner <sub> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _dsn() -> str:
    from app.config import settings

    return settings.require("database_url").split("?", 1)[0]


async def restore(room_id: str, owner: str, *, apply: bool) -> int:
    import asyncpg

    from app.db.session import _init_connection
    from app.services import rooms as rm
    from app.utils.ids import uuid7

    pool = await asyncpg.create_pool(_dsn(), init=_init_connection, min_size=1, max_size=2, timeout=60)
    try:
        room = await pool.fetchrow(
            "SELECT id::text AS id, owner_sub, title, deleted_at FROM rooms WHERE id = $1::uuid", room_id)
        if room is None:
            print(f"no room {room_id}")
            return 2
        if room["owner_sub"] != owner:
            print(f"room {room_id} belongs to {room['owner_sub']}, not {owner} -- refusing")
            return 2

        member = await pool.fetchrow(
            "SELECT role FROM room_members WHERE room_id = $1::uuid AND member_sub = $2", room_id, owner)
        print(f"room      : {room['id']}  {room['title']}")
        print(f"deleted   : {room['deleted_at'] is not None}")
        print(f"membership: {member['role'] if member else 'MISSING'}")
        if member and room["deleted_at"] is None:
            print("nothing to restore")
            return 0
        if not apply:
            print("\n(dry run: pass --apply to make these changes)")
            return 0

        await pool.execute("UPDATE rooms SET deleted_at = NULL WHERE id = $1::uuid", room_id)
        if not member:
            await pool.execute(
                "INSERT INTO room_members (id, room_id, member_sub, role, added_by) "
                "VALUES ($1, $2::uuid, $3, 'owner', $3) ON CONFLICT DO NOTHING",
                str(uuid7()), room_id, owner)
            print("re-added the owner's membership")

        listed = await rm.list_rooms(pool, owner)
        found = next((r for r in listed if r["id"] == room_id), None)
        print(f"\nrestored; the server now lists it: {found is not None}")
        if found:
            print(f"  kind={found['kind']}  title={found['title']}  display_name={found['display_name']!r}")
        print("\nNote: messages, invites and read-state were deleted when the room was; they are not recovered.")
        return 0 if found else 1
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Restore a deleted room, including its membership.")
    ap.add_argument("room_id")
    ap.add_argument("--owner", required=True, help="the owner's subject id")
    ap.add_argument("--apply", action="store_true", help="make the change (default is a dry run)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(restore(a.room_id, a.owner, apply=a.apply)))


if __name__ == "__main__":
    main()
