"""
Rooms that follow a person across devices (docs/rooms_design.md, rev 2; table definitions in db/157_rooms.sql).

A room is an append-only log of events shared by the people in it. Every device keeps a full copy and syncs with "give me the
events after seq N"; this module is the ordering and relay peer, not the only copy. The rules it keeps:

  * OPEN rooms: the text is stored, redacted at the edge like relay tasks (``redact_value``), so connectors such as Claude and
    ChatGPT can read and post. They are not end-to-end encrypted and the product says so. ``ciphertext`` exists for private
    rooms later and is never touched by redaction.
  * Membership is checked on every call, from the signed-in subject. A room you are not in answers "no such room" (404), the same
    as one that does not exist, so ids cannot be probed. The tables have RLS on with no policies, so this check is the whole gate.
  * Ordering: ``seq`` comes from ``rooms.next_seq``, bumped in the same statement that inserts the event. That statement holds the
    room row's lock until it commits, so a reader that has seen seq N can never miss a later commit with a lower seq.
  * Event ids come from the client, so a retried send is idempotent (the first write wins).
  * Depth: an agent-sent event may carry depth 0..4 (the delegation hop count); more is refused. A person's event is depth 0.

Honest limits: the stored text is the redacted text, so a device may show its own unredacted original while other devices show the
redacted copy. Per-person rate limiting is in memory (one worker). Nothing here calls out to anyone or holds a credential.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.delegate import mentions as _mentions
from app.services.trace_redaction import redact_value
from app.utils.ids import uuid7

MAX_BODY = 8000
MAX_ROOMS_PER_OWNER = 50
MAX_MEMBERS = 100
MAX_EVENTS_PER_ROOM = 100_000
MAX_BATCH = 50
MAX_PAGE = 500
INVITE_MAX_DAYS = 30
INVITE_MAX_USES = 100
EVENTS_PER_MINUTE = 120
TITLE_RE = re.compile(r"^#[a-z0-9][a-z0-9_.-]{0,78}$")
HANDLE_RE = re.compile(r"^@[a-z0-9][a-z0-9_.-]{0,38}$", re.I)
KINDS = ("msg", "edit", "delete", "react", "system", "task", "task_update", "note")
NEEDS_TEXT = ("msg", "edit", "system")
NEEDS_TARGET = ("edit", "delete", "react")
CODE_RE = re.compile(r"^rm1_([0-9a-f]{32})_([0-9a-f]{24})$")


class RoomError(Exception):
    """A request the rooms service refuses; ``status`` is the HTTP status the routes use for it."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


MANAGERS = ("owner", "admin")  # may invite people, remove plain members and delete other people's messages


def _clean(text: str, limit: int) -> str:
    matched: list[str] = []
    return redact_value((text or "").strip()[:limit], matched)


