"""
The Delegator relay: a person's own work queue for the agents they run.

A signed-in person registers an agent (a Claude chat with the kell connector, Claude Code, opencode, ...), sends it a task
from the website, and the agent PULLS the task (``claim_next``, exposed as the MCP tool ``delegate_inbox``) and posts the
outcome back (``submit_result``, ``delegate_result``). Design and the rules it keeps:
``.scratch/prompts/delegator_pull_brief.md``.

What this module refuses to be, so nobody has to wonder:
  * It never calls into anyone's machine and starts nothing. Work happens only when the person's own agent asks for it.
  * It holds no model credentials of any kind (no claude.ai login, no API key). The agent authenticates to kell with the
    person's kell sign-in; whatever model it uses is the person's own business with their own provider.
  * Task text and results are free text from people and agents, so they pass the redaction chokepoint before storage and are
    handed to the receiving agent as DATA ("a task to review"), never as instructions kell vouches for.

Every function takes ``owner_sub`` (the Supabase subject) and checks it on every row it touches; the tables have RLS on with no
policies (migration 155), so the application check is the whole gate. Honest limits: there is one queue per (owner, agent),
agent-to-agent calls (``call_agent``) carry ``depth`` and stop at MAX_DEPTH, and expiry is enforced by opportunistic deletes, not a job.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.trace_redaction import redact_value
from app.utils.ids import uuid7

ONLINE_WINDOW = timedelta(seconds=60)
MAX_BODY = 8000
MAX_RESULT = 20000
MAX_CHECK = 2000
MAX_AGENTS_PER_OWNER = 20
MAX_OPEN_TASKS_PER_OWNER = 200       # queued + claimed + waiting for approval, across all agents
ROOM_RE = re.compile(r"^#?[a-z0-9][a-z0-9_.-]{0,78}$", re.I)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
MENTION_RE = re.compile(r"(?<![\w/])@([a-z0-9][a-z0-9_.-]*)(?:/([a-z0-9][a-z0-9_.-]*))?", re.I)
OPEN_STATES = ("needs_approval", "queued", "claimed")


class DelegateError(Exception):
    """A request the relay refuses; ``status`` is the HTTP status the routes use for it."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _clean(text: str, limit: int) -> str:
    matched: list[str] = []
    return redact_value(text.strip()[:limit], matched)


def mention_pairs(text: str) -> list[tuple[Optional[str], str]]:
    """(owner, agent) for each agent mentioned in a line, in order, no repeats: ``@alice/claude`` is ("alice", "claude") and ``@claude`` is
    (None, "claude"). A third part (``@alice/claude/Q4 plan``) names a chat and does not change who is called."""
    seen: list[tuple[Optional[str], str]] = []
    for first, second in MENTION_RE.findall(text or ""):
        pair = (first.lower(), second.lower()) if second else (None, first.lower())
        if pair not in seen:
            seen.append(pair)
    return seen


def mentions(text: str) -> list[str]:
    """Agent names mentioned in a line: ``@alice/web-chat`` and ``@web-chat`` both name ``web-chat``. Order kept, no repeats."""
    seen: list[str] = []
    for _owner, name in mention_pairs(text):
        if name not in seen:
            seen.append(name)
    return seen


def normalize_room(room: Optional[str]) -> str:
    r = (room or "#general").strip()
    if not ROOM_RE.match(r):
        raise DelegateError("room must be 1-79 letters, digits, dots, dashes or underscores (a leading # is fine)")
    return r if r.startswith("#") else f"#{r}"


def _agent_json(row: Any, now: datetime, owner: str = "", mine: bool = True, owner_name: str = "") -> dict[str, Any]:
    last_seen = row["last_seen"]
    online = bool(last_seen and now - last_seen <= ONLINE_WINDOW and row["revoked_at"] is None)
    return {
        "id": str(row["id"]), "name": row["name"], "kind": row["kind"],
        # what to type to call it: your own is @name, someone else's is @theirname/name
        "handle": f"@{row['name']}" if mine or not owner else f"@{owner}/{row['name']}",
        "full_handle": f"@{owner}/{row['name']}" if owner else f"@{row['name']}",
        # owner is the username (what a handle uses); owner_name is the person's display name, for a label like "Anuj's" (may be empty)
        "owner": owner, "owner_name": owner_name, "mine": mine,
        "created_at": row["created_at"].isoformat(),
        "last_seen": last_seen.isoformat() if last_seen else None,
        "online": online, "connected": online,
        "revoked": row["revoked_at"] is not None,
    }


