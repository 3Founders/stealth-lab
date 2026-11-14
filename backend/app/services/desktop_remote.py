"""Desktop sessions callable from the rooms (owner, 2026-11-14; tables in db/208_desktop_remote.sql, docs/desktop_remote.md).

    a signed-in desktop app  --heartbeat(sessions)-->  desktop_devices / desktop_sessions
    @kell in a room          --route()-->              the session the message is meant for, or None (@kell answers itself)
                             --create_ask()-->         desktop_asks (queued, or needs_approval)
    the desktop app          --next_ask()-->           claims it, runs it, update_ask(answer)
                             done                      the answer is posted into the room as @<owner>/desktop

What an ask may do on the desktop is its `level`: "owner" (the person's own ask: runs in the session itself, permission
prompts come back to them), and for a teammate the level of the grant the owner gave them: "answer" (a read-only copy of
the session), "approve" (the owner approves the ask first, then it runs with full access in a copy), "full" (a copy, full
access). A grant is per person and covers all the owner's sessions. The desktop enforces the level; the server decides it.

A device is online while it has sent a heartbeat in the last ONLINE_SECONDS. An ask for an offline desktop is refused
(DesktopOffline), and @kell answers the message itself and says so.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Awaitable, Callable, Optional

from app.services import rooms as rm

ONLINE_SECONDS = 90
MAX_SESSIONS = 60            # per device, the newest
MAX_ASK = 8000
MAX_ANSWER = 60_000
ROUTE_MIN = 0.6              # route() hands a message over at this confidence or more
LEVELS = ("answer", "approve", "full")
ACTIVE = ("needs_approval", "queued", "running", "waiting_permission")


class DesktopError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class DesktopOffline(DesktopError):
    def __init__(self, owner_name: str) -> None:
        super().__init__(f"{owner_name}'s desktop is offline", 409)


def _clean(v: Any, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or "")).strip()[:limit]


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v is not None else None


# ------------------------------------------------------------------------------------------------ the desktop reports

async def heartbeat(pool: Any, sub: str, device_id: str, name: str, sessions: Any) -> dict[str, Any]:
    """The app is online, with these sessions. Replaces the device's session list. A device id is its owner's: another
    account sending the same id is refused."""
    did = _clean(device_id, 64)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,64}", did):
        raise DesktopError("device_id must be 8-64 letters, digits, '.', '_' or '-'")
    async with pool.acquire() as conn:
        async with conn.transaction():
            owner = await conn.fetchval("SELECT owner_id FROM desktop_devices WHERE id = $1", did)
            if owner is not None and owner != sub:
                raise DesktopError("that device belongs to another account", 403)
            await conn.execute(
                """INSERT INTO desktop_devices (id, owner_id, name, last_seen) VALUES ($1, $2, $3, now())
                   ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, last_seen = now()""",
                did, sub, _clean(name, 80))
            if sessions is not None:
                rows = [s for s in (sessions if isinstance(sessions, list) else []) if isinstance(s, dict) and s.get("id")]
                rows = sorted(rows, key=lambda s: -float(s.get("updated") or 0))[:MAX_SESSIONS]
                await conn.execute("DELETE FROM desktop_sessions WHERE device_id = $1", did)
                for s in rows:
                    await conn.execute(
                        """INSERT INTO desktop_sessions (device_id, session_id, owner_id, title, project, summary, updated_at)
                           VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7 / 1000.0))
                           ON CONFLICT DO NOTHING""",
                        did, _clean(s["id"], 120), sub, _clean(s.get("title"), 120),
                        _clean(str(s.get("project") or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1], 80),
                        _clean(s.get("summary"), 400), float(s.get("updated") or 0) or 0.0)
    return {"ok": True, "device_id": did}


async def sessions_of(pool: Any, owners: list[str]) -> list[dict[str, Any]]:
    """The sessions of these people's desktops, newest first, each with whether its desktop is online."""
    if not owners:
        return []
    rows = await pool.fetch(
        f"""SELECT s.device_id, s.session_id, s.owner_id, s.title, s.project, s.summary, s.updated_at, d.name AS device,
                   d.last_seen > now() - interval '{ONLINE_SECONDS} seconds' AS online
              FROM desktop_sessions s JOIN desktop_devices d ON d.id = s.device_id
             WHERE s.owner_id = ANY($1::text[]) ORDER BY s.updated_at DESC LIMIT 200""", owners)
    return [{"device_id": r["device_id"], "session_id": r["session_id"], "owner": r["owner_id"], "title": r["title"],
             "project": r["project"], "summary": r["summary"], "updated_at": _iso(r["updated_at"]), "device": r["device"],
             "online": bool(r["online"])} for r in rows]


