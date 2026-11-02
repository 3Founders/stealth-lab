"""Live editing of a room's documents over a WebSocket: ``/v1/rooms/{room_id}/docs/{doc_id}/ws``.

How it works (the model Google Docs is described as using, in a smaller form): every edit goes to the server, which holds the one ordered
history of the document. A client sends its new text together with the version it was typing on; the server rebases that change over
whatever was saved since (character-level when the changes do not overlap, else line-level; ``room_docs.merge3``), saves the result as the
next version, and pushes it to everyone with the document open the moment it is saved. A client shows its own typing at once and folds
others' changes in around its cursor. Who is here and who is typing travel on the same socket and are never written to the document.

Messages (JSON). Client to server: ``{"type":"auth","token":...}`` first; then ``{"type":"edit","body":...,"base":N}``, ``{"type":"ping"}``.
Server to client: ``hello`` (the document and who is here), ``ack`` (your edit's version, and the merged text if it changed), ``doc`` (a new
version from anyone), ``presence``, ``error``.

Honest limits: one process holds the sockets (the server runs with one worker); a client that cannot open a socket keeps working through
the plain HTTP routes, which poll. There are no remote cursors, only who is here and who is typing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.services import room_docs as rd
from app.services.rooms import RoomError, _Limiter

log = logging.getLogger(__name__)

PATH = re.compile(r"^/v1/rooms/([^/]+)/docs/([^/]+)/ws$")
MAX_MESSAGE = 420_000
MAX_SOCKETS_PER_PERSON = 12
IDLE_SECONDS = 70.0
EDITS = _Limiter(600, 60.0)            # edits per person per minute (a socket sends one every ~100 ms while typing)


class _Conn:
    def __init__(self, ws: WebSocket, sub: str, name: str) -> None:
        self.ws, self.sub, self.name = ws, sub, name
        self.last_edit = 0.0


_DOCS: dict[str, set[_Conn]] = {}      # doc id -> open sockets
_PER_PERSON: dict[str, int] = {}


async def _send(c: _Conn, msg: dict[str, Any]) -> None:
    try:
        await c.ws.send_text(json.dumps(msg, default=str))
    except Exception:  # noqa: BLE001 -- a socket that cannot take it is closing; its own loop cleans up
        log.debug("room docs socket: send failed", exc_info=True)


def _presence(doc_id: str) -> dict[str, Any]:
    now = time.monotonic()
    conns = _DOCS.get(doc_id, set())
    return {"type": "presence", "present": sorted({c.name for c in conns}),
            "editing": sorted({c.name for c in conns if now - c.last_edit <= rd.TYPING_SECONDS})}


async def _broadcast_presence(doc_id: str) -> None:
    msg = _presence(doc_id)
    for c in list(_DOCS.get(doc_id, ())):
        await _send(c, msg)


async def _on_version(doc_id: str, version: int, body: str, by_sub: str, by_name: str) -> None:
    """A new version was saved (from a socket, the HTTP routes or an agent's canvas block): everyone with it open gets it."""
    msg = {"type": "doc", "version": version, "body": body, "by": by_name}
    for c in list(_DOCS.get(doc_id, ())):
        await _send(c, msg)


if _on_version not in rd.LISTENERS:
    rd.LISTENERS.append(_on_version)


def _fake_request(scope: dict[str, Any], token: str) -> Request:
    """The auth gate reads an HTTP request's Authorization header and client address; a socket's token is given the same shape."""
    return Request({"type": "http", "method": "GET", "path": scope.get("path", "/"), "query_string": b"", "client": scope.get("client"),
                    "server": scope.get("server"), "scheme": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


async def handle(scope: dict[str, Any], receive: Any, send: Any, gate: Any, pool_getter: Callable[[], Any]) -> None:
    ws = WebSocket(scope, receive, send)
    m = PATH.match(scope["path"])
    assert m
    room_id, doc_id = m.group(1), m.group(2)
    await ws.accept()
    conn: _Conn | None = None
    try:
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), 15))
        except Exception:  # noqa: BLE001
            await ws.close(code=4401)
            return
        token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
        pool = pool_getter()
        if not isinstance(token, str) or pool is None:
            await ws.close(code=4401)
            return
        subject, refusal = await gate.subject(_fake_request(scope, token))
        if refusal is not None or subject is None:
            await ws.send_text(json.dumps({"type": "error", "error": refusal[0] if refusal else "sign in first", "status": refusal[1] if refusal else 401}))
            await ws.close(code=4401)
            return
        try:
            doc = await rd.get_doc(pool, subject, room_id, doc_id)
        except RoomError as exc:
            await ws.send_text(json.dumps({"type": "error", "error": str(exc), "status": exc.status}))
            await ws.close(code=4404)
            return
        if _PER_PERSON.get(subject, 0) >= MAX_SOCKETS_PER_PERSON:
            await ws.send_text(json.dumps({"type": "error", "error": "too many open documents", "status": 429}))
            await ws.close(code=4429)
            return
        name = await rd._display(pool, subject)
        conn = _Conn(ws, subject, name)
        _DOCS.setdefault(doc_id, set()).add(conn)
        _PER_PERSON[subject] = _PER_PERSON.get(subject, 0) + 1
        await _send(conn, {**_presence(doc_id), **{k: v for k, v in doc.items() if k in ("id", "version", "title", "kind", "body")}, "type": "hello"})
        await _broadcast_presence(doc_id)

        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), IDLE_SECONDS)
            except asyncio.TimeoutError:
                await ws.close(code=4408)
                return
            if len(raw) > MAX_MESSAGE:
                await _send(conn, {"type": "error", "error": "that message is too large", "status": 413})
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "ping":
                rd._touch(doc_id, subject, name)
                await _send(conn, {"type": "pong"})
            elif kind == "edit":
                base = msg.get("base")
                if not isinstance(base, int) or not isinstance(msg.get("body"), str):
                    continue
                if not EDITS.allow(subject):
                    await _send(conn, {"type": "error", "error": "slow down", "status": 429})
                    continue
                conn.last_edit = time.monotonic()
                try:
                    res = await rd.save_doc(pool, subject, room_id, doc_id, msg["body"], base)
                except RoomError as exc:
                    await _send(conn, {"type": "error", "error": str(exc), "status": exc.status})
                    continue
                await _send(conn, {"type": "ack", **res})
                await _broadcast_presence(doc_id)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("room docs socket failed")
    finally:
        if conn is not None:
            _DOCS.get(doc_id, set()).discard(conn)
            if not _DOCS.get(doc_id):
                _DOCS.pop(doc_id, None)
            _PER_PERSON[conn.sub] = max(0, _PER_PERSON.get(conn.sub, 1) - 1)
            await _broadcast_presence(doc_id)


class DocsSocketGate:
    """Wraps the app: a WebSocket to a document's /ws path is served here; everything else goes through untouched."""

    def __init__(self, app: Any, gate: Any, pool_getter: Callable[[], Any]) -> None:
        self.app, self.gate, self.pool_getter = app, gate, pool_getter

    def __getattr__(self, name: str) -> Any:                 # the wrapped app's attributes (state, router, ...) stay reachable
        return getattr(self.app, name)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "websocket" and PATH.match(scope.get("path", "")):
            await handle(scope, receive, send, self.gate, self.pool_getter)
            return
        await self.app(scope, receive, send)
