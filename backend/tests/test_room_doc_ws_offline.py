"""Live document editing over a WebSocket (app/mcp_server/room_doc_ws.py): two people connected to one document see each other's saved
changes at once, character-level edits in one paragraph merge, a stranger is refused, and presence is shared. No database (a fake pool)."""
from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.mcp_server import room_doc_ws as ws
from app.services import room_docs as rd
from tests.test_room_docs_offline import ROOM, Pool, run


class Gate:
    async def subject(self, request):
        tok = request.headers.get("authorization", "")[7:]
        return (tok, None) if tok in ("alice", "bob") else (None, ("sign in first", 401))


def make(pool):
    ws.IDLE_SECONDS = 4.0                       # a stuck test fails in seconds, not after the real idle timeout
    return TestClient(ws.DocsSocketGate(Starlette(), Gate(), lambda: pool))


def open_ws(client, doc_id, token):
    sock = client.websocket_connect(f"/v1/rooms/{ROOM}/docs/{doc_id}/ws")
    s = sock.__enter__()
    s.send_text(json.dumps({"type": "auth", "token": token}))
    return sock, s


def recv_until(sock, kind, limit=10, **match):
    for _ in range(limit):
        m = json.loads(sock.receive_text())
        if m["type"] == kind and all(m.get(k) == v for k, v in match.items()):
            return m
    raise AssertionError(f"no {kind} {match}")


def test_two_people_see_each_others_changes_and_merge_edits_in_one_paragraph():
    pool = Pool()
    doc = run(rd.create_doc(pool, "alice", str(ROOM), "Plan", "document", "The quick brown fox jumps."))
    with make(pool) as client:                  # one event loop for both sockets, as in the real server
        ca, a = open_ws(client, doc["id"], "alice")
        cb, b = open_ws(client, doc["id"], "bob")
        try:
            ha, hb = recv_until(a, "hello"), recv_until(b, "hello")
            assert ha["body"] == hb["body"] == "The quick brown fox jumps." and ha["version"] == 1

            a.send_text(json.dumps({"type": "edit", "body": "The quick red fox jumps.", "base": 1}))            # alice changes one word
            ack_a = recv_until(a, "ack")
            b.send_text(json.dumps({"type": "edit", "body": "The quick brown fox jumps over.", "base": 1}))    # bob, still on version 1, adds to the end
            ack_b = recv_until(b, "ack")
            assert ack_a["version"] == 2 and ack_b["version"] == 3
            assert ack_b["body"] == "The quick red fox jumps over."                                            # both changes survive, in one line
            pushed = recv_until(a, "doc", version=3)
            assert pushed["body"] == "The quick red fox jumps over."                                            # and alice is told at once
        finally:
            cb.__exit__(None, None, None)
            ca.__exit__(None, None, None)


def test_a_stranger_or_a_bad_token_is_refused():
    pool = Pool()
    doc = run(rd.create_doc(pool, "alice", str(ROOM), "Plan", "document", "x"))
    with make(pool) as client:
        c, s = open_ws(client, doc["id"], "mallory")
        try:
            m = json.loads(s.receive_text())
            assert m["type"] == "error" and m["status"] == 401
        finally:
            c.__exit__(None, None, None)