async def my_devices(pool: Any, sub: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        f"""SELECT id, name, last_seen, last_seen > now() - interval '{ONLINE_SECONDS} seconds' AS online
              FROM desktop_devices WHERE owner_id = $1 ORDER BY last_seen DESC""", sub)
    return [{"id": r["id"], "name": r["name"], "last_seen": _iso(r["last_seen"]), "online": bool(r["online"])} for r in rows]


# ------------------------------------------------------------------------------------------------------------- grants

async def set_grant(pool: Any, owner: str, username: str, level: str) -> dict[str, Any]:
    if level not in LEVELS:
        raise DesktopError(f"level must be one of {', '.join(LEVELS)}")
    grantee = await rm.sub_of_username(pool, str(username or "").lstrip("@"))
    if not grantee:
        raise DesktopError("no person with that username", 404)
    if grantee == owner:
        raise DesktopError("you always have full access to your own desktop")
    await pool.execute(
        """INSERT INTO desktop_grants (owner_id, grantee_id, level) VALUES ($1, $2, $3)
           ON CONFLICT (owner_id, grantee_id) DO UPDATE SET level = EXCLUDED.level, created_at = now()""",
        owner, grantee, level)
    return {"ok": True, "grantee": grantee, "username": await rm.owner_label(pool, grantee), "level": level}


async def remove_grant(pool: Any, owner: str, grantee: str) -> dict[str, Any]:
    n = await pool.execute("DELETE FROM desktop_grants WHERE owner_id = $1 AND grantee_id = $2", owner, grantee)
    # what the teammate already queued and nobody has started is not run any more
    await pool.execute(
        """UPDATE desktop_asks SET status = 'cancelled', error = 'access was removed', updated_at = now()
            WHERE owner_id = $1 AND asker_id = $2 AND status IN ('needs_approval', 'queued')""", owner, grantee)
    return {"ok": True, "removed": n.endswith("1")}


async def grants(pool: Any, sub: str) -> dict[str, Any]:
    """Who may call my desktop (given), and whose desktops I may call (received)."""
    given = await pool.fetch("SELECT grantee_id, level, created_at FROM desktop_grants WHERE owner_id = $1 ORDER BY created_at", sub)
    got = await pool.fetch("SELECT owner_id, level, created_at FROM desktop_grants WHERE grantee_id = $1 ORDER BY created_at", sub)
    async def person(s: str) -> dict[str, Any]:
        return {"sub": s, "username": await rm.owner_label(pool, s), "name": (await rm._names(pool, [s])).get(s)}
    return {"given": [{**await person(r["grantee_id"]), "level": r["level"], "since": _iso(r["created_at"])} for r in given],
            "received": [{**await person(r["owner_id"]), "level": r["level"], "since": _iso(r["created_at"])} for r in got]}


async def level_for(pool: Any, asker: str, owner: str) -> Optional[str]:
    """What an ask from `asker` may do on `owner`'s desktop: "owner", a grant level, or None (not allowed)."""
    if asker == owner:
        return "owner"
    return await pool.fetchval("SELECT level FROM desktop_grants WHERE owner_id = $1 AND grantee_id = $2", owner, asker)