def _task_json(row: Any) -> dict[str, Any]:
    def iso(v: Any) -> Optional[str]:
        return v.isoformat() if v else None

    locators = row["locators"]
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": str(row["id"]), "agent_id": str(row["agent_id"]), "room": row["room"], "from": row["from_handle"],
        "text": row["body"], "locators": json.loads(locators) if isinstance(locators, str) else locators,
        "depth": row["depth"], "status": row["status"],
        "created_at": iso(row["created_at"]), "claimed_at": iso(row["claimed_at"]), "finished_at": iso(row["finished_at"]),
        "result": row["result_text"], "check": row["result_check"],
        "room_id": str(row["room_id"]) if "room_id" in keys and row["room_id"] else None,
        "posted": bool("room_id" in keys and row["room_id"]),     # its answer is posted in the room, so the room's message is the answer
    }


TASK_HISTORY = 20       # messages a task carries by default; more are one room_read away
TASK_LINE = 700         # characters per history line, as read_earlier does


async def _attach_room_context(pool: Any, owner_sub: str, task: dict[str, Any]) -> None:
    """Put the room's recent messages and file metadata on a claimed task. A connected agent gets the last few messages without
    asking, so it can follow a conversation; older messages are fetched with room_read(before=...) only when it decides it needs
    them. The files a message names are listed too, so room_file can open a picture or document with the right room id."""
    rid = str(task.get("room_id") or "")
    if not rid:
        return
    texts = [str(task.get("text") or "")]       # full bodies, for file tokens
    try:
        from app.services import rooms as _rooms

        events = await _rooms.recent_events(pool, owner_sub, rid, limit=TASK_HISTORY)
        history: list[dict[str, Any]] = []
        for e in events:
            if e.get("kind") != "msg":
                continue
            body = " ".join(str(e.get("body") or "").split())
            if not body:
                continue
            sender = e.get("sender") or {}
            history.append({"seq": int(e.get("seq") or 0), "from": sender.get("handle") or sender.get("name") or "person",
                            "text": body[:TASK_LINE]})
            texts.append(body)
        if history:
            task["history"] = history
            task["history_note"] = (f"The last {len(history)} messages in this room are in `history`, oldest first. If the task needs "
                                    f'older context, call room_read(room="{rid}", before={history[0]["seq"]}) (add query="..." to search).')
    except Exception:  # noqa: BLE001 -- room history is context, never a reason to lose the task
        pass
    try:
        from app.services import room_attachments as _att

        files = [{"id": fid, "name": name, "mime": mime} for fid, name, mime in _att.tokens(texts)]
        if files:
            task["files"] = files
            task["files_note"] = (f'Files and pictures are listed in `files`. Open one with room_file(room="{rid}", '
                                  'file_id="<id>"): a picture comes back as the image itself.')
    except Exception:  # noqa: BLE001
        pass


async def purge_expired(pool: Any) -> None:
    await pool.execute("DELETE FROM delegate_tasks WHERE delete_after < now()")


async def create_agent(pool: Any, owner_sub: str, name: str, kind: str = "agent") -> dict[str, Any]:
    name = (name or "").strip().lower()
    kind = (kind or "agent").strip().lower()[:40] or "agent"
    if not NAME_RE.match(name):
        raise DelegateError("name must be 1-40 letters, digits, dots, dashes or underscores, starting with a letter or digit")
    live = await pool.fetchval(
        "SELECT count(*) FROM delegate_agents WHERE owner_sub = $1 AND revoked_at IS NULL", owner_sub)
    if live >= MAX_AGENTS_PER_OWNER:
        raise DelegateError(f"at most {MAX_AGENTS_PER_OWNER} agents per person; revoke one first", 409)
    try:
        row = await pool.fetchrow(
            "INSERT INTO delegate_agents (id, owner_sub, name, kind) VALUES ($1, $2, $3, $4) RETURNING *",
            uuid7(), owner_sub, name, kind)
    except Exception as exc:  # noqa: BLE001 -- asyncpg.UniqueViolationError without importing asyncpg here
        if "delegate_agents_owner_name_live" in str(exc) or "unique" in str(exc).lower():
            raise DelegateError(f"you already have an agent named {name!r}", 409) from exc
        raise
    return _agent_json(row, datetime.now(timezone.utc))


