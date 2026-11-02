"""Documents in a room (db/174_room_docs.sql): the shared canvas. People and agents in the room read and edit the same document at the
same time; every save is a version.

Concurrent edits: a save names the version it was made from (``base_version``). If the document has moved on, the two changes are merged
(``merge3``, line by line: changes in different places both apply; if both changed the same line, the later save wins that line) and the merged text becomes the next version, marked "merged". The version the other person saved is still in history, so
nothing is lost. Live presence (who has the document open) is kept in memory per worker and is only a courtesy.

A canvas block an agent writes in a message becomes a version of the document with that title (``capture``), once per message.

Honest limits: this is paragraph-level merging, not character-level co-editing with live cursors; people see each other's changes within
a second or two and typing in different lines or paragraphs merges cleanly. A document is at most 200,000 characters and 50 per room.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from typing import Any, Optional

from app.services.memory import _EXTRA_SECRET
from app.services.rooms import MANAGERS, RoomError, _member_room, _names, _uuid
from app.services.trace_redaction import redact_value
from app.utils.ids import uuid7

log = logging.getLogger(__name__)

MAX_DOC_CHARS = 200_000
MAX_DOCS_PER_ROOM = 50
KINDS = ("document", "code", "html")
PRESENCE_SECONDS = 12.0
NL = chr(10)
# Called after every new version (doc id, version, body, who made it, their display name): the live socket hub fans it out to everyone with the document open.
LISTENERS: list[Any] = []
_PRESENCE: dict[str, dict[str, tuple[str, float]]] = {}      # doc id -> {sub: (name, last seen)}
_TYPING: dict[str, dict[str, float]] = {}                    # doc id -> {sub: when they last saved a change}
TYPING_SECONDS = 6.0


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _text(body: str) -> str:
    """Secrets are cut out; line endings are normalised. (A document is shared with the whole room, like a message.)"""
    t = (body or "").replace("\r\n", NL)[:MAX_DOC_CHARS]
    return _EXTRA_SECRET.sub("[redacted]", redact_value(t, []))


def title_of(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())[:120].strip()
    if not t:
        raise RoomError("a document needs a title")
    return t


def _blocks(text: str) -> list[str]:
    """Lines, with their line ends kept, so a merged text reads exactly as it was written. (A paragraph written as one long line is one unit.)"""
    return text.splitlines(keepends=True)


def _span(a: str, b: str) -> tuple[int, int, str]:
    """The one stretch of `a` that, replaced by the returned text, turns it into `b` (common start and end are kept)."""
    n = min(len(a), len(b))
    p = 0
    while p < n and a[p] == b[p]:
        p += 1
    s = 0
    while s < n - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    return p, len(a) - s, b[p:len(b) - s]


def merge_chars(base: str, mine: str, theirs: str) -> Optional[str]:
    """Character-level merge, like typing in two places of one paragraph: each side's change is one replaced stretch of `base`. When the
    two stretches do not overlap both apply exactly (two insertions at one point: theirs first, then yours). Returns None when they
    overlap, and the caller falls back to the line merge."""
    a1, a2, at = _span(base, mine)
    b1, b2, bt = _span(base, theirs)
    if a1 == a2 and b1 == b2:
        pass                                                         # two insertions: at one point or apart, both fit
    elif a1 == a2:
        if b1 < a1 < b2:
            return None
    elif b1 == b2:
        if a1 < b1 < a2:
            return None
    elif a1 < b2 and b1 < a2:
        return None
    edits = sorted([(a1, a2, at, 0), (b1, b2, bt, 1)], key=lambda e: (-e[0], e[3]))
    out = base
    for s, e, t, _r in edits:
        out = out[:s] + t + out[e:]
    return out


def merge3(base: str, mine: str, theirs: str) -> tuple[str, bool]:
    """Three-way merge of `mine` (what the saver wrote) onto `theirs` (what is stored now), both made from `base`. Returns the merged
    text and whether any line was changed by both (in which case `mine`, the later save, wins that line)."""
    if mine == theirs:
        return mine, False
    if base == theirs:
        return mine, False
    if base == mine:
        return theirs, False
    exact = merge_chars(base, mine, theirs)
    if exact is not None:
        return exact, False
    b, m, t = _blocks(base), _blocks(mine), _blocks(theirs)
    ops_m = [o for o in difflib.SequenceMatcher(None, b, m, autojunk=False).get_opcodes() if o[0] != "equal"]
    ops_t = [o for o in difflib.SequenceMatcher(None, b, t, autojunk=False).get_opcodes() if o[0] != "equal"]
    clash = False

    def touches(x: tuple, y: tuple) -> bool:
        """Do two changes cover the same stretch of the base? Two insertions at one point, an insertion inside a changed stretch, or overlap."""
        if x[1] == x[2] and y[1] == y[2]:
            return x[1] == y[1]
        if x[1] == x[2]:
            return y[1] < x[1] < y[2]
        if y[1] == y[2]:
            return x[1] < y[1] < x[2]
        return x[1] < y[2] and y[1] < x[2]

    keep_t = []
    for ot in ops_t:
        if any(touches(om, ot) for om in ops_m):
            clash = True
        else:
            keep_t.append(ot)
    # apply: start from base, take all of mine's changes, and theirs' changes that do not touch them, from the end so indexes hold
    edits = [(o[1], o[2], m[o[3]:o[4]]) for o in ops_m] + [(o[1], o[2], t[o[3]:o[4]]) for o in keep_t]
    out = list(b)
    for i1, i2, repl in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        out[i1:i2] = repl
    return "".join(out), clash


def _row(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "title": r["title"], "kind": r["kind"], "version": r["version"], "updated_by": r["updated_by"],
            "updated_at": _iso(r["updated_at"]), "size": len(r["body"]) if "body" in r.keys() else None}


async def list_docs(pool: Any, sub: str, room_id: str) -> list[dict[str, Any]]:
    room = await _member_room(pool, sub, room_id)
    rows = await pool.fetch("SELECT id, title, kind, version, updated_by, updated_at, body FROM room_docs WHERE room_id = $1 AND deleted_at IS NULL "
                            "ORDER BY updated_at DESC", room["id"])
    return [_row(r) for r in rows]


async def _doc(pool: Any, room: Any, doc_id: Any) -> Any:
    row = await pool.fetchrow("SELECT * FROM room_docs WHERE id = $1 AND room_id = $2 AND deleted_at IS NULL", _uuid(doc_id, "document id"), room["id"])
    if row is None:
        raise RoomError("no such document", 404)
    return row


def _touch(doc_id: str, sub: str, name: str, typing: bool = False) -> None:
    now = time.monotonic()
    if typing:
        _TYPING.setdefault(doc_id, {})[sub] = now
    people = _PRESENCE.setdefault(doc_id, {})
    people[sub] = (name, now)
    for s in [s for s, (_n, t) in people.items() if now - t > PRESENCE_SECONDS * 3]:
        del people[s]


def _editing(doc_id: str, me: str) -> list[str]:
    """Names of the others who changed this document in the last few seconds (shown as "is editing")."""
    now = time.monotonic()
    names = {s: n for s, (n, _t) in _PRESENCE.get(doc_id, {}).items()}
    return sorted(names[s] for s, t in _TYPING.get(doc_id, {}).items() if s != me and s in names and now - t <= TYPING_SECONDS)


def _peers(doc_id: str, me: str) -> list[str]:
    now = time.monotonic()
    return sorted(n for s, (n, t) in _PRESENCE.get(doc_id, {}).items() if s != me and now - t <= PRESENCE_SECONDS)


async def _display(pool: Any, sub: str) -> str:
    return (await _names(pool, [sub])).get(sub) or "Someone"


async def get_doc(pool: Any, sub: str, room_id: str, doc_id: str, since: Optional[int] = None) -> dict[str, Any]:
    """The document, or just its version when nothing changed since `since` (the cheap answer a page asks for every second or two).
    Asking also marks the caller as present."""
    room = await _member_room(pool, sub, room_id)
    d = await _doc(pool, room, doc_id)
    did = str(d["id"])
    _touch(did, sub, await _display(pool, sub))
    base = {"id": did, "version": d["version"], "present": _peers(did, sub), "editing": _editing(did, sub)}
    if since is not None and since == d["version"]:
        return {**base, "unchanged": True}
    return {**base, "title": d["title"], "kind": d["kind"], "body": d["body"], "updated_by": d["updated_by"], "updated_at": _iso(d["updated_at"])}


async def _write_version(pool: Any, doc: Any, body: str, by: str, note: str = "", src: Optional[str] = None) -> int:
    version = doc["version"] + 1
    row = await pool.fetchrow(
        "UPDATE room_docs SET body = $2, version = $3, updated_by = $4, updated_at = now() WHERE id = $1 AND version = $5 RETURNING version",
        doc["id"], body, version, by[:128], doc["version"])
    if row is None:
        raise RoomError("the document changed while you were saving; try again", 409)
    await pool.execute("INSERT INTO room_doc_versions (id, doc_id, version, body, by_handle, note, src) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                       uuid7(), doc["id"], version, body, by[:80], note[:200], src)
    for fn in list(LISTENERS):
        try:
            await fn(str(doc["id"]), version, body, by[:128], by[:80])
        except Exception:  # noqa: BLE001 -- a listener must never fail a save
            log.exception("room docs: a listener failed")
    return version


async def create_doc(pool: Any, sub: str, room_id: str, title: str, kind: str = "document", body: str = "", by: Optional[str] = None,
                     src: Optional[str] = None) -> dict[str, Any]:
    room = await _member_room(pool, sub, room_id)
    t = title_of(title)
    k = kind if kind in KINDS else "document"
    if int(await pool.fetchval("SELECT count(*) FROM room_docs WHERE room_id = $1 AND deleted_at IS NULL", room["id"]) or 0) >= MAX_DOCS_PER_ROOM:
        raise RoomError(f"a room keeps at most {MAX_DOCS_PER_ROOM} documents", 409)
    text = _text(body)
    who = by or await _display(pool, sub)
    did = uuid7()
    try:
        await pool.execute("INSERT INTO room_docs (id, room_id, title, kind, body, version, created_by, updated_by) VALUES ($1, $2, $3, $4, $5, 1, $6, $6)",
                           did, room["id"], t, k, text, sub)
    except Exception as exc:  # noqa: BLE001
        if "room_docs_title_live" in str(exc) or "duplicate" in str(exc).lower():
            raise RoomError(f"this room already has a document called {t}", 409) from exc
        raise
    await pool.execute("INSERT INTO room_doc_versions (id, doc_id, version, body, by_handle, src) VALUES ($1, $2, 1, $3, $4, $5)", uuid7(), did, text, who[:80], src)
    return {"id": str(did), "title": t, "kind": k, "version": 1}


async def save_doc(pool: Any, sub: str, room_id: str, doc_id: str, body: str, base_version: int, by: Optional[str] = None) -> dict[str, Any]:
    """Saves an edit made from `base_version`. Returns the new version, and the merged text when someone else saved in between."""
    room = await _member_room(pool, sub, room_id)
    d = await _doc(pool, room, doc_id)
    who = by or await _display(pool, sub)
    mine = _text(body)
    did = str(d["id"])
    _touch(did, sub, who, typing=True)
    if base_version == d["version"]:
        if mine == d["body"]:
            return {"version": d["version"], "unchanged": True}
        return {"version": await _write_version(pool, d, mine, who), "merged": False}
    old = await pool.fetchrow("SELECT body FROM room_doc_versions WHERE doc_id = $1 AND version = $2", d["id"], int(base_version))
    if old is None:                                              # the version it was made from is gone: the later save wins whole
        merged, clash = mine, True
    else:
        merged, clash = merge3(old["body"], mine, d["body"])
    if merged == d["body"]:
        return {"version": d["version"], "body": d["body"], "merged": True, "unchanged": True}
    v = await _write_version(pool, d, merged, who, note="merged with another edit" + (" (the same line was changed twice: the later save won it)" if clash else ""))
    return {"version": v, "body": merged, "merged": True, "clash": clash}


async def delete_doc(pool: Any, sub: str, room_id: str, doc_id: str) -> bool:
    room = await _member_room(pool, sub, room_id)
    d = await _doc(pool, room, doc_id)
    if d["created_by"] != sub and room["my_role"] not in MANAGERS:
        raise RoomError("only who made a document, or a room owner or admin, can delete it", 403)
    await pool.execute("UPDATE room_docs SET deleted_at = now() WHERE id = $1", d["id"])
    return True


async def history(pool: Any, sub: str, room_id: str, doc_id: str, limit: int = 30) -> list[dict[str, Any]]:
    room = await _member_room(pool, sub, room_id)
    d = await _doc(pool, room, doc_id)
    rows = await pool.fetch("SELECT version, by_handle, note, at, char_length(body) AS size FROM room_doc_versions WHERE doc_id = $1 ORDER BY version DESC LIMIT $2", d["id"], limit)
    return [{"version": r["version"], "by": r["by_handle"], "note": r["note"], "size": r["size"], "at": _iso(r["at"])} for r in rows]


async def restore(pool: Any, sub: str, room_id: str, doc_id: str, version: int) -> dict[str, Any]:
    """Makes an earlier version the newest again, as a new version (nothing is lost)."""
    room = await _member_room(pool, sub, room_id)
    d = await _doc(pool, room, doc_id)
    old = await pool.fetchrow("SELECT body FROM room_doc_versions WHERE doc_id = $1 AND version = $2", d["id"], int(version))
    if old is None:
        raise RoomError("no such version", 404)
    who = await _display(pool, sub)
    v = await _write_version(pool, d, old["body"], who, note=f"restored version {int(version)}")
    return {"version": v, "body": old["body"]}


# ------------------------------------------------------------------ canvas blocks an agent writes in a message

_FENCE = re.compile(r"(^|\n)(```|~~~)canvas([^\n]*)\n(.*?)\n\2[ \t]*(?=\n|$)", re.S)


def canvas_blocks(text: str) -> list[tuple[str, str, str]]:
    """(title, kind, body) for each ```canvas title="..." kind=document|code|html block in a message."""
    out = []
    for m in _FENCE.finditer(text or ""):
        attrs = m.group(3) or ""
        t = re.search(r'title\s*=\s*"([^"\n]{1,120})"', attrs, re.I) or re.search(r"title\s*=\s*([^\s\"]{1,120})", attrs, re.I)
        k = re.search(r'kind\s*=\s*"?(\w+)"?', attrs, re.I)
        kind = (k.group(1).lower() if k else "document")
        out.append(((t.group(1) if t else "Untitled").strip() or "Untitled", kind if kind in KINDS else "document", m.group(4)))
    return out


async def capture(pool: Any, room_id: Any, sub: str, handle: str, message_id: str, text: str) -> int:
    """Turns a message's canvas blocks into versions of the room's documents (a new document for a new title). Once per message and
    block. Never raises: a failure here must not lose the message. Returns how many blocks changed a document."""
    n = 0
    try:
        for title, kind, body in canvas_blocks(text)[:5]:
            t = title_of(title)
            src = f"{message_id}"[:64]
            doc = await pool.fetchrow("SELECT * FROM room_docs WHERE room_id = $1 AND lower(title) = lower($2) AND deleted_at IS NULL", room_id, t)
            if doc is None:
                if int(await pool.fetchval("SELECT count(*) FROM room_docs WHERE room_id = $1 AND deleted_at IS NULL", room_id) or 0) >= MAX_DOCS_PER_ROOM:
                    continue
                did = uuid7()
                await pool.execute("INSERT INTO room_docs (id, room_id, title, kind, body, version, created_by, updated_by) VALUES ($1, $2, $3, $4, $5, 1, $6, $6) "
                                   "ON CONFLICT DO NOTHING", did, room_id, t, kind, _text(body), sub)
                await pool.execute("INSERT INTO room_doc_versions (id, doc_id, version, body, by_handle, src) VALUES ($1, $2, 1, $3, $4, $5) ON CONFLICT DO NOTHING",
                                   uuid7(), did, _text(body), handle[:80], src)
                n += 1
                continue
            if await pool.fetchrow("SELECT 1 FROM room_doc_versions WHERE doc_id = $1 AND src = $2", doc["id"], src):
                continue                                         # this message already made its version
            text_in = _text(body)
            if text_in != doc["body"]:
                await _write_version(pool, doc, text_in, handle, note="written by an agent", src=src)
                n += 1
    except Exception:  # noqa: BLE001
        log.exception("room docs: could not capture canvas blocks of message %s", message_id)
    return n