def _uuid(value: Any, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise RoomError(f"not a valid {what}", 400) from exc


def normalize_title(raw: Optional[str]) -> str:
    t = (raw or "").strip().lower().replace(" ", "-")
    t = t if t.startswith("#") else f"#{t}"
    if not TITLE_RE.match(t):
        raise RoomError("a room name is 1-79 letters, digits, dots, dashes or underscores")
    return t


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v else None


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _count(tag: Any) -> int:
    try:
        return int(str(tag).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


# ------------------------------------------------------------------ people's display names

async def _names(pool: Any, subs: list[str]) -> dict[str, str]:
    """Display names for subjects, where the account has one. A lookup that cannot run just means no names."""
    subs = sorted({s for s in subs if s})
    if not subs:
        return {}
    try:
        rows = await pool.fetch(
            "SELECT external_subject, display_name FROM users WHERE external_subject = ANY($1::text[]) "
            "AND display_name IS NOT NULL", subs)
    except Exception:  # noqa: BLE001 -- no users table here (a bare database) or it is unreachable: names are optional
        return {}
    return {r["external_subject"]: r["display_name"] for r in rows}


# ------------------------------------------------------------------ rooms

async def _member_room(pool: Any, sub: str, room_id: Any) -> Any:
    """The room row (with the caller's role) if the caller is in it, else 'no such room'."""
    rid = _uuid(room_id, "room id")
    row = await pool.fetchrow(
        "SELECT r.*, m.role AS my_role FROM rooms r JOIN room_members m ON m.room_id = r.id "
        "WHERE r.id = $1 AND r.deleted_at IS NULL AND m.member_sub = $2 AND m.removed_at IS NULL", rid, sub)
    if row is None:
        raise RoomError("no such room", 404)
    return row


def _room_json(row: Any, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out = {"id": str(row["id"]), "title": row["title"], "mode": row["mode"], "role": row["my_role"],
           "created_at": _iso(row["created_at"]), "last_seq": row["next_seq"]}
    out.update(extra or {})
    return out


async def create_room(pool: Any, sub: str, title: str, room_id: Optional[str] = None) -> dict[str, Any]:
    """Make a room you own. With the same `room_id` (or the same title) again you get the room you already made, so a device
    that retries or adopts an existing local room never creates a second one."""
    t = normalize_title(title)
    rid = _uuid(room_id, "room id") if room_id else uuid7()
    existing = await pool.fetchrow(
        "SELECT r.*, 'owner'::text AS my_role FROM rooms r WHERE r.owner_sub = $1 AND r.title = $2 AND r.deleted_at IS NULL", sub, t)
    if existing is not None:
        return _room_json(existing)
    taken = await pool.fetchval("SELECT 1 FROM rooms WHERE id = $1", rid)
    if taken:
        raise RoomError("that room id is already in use", 409)
    live = await pool.fetchval("SELECT count(*) FROM rooms WHERE owner_sub = $1 AND deleted_at IS NULL", sub)
    if live >= MAX_ROOMS_PER_OWNER:
        raise RoomError(f"at most {MAX_ROOMS_PER_OWNER} rooms per person; delete one first", 409)
    try:
        row = await pool.fetchrow(
            "WITH r AS (INSERT INTO rooms (id, owner_sub, title) VALUES ($1, $2, $3) RETURNING *), "
            "m AS (INSERT INTO room_members (id, room_id, member_sub, role, added_by) SELECT $4, r.id, $2, 'owner', $2 FROM r) "
            "SELECT r.*, 'owner'::text AS my_role FROM r", rid, sub, t, uuid7())
    except Exception as exc:  # noqa: BLE001 -- a concurrent create of the same title: asyncpg.UniqueViolationError
        if "rooms_owner_title_live" in str(exc) or "unique" in str(exc).lower():
            row = await pool.fetchrow(
                "SELECT r.*, 'owner'::text AS my_role FROM rooms r WHERE r.owner_sub = $1 AND r.title = $2 AND r.deleted_at IS NULL", sub, t)
            if row is not None:
                return _room_json(row)
        raise
    return _room_json(row, {"unread": 0, "members": 1, "last": None})


async def list_rooms(pool: Any, sub: str) -> list[dict[str, Any]]:
    """Every live room the caller is in, with unread counts and the latest line, for the room list on any device."""
    rows = await pool.fetch(
        "SELECT r.*, m.role AS my_role, COALESCE(rd.last_read_seq, 0) AS last_read_seq, "
        "(SELECT count(*) FROM room_events e WHERE e.room_id = r.id AND e.seq > COALESCE(rd.last_read_seq, 0) "
        "   AND e.sender_sub <> $1 AND e.kind IN ('msg', 'system')) AS unread, "
        "(SELECT count(*) FROM room_members mm WHERE mm.room_id = r.id AND mm.removed_at IS NULL) AS members "
        "FROM rooms r JOIN room_members m ON m.room_id = r.id AND m.member_sub = $1 AND m.removed_at IS NULL "
        "LEFT JOIN room_reads rd ON rd.room_id = r.id AND rd.member_sub = $1 "
        "WHERE r.deleted_at IS NULL ORDER BY r.created_at", sub)
    out = []
    for r in rows:
        last = await pool.fetchrow(
            "SELECT sender_handle, sender_kind, left(body, 140) AS preview, created_at FROM room_events "
            "WHERE room_id = $1 AND kind = 'msg' ORDER BY seq DESC LIMIT 1", r["id"])
        out.append(_room_json(r, {
            "last_read_seq": r["last_read_seq"], "unread": r["unread"], "members": r["members"],
            "last": None if last is None else {"handle": last["sender_handle"], "kind": last["sender_kind"],
                                               "preview": last["preview"], "at": _iso(last["created_at"])}}))
    return out


async def rename_room(pool: Any, sub: str, room_id: str, title: str) -> dict[str, Any]:
    room = await _member_room(pool, sub, room_id)
    if room["my_role"] != "owner":
        raise RoomError("only the owner can rename a room", 403)
    t = normalize_title(title)
    try:
        row = await pool.fetchrow(
            "UPDATE rooms SET title = $2 WHERE id = $1 AND deleted_at IS NULL RETURNING *, 'owner'::text AS my_role", room["id"], t)
    except Exception as exc:  # noqa: BLE001
        if "unique" in str(exc).lower():
            raise RoomError(f"you already have a room named {t}", 409) from exc
        raise
    return _room_json(row)


async def delete_room(pool: Any, sub: str, room_id: str) -> dict[str, Any]:
    """Owner only. Removes the room for everyone: its history, members and invites are deleted; the room row is kept as a
    tombstone so the id is not reused."""
    room = await _member_room(pool, sub, room_id)
    if room["my_role"] != "owner":
        raise RoomError("only the owner can delete a room", 403)
    rid = room["id"]
    await pool.execute("UPDATE rooms SET deleted_at = now() WHERE id = $1", rid)
    events = await pool.execute("DELETE FROM room_events WHERE room_id = $1", rid)
    await pool.execute("DELETE FROM room_invites WHERE room_id = $1", rid)
    await pool.execute("DELETE FROM room_reads WHERE room_id = $1", rid)
    await pool.execute("DELETE FROM room_members WHERE room_id = $1", rid)
    return {"deleted": str(rid), "events_removed": _count(events)}


# ------------------------------------------------------------------ members

async def list_members(pool: Any, sub: str, room_id: str) -> list[dict[str, Any]]:
    room = await _member_room(pool, sub, room_id)
    rows = await pool.fetch(
        "SELECT m.member_sub, m.agent_id, m.role, m.added_at, a.name AS agent_name FROM room_members m "
        "LEFT JOIN delegate_agents a ON a.id = m.agent_id WHERE m.room_id = $1 AND m.removed_at IS NULL ORDER BY m.added_at",
        room["id"])
    names = await _names(pool, [r["member_sub"] for r in rows if r["member_sub"]])
    return [{"kind": "agent" if r["agent_id"] else "person", "sub": r["member_sub"], "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
             "name": (f"@{r['agent_name']}" if r["agent_id"] else names.get(r["member_sub"])), "role": r["role"], "added_at": _iso(r["added_at"]),
             "me": r["member_sub"] == sub} for r in rows]


async def set_member_role(pool: Any, sub: str, room_id: str, who: str, role: str) -> dict[str, Any]:
    """Owner only: make a person an admin, or a plain member again. The owner's own role never changes here, and agents have no role."""
    room = await _member_room(pool, sub, room_id)
    if room["my_role"] != "owner":
        raise RoomError("only the owner can change roles", 403)
    if role not in ("admin", "member"):
        raise RoomError("role must be admin or member")
    if who == room["owner_sub"]:
        raise RoomError("the owner's role can't be changed", 409)
    row = await pool.fetchrow(
        "UPDATE room_members SET role = $3 WHERE room_id = $1 AND member_sub = $2 AND removed_at IS NULL RETURNING member_sub",
        room["id"], who, role)
    if row is None:
        raise RoomError("no such member", 404)
    return {"sub": who, "role": role}


async def remove_member(pool: Any, sub: str, room_id: str, who: str) -> dict[str, Any]:
    """The owner removes someone, or anyone leaves (who == their own subject). The owner cannot leave: delete the room instead."""
    room = await _member_room(pool, sub, room_id)
    if who == room["owner_sub"]:
        raise RoomError("the owner can't be removed; delete the room instead", 409)
    if who != sub and room["my_role"] not in MANAGERS:
        raise RoomError("only the owner or an admin can remove someone else", 403)
    if who != sub and room["my_role"] == "admin":
        target = await pool.fetchrow(
            "SELECT role FROM room_members WHERE room_id = $1 AND member_sub = $2 AND removed_at IS NULL", room["id"], who)
        if target is not None and target["role"] != "member":
            raise RoomError("an admin can only remove plain members", 403)
    row = await pool.fetchrow(
        "UPDATE room_members SET removed_at = now() WHERE room_id = $1 AND member_sub = $2 AND removed_at IS NULL RETURNING id",
        room["id"], who)
    if row is None:
        raise RoomError("no such member", 404)
    return {"removed": who}


# ------------------------------------------------------------------ invites

async def create_invite(pool: Any, sub: str, room_id: str, days: int = 7, max_uses: int = 20) -> dict[str, Any]:
    room = await _member_room(pool, sub, room_id)
    if room["my_role"] not in MANAGERS:
        raise RoomError("only the owner or an admin can invite people", 403)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= INVITE_MAX_DAYS:
        raise RoomError(f"days must be a whole number from 1 to {INVITE_MAX_DAYS}")
    if not isinstance(max_uses, int) or isinstance(max_uses, bool) or not 1 <= max_uses <= INVITE_MAX_USES:
        raise RoomError(f"max_uses must be a whole number from 1 to {INVITE_MAX_USES}")
    invite_id = uuid7()
    secret = secrets.token_hex(12)
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    await pool.execute(
        "INSERT INTO room_invites (id, room_id, secret_hash, created_by, expires_at, max_uses) VALUES ($1, $2, $3, $4, $5, $6)",
        invite_id, room["id"], _hash(secret), sub, expires, max_uses)
    # the only time the code exists in the clear
    return {"id": str(invite_id), "code": f"rm1_{invite_id.hex}_{secret}", "expires_at": expires.isoformat(), "max_uses": max_uses}


async def list_invites(pool: Any, sub: str, room_id: str) -> list[dict[str, Any]]:
    room = await _member_room(pool, sub, room_id)
    if room["my_role"] not in MANAGERS:
        raise RoomError("only the owner or an admin can see invites", 403)
    rows = await pool.fetch(
        "SELECT id, created_at, expires_at, max_uses, uses, revoked_at, "
        "(revoked_at IS NULL AND expires_at > now() AND uses < max_uses) AS active "
        "FROM room_invites WHERE room_id = $1 ORDER BY created_at DESC LIMIT 100", room["id"])
    return [{"id": str(r["id"]), "created_at": _iso(r["created_at"]), "expires_at": _iso(r["expires_at"]), "max_uses": r["max_uses"],
             "uses": r["uses"], "revoked": r["revoked_at"] is not None, "active": bool(r["active"])} for r in rows]


async def revoke_invite(pool: Any, sub: str, room_id: str, invite_id: str) -> dict[str, Any]:
    room = await _member_room(pool, sub, room_id)
    if room["my_role"] not in MANAGERS:
        raise RoomError("only the owner or an admin can revoke an invite", 403)
    row = await pool.fetchrow(
        "UPDATE room_invites SET revoked_at = now() WHERE id = $1 AND room_id = $2 AND revoked_at IS NULL RETURNING id",
        _uuid(invite_id, "invite id"), room["id"])
    if row is None:
        raise RoomError("no such active invite", 404)
    return {"revoked": str(row["id"])}


async def redeem_invite(pool: Any, sub: str, code: str) -> dict[str, Any]:
    """Join the room an invite is for. One generic error for every way an invite can be unusable, so a code can't be probed.
    Someone who is already in the room gets it back and the invite is not used up."""
    bad = RoomError("this invite is not valid", 404)
    m = CODE_RE.match((code or "").strip())
    if not m:
        raise bad
    invite_id, secret = uuid.UUID(m.group(1)), m.group(2)
    inv = await pool.fetchrow(
        "SELECT i.id, i.room_id, i.secret_hash, i.uses, i.max_uses, r.title FROM room_invites i JOIN rooms r ON r.id = i.room_id "
        "WHERE i.id = $1 AND i.revoked_at IS NULL AND i.expires_at > now() AND r.deleted_at IS NULL", invite_id)
    if inv is None or not hmac.compare_digest(inv["secret_hash"], _hash(secret)):
        raise bad
    already = await pool.fetchval(
        "SELECT 1 FROM room_members WHERE room_id = $1 AND member_sub = $2 AND removed_at IS NULL", inv["room_id"], sub)
    if not already:
        count = await pool.fetchval("SELECT count(*) FROM room_members WHERE room_id = $1 AND removed_at IS NULL", inv["room_id"])
        if count >= MAX_MEMBERS:
            raise RoomError("this room is full", 409)
        used = await pool.fetchrow(
            "UPDATE room_invites SET uses = uses + 1 WHERE id = $1 AND revoked_at IS NULL AND expires_at > now() "
            "AND uses < max_uses RETURNING id", inv["id"])
        if used is None:
            raise bad
        await pool.execute(
            "INSERT INTO room_members (id, room_id, member_sub, role, added_by) VALUES ($1, $2, $3, 'member', $4) "
            "ON CONFLICT (room_id, member_sub) DO UPDATE SET removed_at = NULL, role = 'member', added_at = now()",
            uuid7(), inv["room_id"], sub, sub)
    return {"room_id": str(inv["room_id"]), "title": inv["title"], "already_member": bool(already)}


# ------------------------------------------------------------------ events

def _event_json(row: Any, names: dict[str, str], me: str) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "room_id": str(row["room_id"]), "seq": row["seq"], "kind": row["kind"],
        "sender": {"kind": row["sender_kind"], "handle": row["sender_handle"], "sub": row["sender_sub"],
                   "name": names.get(row["sender_sub"]), "me": row["sender_sub"] == me},
        "body": row["body"], "target": str(row["target"]) if row["target"] else None,
        "reply_to": str(row["reply_to"]) if row["reply_to"] else None, "lamport": row["lamport"],
        "mentions": list(row["mentions"] or []), "depth": row["depth"], "device_id": row["device_id"],
        "created_at": _iso(row["created_at"]),
    }


async def list_events(pool: Any, sub: str, room_id: str, after: int = 0, limit: int = 200) -> dict[str, Any]:
    """Events with seq > after, oldest first. `next` is the cursor to ask for next time; `more` says there is another page."""
    room = await _member_room(pool, sub, room_id)
    limit = max(1, min(int(limit or 200), MAX_PAGE))
    rows = await pool.fetch(
        "SELECT * FROM room_events WHERE room_id = $1 AND seq > $2 ORDER BY seq LIMIT $3", room["id"], max(0, int(after or 0)), limit + 1)
    page = rows[:limit]
    names = await _names(pool, [r["sender_sub"] for r in page])
    events = [_event_json(r, names, sub) for r in page]
    return {"events": events, "next": events[-1]["seq"] if events else max(0, int(after or 0)), "more": len(rows) > limit,
            "last_seq": room["next_seq"]}


class _Limiter:
    """A sliding window per person, in memory (one worker). Swappable in tests."""

    def __init__(self, limit: int = EVENTS_PER_MINUTE, seconds: float = 60.0) -> None:
        from collections import defaultdict, deque
        import time
        self.limit, self.seconds, self._hits, self._now = limit, seconds, defaultdict(deque), time.monotonic

    def allow(self, key: str) -> bool:
        now = self._now()
        q = self._hits[key]
        while q and now - q[0] >= self.seconds:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


LIMITER = _Limiter()


def _validate(ev: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """The cleaned event, or the reason it is refused (permanent: retrying the same event will not help)."""
    if not isinstance(ev, dict):
        return None, "an event must be an object"
    try:
        eid = uuid.UUID(str(ev.get("id")))
    except (ValueError, TypeError):
        return None, "id must be a UUID"
    kind = ev.get("kind", "msg")
    if kind not in KINDS:
        return {"id": eid}, f"kind must be one of {', '.join(KINDS)}"
    sender_kind = ev.get("sender_kind", "person")
    if sender_kind not in ("person", "agent"):
        return {"id": eid}, "sender_kind must be person or agent"
    body = _clean(str(ev.get("body", "")), MAX_BODY)
    if kind in NEEDS_TEXT and not body:
        return {"id": eid}, "the message is empty"
    target = reply_to = None
    try:
        if ev.get("target"):
            target = uuid.UUID(str(ev["target"]))
        if ev.get("reply_to"):
            reply_to = uuid.UUID(str(ev["reply_to"]))
    except ValueError:
        return {"id": eid}, "target and reply_to must be UUIDs"
    if kind in NEEDS_TARGET and target is None:
        return {"id": eid}, f"a {kind} needs a target"
    depth = 0
    if sender_kind == "agent":
        d = ev.get("depth", 0)
        if not isinstance(d, int) or isinstance(d, bool) or d < 0:
            return {"id": eid}, "depth must be a whole number"
        if d > 4:
            return {"id": eid}, "delegation depth limit (4) reached: a person has to step in"
        depth = d
    handle = str(ev.get("sender_handle", "") or "").strip()
    if sender_kind == "agent":
        if not HANDLE_RE.match(handle):
            return {"id": eid}, "an agent's sender_handle must look like @name"
    else:
        handle = "@person"  # people are shown by their account name, never by a label they typed
    lamport = ev.get("lamport", 0)
    lamport = lamport if isinstance(lamport, int) and not isinstance(lamport, bool) and 0 <= lamport < 2**53 else 0
    device = str(ev.get("device_id", "") or "")[:64] or None
    return {"id": eid, "kind": kind, "sender_kind": sender_kind, "handle": handle[:80], "body": body, "target": target,
            "reply_to": reply_to, "lamport": lamport, "mentions": _mentions(body), "depth": depth, "device_id": device}, None


async def append_events(pool: Any, sub: str, room_id: str, events: Any) -> dict[str, Any]:
    """Store a batch of events. Idempotent by id: an id already stored is reported as accepted with its seq. Each refusal says
    why and whether to `retry` (a rate limit) or give up (a bad event)."""
    room = await _member_room(pool, sub, room_id)
    if not isinstance(events, list) or not events:
        raise RoomError("events must be a non-empty list")
    if len(events) > MAX_BATCH:
        raise RoomError(f"at most {MAX_BATCH} events per request")
    if room["next_seq"] >= MAX_EVENTS_PER_ROOM:
        raise RoomError("this room has reached its size limit", 409)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    top = 0
    for raw in events:
        ev, why = _validate(raw)
        eid = str(ev["id"]) if ev and "id" in ev else None
        if why is not None:
            rejected.append({"id": eid, "reason": why, "retry": False})
            continue
        prior = await pool.fetchrow("SELECT room_id, seq FROM room_events WHERE id = $1", ev["id"])
        if prior is not None:
            if prior["room_id"] != room["id"]:
                rejected.append({"id": eid, "reason": "that event id is already used elsewhere", "retry": False})
            else:
                accepted.append({"id": eid, "seq": prior["seq"], "duplicate": True})
                top = max(top, prior["seq"])
            continue
        if ev["target"] is not None and ev["kind"] in NEEDS_TARGET:
            t = await pool.fetchrow("SELECT sender_sub FROM room_events WHERE id = $1 AND room_id = $2", ev["target"], room["id"])
            if t is None:
                rejected.append({"id": eid, "reason": "the event you refer to is not in this room", "retry": False})
                continue
            if ev["kind"] in ("edit", "delete") and t["sender_sub"] != sub and not (ev["kind"] == "delete" and room["my_role"] in MANAGERS):
                rejected.append({"id": eid, "reason": "you can only change your own messages", "retry": False})
                continue
        if not LIMITER.allow(sub):
            rejected.append({"id": eid, "reason": "slow down: too many messages in a minute", "retry": True})
            continue
        row = await pool.fetchrow(
            "WITH bump AS (UPDATE rooms SET next_seq = next_seq + 1 WHERE id = $1 AND deleted_at IS NULL RETURNING next_seq) "
            "INSERT INTO room_events (id, room_id, seq, sender_sub, sender_kind, sender_handle, kind, body, target, reply_to, "
            "lamport, mentions, depth, device_id) SELECT $2, $1, bump.next_seq, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13 "
            "FROM bump ON CONFLICT (id) DO NOTHING RETURNING seq",
            room["id"], ev["id"], sub, ev["sender_kind"], ev["handle"], ev["kind"], ev["body"], ev["target"], ev["reply_to"],
            ev["lamport"], ev["mentions"], ev["depth"], ev["device_id"])
        if row is None:  # lost a race with an identical retry, or the room was just deleted
            again = await pool.fetchrow("SELECT room_id, seq FROM room_events WHERE id = $1", ev["id"])
            if again is not None and again["room_id"] == room["id"]:
                accepted.append({"id": eid, "seq": again["seq"], "duplicate": True})
                top = max(top, again["seq"])
            else:
                rejected.append({"id": eid, "reason": "no such room", "retry": False})
            continue
        accepted.append({"id": eid, "seq": row["seq"]})
        top = max(top, row["seq"])
    if top:  # what you send is read by you
        await pool.execute(
            "INSERT INTO room_reads (room_id, member_sub, last_read_seq) VALUES ($1, $2, $3) "
            "ON CONFLICT (room_id, member_sub) DO UPDATE SET last_read_seq = GREATEST(room_reads.last_read_seq, EXCLUDED.last_read_seq), "
            "updated_at = now()", room["id"], sub, top)
    return {"accepted": accepted, "rejected": rejected}


async def mark_read(pool: Any, sub: str, room_id: str, seq: int) -> dict[str, Any]:
    room = await _member_room(pool, sub, room_id)
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise RoomError("seq must be a whole number")
    seq = min(seq, room["next_seq"])
    row = await pool.fetchrow(
        "INSERT INTO room_reads (room_id, member_sub, last_read_seq) VALUES ($1, $2, $3) "
        "ON CONFLICT (room_id, member_sub) DO UPDATE SET last_read_seq = GREATEST(room_reads.last_read_seq, EXCLUDED.last_read_seq), "
        "updated_at = now() RETURNING last_read_seq", room["id"], sub, seq)
    return {"last_read_seq": row["last_read_seq"]}