async def list_agents(pool: Any, owner_sub: str, room_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Your agents, and, when a server room is given, the agents of the other people in it (they can be called by @theirname/agent)."""
    from app.services import rooms as _rooms

    now = datetime.now(timezone.utc)
    mine_label = await _rooms.owner_label(pool, owner_sub)
    rows = await pool.fetch(
        "SELECT * FROM delegate_agents WHERE owner_sub = $1 AND revoked_at IS NULL ORDER BY created_at", owner_sub)
    out = [_agent_json(r, now, mine_label, True) for r in rows]
    if room_id:
        try:
            rid = _uuid(room_id)
            member = await pool.fetchval(
                "SELECT 1 FROM room_members WHERE room_id = $1 AND member_sub = $2 AND removed_at IS NULL", rid, owner_sub)
            if member:
                others = await pool.fetch(
                    "SELECT a.* FROM delegate_agents a JOIN room_members m ON m.member_sub = a.owner_sub "
                    "WHERE m.room_id = $1 AND m.removed_at IS NULL AND a.owner_sub <> $2 AND a.revoked_at IS NULL ORDER BY a.created_at",
                    rid, owner_sub)
                labels: dict[str, str] = {}
                names = await _rooms._names(pool, [r["owner_sub"] for r in others])      # one lookup for everyone in the room
                for r in others:
                    if r["owner_sub"] not in labels:
                        labels[r["owner_sub"]] = await _rooms.owner_label(pool, r["owner_sub"])
                    out.append(_agent_json(r, now, labels[r["owner_sub"]], False, names.get(r["owner_sub"], "")))
        except (ValueError, Exception):  # noqa: BLE001 -- a bad room id or no room tables: your own agents are still listed
            pass
    return out


async def revoke_agent(pool: Any, owner_sub: str, agent_id: str) -> dict[str, Any]:
    """Revoke an agent and cancel everything it has not finished. Idempotent for an already-revoked agent."""
    try:
        aid = _uuid(agent_id)
    except ValueError as exc:
        raise DelegateError("not a valid agent id", 400) from exc
    row = await pool.fetchrow(
        "UPDATE delegate_agents SET revoked_at = COALESCE(revoked_at, now()) "
        "WHERE id = $1 AND owner_sub = $2 RETURNING id", aid, owner_sub)
    if row is None:
        raise DelegateError("no such agent", 404)
    cancelled = await pool.execute(
        "UPDATE delegate_tasks SET status = 'cancelled', finished_at = now() "
        "WHERE agent_id = $1 AND owner_sub = $2 AND status = ANY($3::text[])", aid, owner_sub, list(OPEN_STATES))
    return {"revoked": str(aid), "cancelled": _count(cancelled)}


def _count(command_tag: Any) -> int:
    try:
        return int(str(command_tag).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


def _uuid(value: str):
    import uuid

    return uuid.UUID(str(value))


async def _room_for(pool: Any, sub: str, room_id: Optional[str], label: str) -> Optional[Any]:
    """The server room a message was sent in, if the sender is in it: by id when the page knows it, else by the label among their rooms."""
    try:
        if room_id:
            return await pool.fetchrow(
                "SELECT r.id FROM rooms r JOIN room_members m ON m.room_id = r.id WHERE r.id = $1 AND r.deleted_at IS NULL "
                "AND m.member_sub = $2 AND m.removed_at IS NULL", _uuid(room_id), sub)
        return await pool.fetchrow(
            "SELECT r.id FROM rooms r JOIN room_members m ON m.room_id = r.id WHERE lower(r.title) = lower($1) AND r.deleted_at IS NULL "
            "AND m.member_sub = $2 AND m.removed_at IS NULL ORDER BY (r.owner_sub = $2) DESC, r.created_at LIMIT 1", label, sub)
    except Exception:  # noqa: BLE001 -- no room tables, or a bad id: the room is only a label
        return None


async def post_message(pool: Any, owner_sub: str, text: str, room: Optional[str] = None,
                       needs_approval: bool = False, from_handle: str = "@me", room_id: Optional[str] = None,
                       depth: int = 0) -> dict[str, Any]:
    """A line from a person in a room: one task per live agent it mentions. ``@claude`` is the sender's own claude; ``@alice/claude`` is Alice's,
    if Alice is in the same room (anyone in a room can call anyone's agent there; the agent's owner still sees and answers every task).
    A line that mentions nobody (or only unknown names) creates no task and says so."""
    from app.services import rooms as _rooms

    sender = owner_sub
    body = _clean(text or "", MAX_BODY)
    if not body:
        raise DelegateError("the message is empty")
    room = normalize_room(room)
    pairs = mention_pairs(body)
    if not pairs:
        return {"room": room, "tasks": [], "unknown": [], "note": "no @agent mentioned, so nothing was sent to an agent"}
    await purge_expired(pool)
    room_row = await _room_for(pool, sender, room_id, room)
    rid = room_row["id"] if room_row else None
    my_label = await _rooms.owner_label(pool, sender)

    targets: list[tuple[str, Any]] = []          # (shown name, agent row)
    unknown: list[str] = []
    for who, name in pairs:
        shown = f"{who}/{name}" if who else name
        agent = None
        their_sub = await _rooms.sub_of_username(pool, who) if who and who != my_label.lower() else None
        if who is None or who == my_label.lower() or their_sub is None:       # not an account: "@alice/web-chat" is just your web-chat, as it always was
            agent = await pool.fetchrow(
                "SELECT * FROM delegate_agents WHERE owner_sub = $1 AND name = $2 AND revoked_at IS NULL", sender, name)
            if agent is None and who is None and rid is not None:
                # not yours: if exactly one other person in this room has an agent of that name, that is the one meant
                found = await pool.fetch(
                    "SELECT a.* FROM delegate_agents a JOIN room_members m ON m.member_sub = a.owner_sub "
                    "WHERE m.room_id = $1 AND m.removed_at IS NULL AND a.name = $2 AND a.revoked_at IS NULL", rid, name)
                agent = found[0] if len(found) == 1 else None
        else:
            their = their_sub
            # reachable when they share a room with the caller, or the agent's owner granted the caller access (agent_links, migration 182)
            in_room = bool(their and rid is not None and await pool.fetchval(
                "SELECT 1 FROM room_members WHERE room_id = $1 AND member_sub = $2 AND removed_at IS NULL", rid, their))
            if their and (in_room or await _linked(pool, their, sender)):
                agent = await pool.fetchrow(
                    "SELECT * FROM delegate_agents WHERE owner_sub = $1 AND name = $2 AND revoked_at IS NULL", their, name)
        if agent is None:
            unknown.append(shown)
        else:
            targets.append((shown, agent))

    tasks = []
    asked_by = from_handle if from_handle and from_handle != "@me" else f"@{my_label}"
    for _shown, agent in targets:
        owner = agent["owner_sub"]
        # Someone else's text runs in your agent with your tools, so a task from another person waits for its owner's OK (the approve button); a
        # person's own tasks go straight through unless they asked for approval.
        trusted = owner != sender and await _linked(pool, owner, sender, auto_only=True)
        status = "needs_approval" if (needs_approval or (owner != sender and not trusted)) else "queued"
        open_now = await pool.fetchval(
            "SELECT count(*) FROM delegate_tasks WHERE owner_sub = $1 AND status = ANY($2::text[])", owner, list(OPEN_STATES))
        if open_now + 1 > MAX_OPEN_TASKS_PER_OWNER:
            raise DelegateError("too many unfinished tasks; cancel or finish some first", 429)
        row = await pool.fetchrow(
            "INSERT INTO delegate_tasks (id, owner_sub, agent_id, room, from_handle, body, status, requester_sub, room_id, depth) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *",
            uuid7(), owner, agent["id"], room, asked_by[:80], body, status, sender, rid, depth)
        tasks.append(_task_json(row))
    return {"room": room, "tasks": tasks, "unknown": unknown}


MAX_DEPTH = 4


async def _linked(pool: Any, owner_sub: str, caller_sub: str, auto_only: bool = False) -> bool:
    """True when ``owner_sub`` let ``caller_sub`` call their agents without a shared room (a live row in agent_links). With auto_only, only a
    grant that also skips the owner's approval counts. A missing table (migration 182 not applied) means no grant."""
    try:
        return bool(await pool.fetchval(
            "SELECT 1 FROM agent_links WHERE owner_sub = $1 AND peer_sub = $2 AND revoked_at IS NULL AND ($3 = false OR auto_approve)",
            owner_sub, caller_sub, auto_only))
    except Exception:  # noqa: BLE001
        return False


async def grant_access(pool: Any, owner_sub: str, peer_username: str, auto_approve: bool = False) -> dict[str, Any]:
    """Let another person (by username) call your agents from anywhere, not only from a room you share: ``@you/agent`` then works for them.
    One-way: for both of you to call each other, each grants the other. Their task still waits for your approval unless auto_approve."""
    from app.services import rooms as _rooms

    peer = await _rooms.sub_of_username(pool, peer_username)
    if peer is None:
        raise DelegateError(f"no person named {peer_username!r}", 404)
    if peer == owner_sub:
        raise DelegateError("that is you: your own agents are always reachable", 400)
    await pool.execute(
        "INSERT INTO agent_links (owner_sub, peer_sub, auto_approve) VALUES ($1, $2, $3) "
        "ON CONFLICT (owner_sub, peer_sub) DO UPDATE SET revoked_at = NULL, auto_approve = EXCLUDED.auto_approve",
        owner_sub, peer, bool(auto_approve))
    return {"peer": peer_username.lower(), "auto_approve": bool(auto_approve)}


async def revoke_access(pool: Any, owner_sub: str, peer_username: str) -> dict[str, Any]:
    from app.services import rooms as _rooms

    peer = await _rooms.sub_of_username(pool, peer_username)
    if peer is None:
        raise DelegateError(f"no person named {peer_username!r}", 404)
    await pool.execute("UPDATE agent_links SET revoked_at = now() WHERE owner_sub = $1 AND peer_sub = $2 AND revoked_at IS NULL", owner_sub, peer)
    return {"revoked": peer_username.lower()}


async def list_access(pool: Any, owner_sub: str) -> dict[str, Any]:
    """Who you let call your agents, and whose agents you may call."""
    from app.services import rooms as _rooms

    given = await pool.fetch("SELECT peer_sub, auto_approve FROM agent_links WHERE owner_sub = $1 AND revoked_at IS NULL", owner_sub)
    got = await pool.fetch("SELECT owner_sub, auto_approve FROM agent_links WHERE peer_sub = $1 AND revoked_at IS NULL", owner_sub)
    return {"you_allow": [{"person": await _rooms.owner_label(pool, r["peer_sub"]), "auto_approve": r["auto_approve"]} for r in given],
            "allow_you": [{"person": await _rooms.owner_label(pool, r["owner_sub"]), "auto_approve": r["auto_approve"]} for r in got]}


async def call_agent(pool: Any, caller_sub: str, to: str, text: str, from_agent: str = "agent", parent_task_id: str = "") -> dict[str, Any]:
    """An AGENT calls another agent: ``to`` is ``@anuj/kell`` (a person's agent) or ``@kell`` (one of the caller's own). The question becomes a
    task for that agent, one hop deeper than the task the caller is working on (``parent_task_id``), refused past MAX_DEPTH so two agents cannot
    call each other forever. The answer stays on the task (no room): read it with get_task. Another person's agent still needs its owner's
    approval unless they granted auto_approve; and reaching it needs a shared room or a grant."""
    from app.services import rooms as _rooms

    depth = 1
    if parent_task_id:
        try:
            parent = await pool.fetchrow("SELECT depth FROM delegate_tasks WHERE id = $1 AND owner_sub = $2 AND status = 'claimed'",
                                         _uuid(parent_task_id), caller_sub)
        except ValueError as exc:
            raise DelegateError("not a valid task id", 400) from exc
        if parent is None:
            raise DelegateError("parent_task_id is not a task you are working on", 404)
        depth = int(parent["depth"]) + 1
    if depth > MAX_DEPTH:
        raise DelegateError(f"agents have called each other {MAX_DEPTH} times in a row: a person has to step in", 409)
    pairs = mention_pairs(to if to.strip().startswith("@") else "@" + to.strip())
    if len(pairs) != 1:
        raise DelegateError("name one agent, like @anuj/kell", 400)
    who, name = pairs[0]
    mine = await _rooms.owner_label(pool, caller_sub)
    out = await post_message(pool, caller_sub, f"@{who + '/' if who else ''}{name} {text}", "#direct",
                             from_handle=f"@{mine}/{(from_agent or 'agent').lower()}", depth=depth)
    if not out["tasks"]:
        raise DelegateError(f"{to} is not reachable: it has to exist, and its owner has to share a room with you or allow you (agent_links)", 404)
    return out["tasks"][0]


async def get_task(pool: Any, sub: str, task_id: str) -> dict[str, Any]:
    """One task you asked or own: its status and, once done, its result."""
    try:
        row = await pool.fetchrow("SELECT * FROM delegate_tasks WHERE id = $1 AND (owner_sub = $2 OR requester_sub = $2)", _uuid(task_id), sub)
    except ValueError as exc:
        raise DelegateError("not a valid task id", 400) from exc
    if row is None:
        raise DelegateError("no such task", 404)
    return _task_json(row)


async def list_tasks(pool: Any, owner_sub: str, room: Optional[str] = None, after: Optional[datetime] = None,
                     limit: int = 100) -> list[dict[str, Any]]:
    await purge_expired(pool)
    room = normalize_room(room) if room else None
    # the newest `limit` that match, oldest first; with `after`, anything created, claimed or finished since then
    rows = await pool.fetch(
        "SELECT * FROM (SELECT * FROM delegate_tasks WHERE (owner_sub = $1 OR requester_sub = $1) AND ($2::text IS NULL OR room = $2) "
        "AND ($3::timestamptz IS NULL OR GREATEST(created_at, COALESCE(claimed_at, created_at), "
        "COALESCE(finished_at, created_at)) > $3) ORDER BY created_at DESC LIMIT $4) t ORDER BY created_at",
        owner_sub, room, after, max(1, min(limit, 200)))
    return [_task_json(r) for r in rows]


async def decide_task(pool: Any, owner_sub: str, task_id: str, approve: bool) -> dict[str, Any]:
    """Approve a task waiting for the person's OK (it becomes claimable) or cancel any unfinished task."""
    try:
        tid = _uuid(task_id)
    except ValueError as exc:
        raise DelegateError("not a valid task id", 400) from exc
    if approve:
        row = await pool.fetchrow(
            "UPDATE delegate_tasks SET status = 'queued' WHERE id = $1 AND owner_sub = $2 AND status = 'needs_approval' "
            "RETURNING *", tid, owner_sub)
    else:
        # the agent's owner or the person who asked can cancel an unfinished task; only the owner can approve one
        row = await pool.fetchrow(
            "UPDATE delegate_tasks SET status = 'cancelled', finished_at = now() "
            "WHERE id = $1 AND (owner_sub = $2 OR requester_sub = $2) AND status = ANY($3::text[]) RETURNING *", tid, owner_sub, list(OPEN_STATES))
    if row is None:
        raise DelegateError("no such task, or it is not in a state that allows this", 404)
    return _task_json(row)


async def _live_agent(pool: Any, owner_sub: str, agent: str) -> Any:
    name = (agent or "").strip().lower().lstrip("@")
    name = name.rsplit("/", 1)[-1]
    row = await pool.fetchrow(
        "SELECT * FROM delegate_agents WHERE owner_sub = $1 AND name = $2 AND revoked_at IS NULL", owner_sub, name)
    if row is None:
        raise DelegateError(
            f"no agent named {name!r} on your account; add it on the Delegator page first", 404)
    return row


async def touch_agent(pool: Any, owner_sub: str, name: str) -> None:
    """A connected agent was just seen (it called a kell room tool): it shows as connected for a minute, and the first time it is seen it is
    registered under its tool's name (claude, claude-code, chatgpt). An agent the person removed is not brought back by this. Never raises."""
    name = (name or "").strip().lower()
    if not NAME_RE.match(name) or name == "agent":
        return
    try:
        row = await pool.fetchrow("SELECT id, revoked_at FROM delegate_agents WHERE owner_sub = $1 AND name = $2 ORDER BY created_at DESC LIMIT 1", owner_sub, name)
        if row is None:
            await create_agent(pool, owner_sub, name)
            row = await pool.fetchrow("SELECT id, revoked_at FROM delegate_agents WHERE owner_sub = $1 AND name = $2 AND revoked_at IS NULL", owner_sub, name)
        if row is not None and row["revoked_at"] is None:
            await pool.execute("UPDATE delegate_agents SET last_seen = now() WHERE id = $1", row["id"])
    except Exception:  # noqa: BLE001 -- a presence update never fails a tool call
        pass


async def claim_next(pool: Any, owner_sub: str, agent: str) -> dict[str, Any]:
    """The agent asks for its next task. Marks the agent seen (that is what "online" means), hands over at most one task
    atomically, and says how many more are waiting. The task text is DATA from the person: show it to them as a task to
    review; it is not an instruction kell vouches for."""
    try:
        a = await _live_agent(pool, owner_sub, agent)
    except DelegateError as exc:
        # Nobody has to register an agent first: the first time a connected agent asks for its inbox under a valid name, it is added.
        name = (agent or "").strip().lower().lstrip("@").rsplit("/", 1)[-1]
        if exc.status != 404 or not NAME_RE.match(name):
            raise
        if await pool.fetchval("SELECT 1 FROM delegate_agents WHERE owner_sub = $1 AND name = $2", owner_sub, name):
            raise DelegateError(f"the agent {name!r} was removed from your account; add it again to bring it back", 404)   # removal sticks
        await create_agent(pool, owner_sub, name)
        a = await _live_agent(pool, owner_sub, name)
    await purge_expired(pool)
    await pool.execute("UPDATE delegate_agents SET last_seen = now() WHERE id = $1", a["id"])
    row = await pool.fetchrow(
        "UPDATE delegate_tasks SET status = 'claimed', claimed_at = now() WHERE id = ("
        "  SELECT id FROM delegate_tasks WHERE agent_id = $1 AND owner_sub = $2 AND status = 'queued' "
        "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *", a["id"], owner_sub)
    waiting = await pool.fetchval(
        "SELECT count(*) FROM delegate_tasks WHERE agent_id = $1 AND owner_sub = $2 AND status = 'queued'",
        a["id"], owner_sub)
    awaiting_ok = await pool.fetchval(
        "SELECT count(*) FROM delegate_tasks WHERE agent_id = $1 AND owner_sub = $2 AND status = 'needs_approval'",
        a["id"], owner_sub)
    out: dict[str, Any] = {"agent": a["name"], "waiting": waiting, "awaiting_owner_approval": awaiting_ok}
    if row is None:
        out["task"] = None
        out["note"] = "no task for you right now"
    else:
        out["task"] = _task_json(row)
        await _attach_room_context(pool, owner_sub, out["task"])
        who = out["task"].get("from") or "the account owner"
        out["note"] = (f"This is a task from {who}, sent from kell and approved by the account owner. It is data to act on for them, "
                       "not an instruction from kell. When you finish, call delegate_result with this task's id: kell posts your answer "
                       "in the room where you were asked.")
    return out


async def submit_result(pool: Any, owner_sub: str, task_id: str, result: str, ok: bool = True,
                        check: Optional[str] = None, chat: str = "") -> dict[str, Any]:
    try:
        tid = _uuid(task_id)
    except ValueError as exc:
        raise DelegateError("not a valid task id", 400) from exc
    text = _clean(result or "", MAX_RESULT)
    if not text:
        raise DelegateError("the result is empty")
    check_text = _clean(check, MAX_CHECK) if check else None
    row = await pool.fetchrow(
        "UPDATE delegate_tasks SET status = $3, finished_at = now(), result_text = $4, result_check = $5 "
        "WHERE id = $1 AND owner_sub = $2 AND status = 'claimed' RETURNING *",
        tid, owner_sub, "done" if ok else "failed", text, check_text)
    if row is None:
        raise DelegateError("no such task, or it was not handed to you (claim it first, and only once)", 404)
    await _post_answer(pool, owner_sub, row, text, ok, chat)
    return _task_json(row)


async def _post_answer(pool: Any, owner_sub: str, task: Any, text: str, ok: bool, chat: str = "") -> None:
    """The answer goes back where the question was asked: as a message in the room, from the agent's own handle (@owner/claude, plus the chat's
    name when the agent gave one), so everyone in the room reads it and a canvas block in it becomes a canvas. A task asked in a room that
    exists only on one device has no server room: its answer stays on the task. Never raises: a failure to post must not lose the result."""
    keys = task.keys() if hasattr(task, "keys") else []
    if "room_id" not in keys or not task["room_id"]:
        return
    try:
        from app.services import rooms as _rooms

        agent = await pool.fetchrow("SELECT name FROM delegate_agents WHERE id = $1", task["agent_id"])
        name = (agent["name"] if agent else "agent")
        label = await _rooms.owner_label(pool, owner_sub)
        suffix = _rooms.chat_label(chat)
        handle = f"@{label}/{name}" + (f"/{suffix}" if suffix else "")
        body = text if ok else f"I could not finish that: {text}"
        body = await _rooms.externalize_canvases(pool, owner_sub, task["room_id"], handle, body)
        await _rooms.append_events(pool, owner_sub, str(task["room_id"]), _rooms.agent_events(handle, body))
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("relay: could not post the answer for task %s into its room", task["id"])