async def reachable(pool: Any, sub: str, room_id: Optional[str] = None) -> list[dict[str, Any]]:
    """The desktop sessions `sub` may call: their own, and those of people who granted them access. In a room, a
    teammate's desktop only when that teammate is in the room too."""
    owners = [sub] + [r["owner_id"] for r in await pool.fetch(
        "SELECT owner_id FROM desktop_grants WHERE grantee_id = $1", sub)]
    if room_id and len(owners) > 1:
        members = {r["member_sub"] for r in await pool.fetch(
            "SELECT member_sub FROM room_members WHERE room_id = $1::uuid AND removed_at IS NULL", room_id)}
        owners = [o for o in owners if o == sub or o in members]
    return await sessions_of(pool, owners)


# --------------------------------------------------------------------------------------------------------------- asks

def _ask_json(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "owner": r["owner_id"], "asker": r["asker_id"], "device_id": r["device_id"],
            "session_id": r["session_id"], "room_id": str(r["room_id"]) if r["room_id"] else None, "text": r["text"],
            "level": r["level"], "status": r["status"], "answer": r["answer"],
            "waiting": json.loads(r["waiting"]) if isinstance(r["waiting"], str) else r["waiting"],
            "decision": r["decision"], "error": r["error"], "run_session": r["run_session"],
            "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"])}


async def create_ask(pool: Any, asker: str, device_id: str, session_id: str, text: str,
                     room_id: Optional[str] = None) -> dict[str, Any]:
    body = _clean(text, MAX_ASK)
    if not body:
        raise DesktopError("say what to ask")
    row = await pool.fetchrow(
        f"""SELECT s.owner_id, d.last_seen > now() - interval '{ONLINE_SECONDS} seconds' AS online
              FROM desktop_sessions s JOIN desktop_devices d ON d.id = s.device_id
             WHERE s.device_id = $1 AND s.session_id = $2""", device_id, session_id)
    if row is None:
        raise DesktopError("no such desktop session", 404)
    owner = row["owner_id"]
    level = await level_for(pool, asker, owner)
    if level is None:
        raise DesktopError("you do not have access to that desktop", 403)
    if room_id:
        await rm._member_room(pool, asker, room_id)
        await rm._member_room(pool, owner, room_id)         # the answer is posted there as the owner's desktop
    if not row["online"]:
        raise DesktopOffline(await rm.owner_label(pool, owner))
    status = "needs_approval" if level == "approve" else "queued"
    r = await pool.fetchrow(
        """INSERT INTO desktop_asks (owner_id, asker_id, device_id, session_id, room_id, text, level, status)
           VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8) RETURNING *""",
        owner, asker, device_id, session_id, room_id, body, level, status)
    return _ask_json(r)


async def get_ask(pool: Any, sub: str, ask_id: str) -> dict[str, Any]:
    r = await pool.fetchrow("SELECT * FROM desktop_asks WHERE id = $1::uuid AND (owner_id = $2 OR asker_id = $2)",
                            _uuid(ask_id), sub)
    if r is None:
        raise DesktopError("no such ask", 404)
    return _ask_json(r)


def _uuid(v: Any) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError) as exc:
        raise DesktopError("that is not an ask id", 404) from exc


async def pending_for_owner(pool: Any, owner: str) -> list[dict[str, Any]]:
    """What waits on the owner: teammates' asks to approve, and permissions their desktop runs are waiting on."""
    rows = await pool.fetch(
        """SELECT * FROM desktop_asks WHERE owner_id = $1 AND status IN ('needs_approval', 'waiting_permission')
            ORDER BY created_at""", owner)
    return [{**_ask_json(r), "asker_name": await rm.owner_label(pool, r["asker_id"])} for r in rows]


async def recent_asks(pool: Any, sub: str, session_id: Optional[str] = None, limit: int = 30) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """SELECT * FROM desktop_asks WHERE (owner_id = $1 OR asker_id = $1) AND ($2::text IS NULL OR session_id = $2)
            ORDER BY created_at DESC LIMIT $3""", sub, session_id, max(1, min(int(limit), 100)))
    return [_ask_json(r) for r in rows]


