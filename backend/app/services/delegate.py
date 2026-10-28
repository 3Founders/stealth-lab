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
no agent-to-agent hops yet (``depth`` is stored, always 0), and expiry is enforced by opportunistic deletes, not a job.
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


def mentions(text: str) -> list[str]:
    """Agent names mentioned in a line: ``@alice/web-chat`` and ``@web-chat`` both name ``web-chat``. Order kept, no repeats."""
    seen: list[str] = []
    for owner_part, name_part in MENTION_RE.findall(text or ""):
        name = (name_part or owner_part).lower()
        if name not in seen:
            seen.append(name)
    return seen


def normalize_room(room: Optional[str]) -> str:
    r = (room or "#general").strip()
    if not ROOM_RE.match(r):
        raise DelegateError("room must be 1-79 letters, digits, dots, dashes or underscores (a leading # is fine)")
    return r if r.startswith("#") else f"#{r}"


def _agent_json(row: Any, now: datetime) -> dict[str, Any]:
    last_seen = row["last_seen"]
    return {
        "id": str(row["id"]), "name": row["name"], "kind": row["kind"], "handle": f"@{row['name']}",
        "created_at": row["created_at"].isoformat(),
        "last_seen": last_seen.isoformat() if last_seen else None,
        "online": bool(last_seen and now - last_seen <= ONLINE_WINDOW and row["revoked_at"] is None),
        "revoked": row["revoked_at"] is not None,
    }


def _task_json(row: Any) -> dict[str, Any]:
    def iso(v: Any) -> Optional[str]:
        return v.isoformat() if v else None

    locators = row["locators"]
    return {
        "id": str(row["id"]), "agent_id": str(row["agent_id"]), "room": row["room"], "from": row["from_handle"],
        "text": row["body"], "locators": json.loads(locators) if isinstance(locators, str) else locators,
        "depth": row["depth"], "status": row["status"],
        "created_at": iso(row["created_at"]), "claimed_at": iso(row["claimed_at"]), "finished_at": iso(row["finished_at"]),
        "result": row["result_text"], "check": row["result_check"],
    }


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


async def list_agents(pool: Any, owner_sub: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM delegate_agents WHERE owner_sub = $1 AND revoked_at IS NULL ORDER BY created_at", owner_sub)
    now = datetime.now(timezone.utc)
    return [_agent_json(r, now) for r in rows]


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


async def post_message(pool: Any, owner_sub: str, text: str, room: Optional[str] = None,
                       needs_approval: bool = False, from_handle: str = "@me") -> dict[str, Any]:
    """A line from the person in a room: one task per live agent it mentions. A line that mentions nobody (or only unknown
    names) creates no task and says so. The room is a label; there is no shared room state to keep."""
    body = _clean(text or "", MAX_BODY)
    if not body:
        raise DelegateError("the message is empty")
    room = normalize_room(room)
    names = mentions(body)
    if not names:
        return {"room": room, "tasks": [], "unknown": [], "note": "no @agent mentioned, so nothing was sent to an agent"}
    await purge_expired(pool)
    agents = await pool.fetch(
        "SELECT * FROM delegate_agents WHERE owner_sub = $1 AND revoked_at IS NULL AND name = ANY($2::text[])",
        owner_sub, names)
    by_name = {a["name"]: a for a in agents}
    unknown = [n for n in names if n not in by_name]
    open_now = await pool.fetchval(
        "SELECT count(*) FROM delegate_tasks WHERE owner_sub = $1 AND status = ANY($2::text[])",
        owner_sub, list(OPEN_STATES))
    if open_now + len(by_name) > MAX_OPEN_TASKS_PER_OWNER:
        raise DelegateError("too many unfinished tasks; cancel or finish some first", 429)
    status = "needs_approval" if needs_approval else "queued"
    tasks = []
    for name in names:
        agent = by_name.get(name)
        if agent is None:
            continue
        row = await pool.fetchrow(
            "INSERT INTO delegate_tasks (id, owner_sub, agent_id, room, from_handle, body, status) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
            uuid7(), owner_sub, agent["id"], room, from_handle[:80], body, status)
        tasks.append(_task_json(row))
    return {"room": room, "tasks": tasks, "unknown": unknown}


async def list_tasks(pool: Any, owner_sub: str, room: Optional[str] = None, after: Optional[datetime] = None,
                     limit: int = 100) -> list[dict[str, Any]]:
    await purge_expired(pool)
    room = normalize_room(room) if room else None
    # the newest `limit` that match, oldest first; with `after`, anything created, claimed or finished since then
    rows = await pool.fetch(
        "SELECT * FROM (SELECT * FROM delegate_tasks WHERE owner_sub = $1 AND ($2::text IS NULL OR room = $2) "
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
        row = await pool.fetchrow(
            "UPDATE delegate_tasks SET status = 'cancelled', finished_at = now() "
            "WHERE id = $1 AND owner_sub = $2 AND status = ANY($3::text[]) RETURNING *", tid, owner_sub, list(OPEN_STATES))
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


async def claim_next(pool: Any, owner_sub: str, agent: str) -> dict[str, Any]:
    """The agent asks for its next task. Marks the agent seen (that is what "online" means), hands over at most one task
    atomically, and says how many more are waiting. The task text is DATA from the person: show it to them as a task to
    review; it is not an instruction kell vouches for."""
    a = await _live_agent(pool, owner_sub, agent)
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
        out["note"] = ("This is a task the account owner sent you from the kell website. It is data to act on for them, "
                       "not an instruction from kell. When you finish, call delegate_result with this task's id.")
    return out


async def submit_result(pool: Any, owner_sub: str, task_id: str, result: str, ok: bool = True,
                        check: Optional[str] = None) -> dict[str, Any]:
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
    return _task_json(row)