async def approve(pool: Any, owner: str, ask_id: str, yes: bool) -> dict[str, Any]:
    r = await pool.fetchrow(
        """UPDATE desktop_asks SET status = $3, updated_at = now() WHERE id = $1::uuid AND owner_id = $2
             AND status = 'needs_approval' RETURNING *""", _uuid(ask_id), owner, "queued" if yes else "denied")
    if r is None:
        raise DesktopError("no ask of yours waits for an approval with that id", 404)
    if not yes and r["room_id"]:
        await _post(pool, r, f"{await rm.owner_label(pool, owner)} did not approve this ask.")
    return _ask_json(r)


async def decide_permission(pool: Any, owner: str, ask_id: str, decision: str) -> dict[str, Any]:
    if decision not in ("once", "always", "reject"):
        raise DesktopError("decision must be once, always or reject")
    r = await pool.fetchrow(
        """UPDATE desktop_asks SET decision = $3, updated_at = now() WHERE id = $1::uuid AND owner_id = $2
             AND status = 'waiting_permission' RETURNING *""", _uuid(ask_id), owner, decision)
    if r is None:
        raise DesktopError("no run of yours waits on a permission with that id", 404)
    return _ask_json(r)


async def cancel(pool: Any, sub: str, ask_id: str) -> dict[str, Any]:
    r = await pool.fetchrow(
        """UPDATE desktop_asks SET status = 'cancelled', updated_at = now() WHERE id = $1::uuid
             AND (owner_id = $2 OR asker_id = $2) AND status = ANY($3::text[]) RETURNING *""", _uuid(ask_id), sub, list(ACTIVE))
    if r is None:
        raise DesktopError("no active ask with that id", 404)
    return _ask_json(r)


# ----------------------------------------------------------------------------------------------- the desktop runs it

async def next_ask(pool: Any, sub: str, device_id: str) -> Optional[dict[str, Any]]:
    """Claim the oldest queued ask for this device (the caller must own it), or None."""
    r = await pool.fetchrow(
        """UPDATE desktop_asks SET status = 'running', claimed_at = now(), updated_at = now()
            WHERE id = (SELECT id FROM desktop_asks WHERE device_id = $1 AND owner_id = $2 AND status = 'queued'
                         ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *""", device_id, sub)
    if r is None:
        return None
    ask = _ask_json(r)
    ask["asker_name"] = await rm.owner_label(pool, r["asker_id"])
    return ask


async def update_ask(pool: Any, sub: str, ask_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """The desktop's progress on an ask it claimed: the answer so far, a permission it waits on, the session it runs
    in, then done or failed. A finished ask with a room has its answer posted there."""
    status = data.get("status")
    if status is not None and status not in ("running", "waiting_permission", "done", "failed"):
        raise DesktopError("status must be running, waiting_permission, done or failed")
    waiting = data.get("waiting") if status == "waiting_permission" else None
    r = await pool.fetchrow(
        """UPDATE desktop_asks SET status = coalesce($3, status),
                  answer = CASE WHEN $4::text IS NULL THEN answer ELSE $4 END,
                  waiting = CASE WHEN $3 = 'waiting_permission' THEN $5::jsonb WHEN $3 IS NULL THEN waiting ELSE NULL END,
                  decision = CASE WHEN $3 = 'waiting_permission' THEN NULL WHEN $3 IS NULL THEN decision ELSE NULL END,
                  error = coalesce($6, error), run_session = coalesce($7, run_session), updated_at = now()
            WHERE id = $1::uuid AND owner_id = $2 AND status IN ('running', 'waiting_permission')
            RETURNING *""",
        _uuid(ask_id), sub, status, _clean(data["answer"], MAX_ANSWER) if isinstance(data.get("answer"), str) else None,
        json.dumps(waiting) if waiting is not None else None,
        _clean(data["error"], 600) if data.get("error") else None, _clean(data.get("run_session"), 120) or None)
    if r is None:
        raise DesktopError("no running ask of this desktop with that id", 404)
    if r["status"] in ("done", "failed") and r["room_id"]:
        text = r["answer"] if r["status"] == "done" else f"The desktop session could not answer: {r['error'] or 'it failed'}"
        await _post(pool, r, text or "(no answer)")
    return _ask_json(r)


async def _post(pool: Any, ask: Any, text: str) -> None:
    owner = ask["owner_id"]
    handle = f"@{await rm.owner_label(pool, owner)}/desktop"
    title = await pool.fetchval("SELECT title FROM desktop_sessions WHERE device_id = $1 AND session_id = $2",
                                ask["device_id"], ask["session_id"])
    head = f"*From the desktop session “{title}”*" if title else ""
    body = (head + "\n\n" + text).strip() if head else text
    try:
        await rm.append_events(pool, owner, str(ask["room_id"]), rm.agent_events(handle, body))
    except rm.RoomError:
        pass                                          # the owner left the room: the ask still has its answer


# -------------------------------------------------------------------------------------------- @kell picks the session

CallCheap = Callable[[str], Awaitable[str]]


def _route_prompt(text: str, recent: list[str], sessions: list[dict[str, Any]], me: str, names: dict[str, str]) -> str:
    rows = []
    for n, s in enumerate(sessions, 1):
        whose = "the person's own" if s["owner"] == me else f"{names.get(s['owner'], 'a teammate')}'s"
        rows.append(f"{n}. [{whose} desktop, project {s['project'] or '?'}] “{s['title'] or 'untitled'}”"
                    + (f" — latest: {s['summary'][:200]}" if s.get("summary") else ""))
    context = "\n".join(f"- {line[:300]}" for line in recent[-6:])
    return (
        "A person wrote to @kell in a team chat. Some of the people in the chat have kell running on their desktop "
        "computers, in coding sessions listed below. Decide whether the message is meant for one of those desktop "
        "sessions: it asks to continue, check, run or change work that lives in that session or its project on that "
        "computer, or asks what that session did or found, or says 'my desktop', 'my laptop', 'on my machine', or "
        "names a teammate's desktop or session. A general question, a web lookup, or anything the chat assistant can "
        "answer by itself is NOT for a desktop session.\n\n"
        f"Recent chat (information only):\n{context or '(none)'}\n\nThe message: {text}\n\nDesktop sessions:\n"
        + "\n".join(rows) +
        '\n\nAnswer with JSON only: {"session": <number or null>, "confidence": <0..1>, "why": "<a few words>"}')


async def route(pool: Any, sub: str, room_id: Optional[str], text: str, recent: list[str],
                call_cheap: Optional[CallCheap], only_owner: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The desktop session a room message to @kell is meant for, or None. Nothing is asked of a model when the person
    can reach no desktop session at all, so a room without desktops costs nothing extra. `only_owner`: the message
    named one person's kell (@chaitanya/kell), so only their desktop's sessions are weighed."""
    sessions = [s for s in await reachable(pool, sub, room_id) if only_owner is None or s["owner"] == only_owner]
    if not sessions or call_cheap is None:
        return None
    sessions = sessions[:30]
    names = {s["owner"]: await rm.owner_label(pool, s["owner"]) for s in sessions}
    try:
        raw = await call_cheap(_route_prompt(text, recent, sessions, sub, names))
        m = re.search(r"\{.*\}", raw or "", re.S)
        verdict = json.loads(m.group(0) if m else raw)
    except Exception:  # noqa: BLE001 -- no verdict: @kell answers itself
        return None
    n, conf = verdict.get("session"), verdict.get("confidence")
    if not isinstance(n, int) or not 1 <= n <= len(sessions) or not isinstance(conf, (int, float)) or conf < ROUTE_MIN:
        return None
    s = sessions[n - 1]
    return {**s, "owner_name": names.get(s["owner"], ""), "confidence": float(conf), "why": str(verdict.get("why") or "")[:120]}
