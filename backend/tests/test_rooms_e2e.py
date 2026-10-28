"""
Rooms against a real Postgres: ownership and membership isolation, idempotent ordered appends, invites, unread counts, edits and
deletes, the HTTP routes with CORS, and the stream.

Needs a Postgres it may create a throwaway database in (DATABASE_URL_LOCAL, from backend/.env or the environment); skips
otherwise, like the other *_e2e.py files. The database is created fresh, migrations 155 (delegate agents, which room members may
reference) and 157 are applied, and it is dropped afterwards. No fakes: the SQL under test is the SQL that ships.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")
from dotenv import dotenv_values  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.mcp_server import delegate_routes as dr  # noqa: E402
from app.mcp_server import room_routes as rr  # noqa: E402
from app.services import rooms as rm  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get("DATABASE_URL_LOCAL") or dotenv_values(ROOT / ".env").get("DATABASE_URL_LOCAL")
pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="DATABASE_URL_LOCAL not set")

ALICE, BOB, CAROL = "sub-alice", "sub-bob", "sub-carol"


def _swap_db(dsn: str, name: str) -> str:
    head, _, tail = dsn.rpartition("/")
    return f"{head}/{name}" + (("?" + tail.split("?", 1)[1]) if "?" in tail else "")


class _Db:
    def __init__(self, real, loop, dsn):
        self._real, self._loop, self._dsn = real, loop, dsn

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture()
def pool():
    name = f"kell_rooms_{uuid.uuid4().hex[:10]}"
    loop = asyncio.new_event_loop()

    async def up():
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'CREATE DATABASE "{name}"')
        await admin.close()
        p = await asyncpg.create_pool(_swap_db(ADMIN_DSN, name), min_size=1, max_size=8)
        for f in ("155_delegate_relay.sql", "157_rooms.sql"):
            await p.execute((ROOT / "db" / f).read_text(encoding="utf-8"))
        return p

    async def down(p):
        await p.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

    rm.LIMITER = rm._Limiter()          # a fresh window per test
    real = loop.run_until_complete(up())
    p = _Db(real, loop, _swap_db(ADMIN_DSN, name))
    yield p
    loop.run_until_complete(down(real))
    loop.close()


def run(pool, coro):
    return pool._loop.run_until_complete(coro)


def ev(text="hello", **over):
    return {"id": str(uuid.uuid4()), "kind": "msg", "body": text, **over}


def send(pool, sub, room_id, *events):
    return run(pool, rm.append_events(pool, sub, room_id, list(events)))


def room(pool, sub=ALICE, title="#general"):
    return run(pool, rm.create_room(pool, sub, title))


def join(pool, owner_room, sub):
    inv = run(pool, rm.create_invite(pool, ALICE, owner_room["id"]))
    return run(pool, rm.redeem_invite(pool, sub, inv["code"])), inv


def test_creating_a_room_is_idempotent_and_private_to_its_members(pool):
    a = room(pool)
    assert a["title"] == "#general" and a["role"] == "owner" and a["mode"] == "open"
    assert room(pool)["id"] == a["id"]                                           # same title, same room
    again = run(pool, rm.create_room(pool, ALICE, "General", a["id"]))
    assert again["id"] == a["id"]
    assert [r["id"] for r in run(pool, rm.list_rooms(pool, ALICE))] == [a["id"]]
    assert run(pool, rm.list_rooms(pool, BOB)) == []
    assert room(pool, BOB)["id"] != a["id"]                                      # Bob's own #general is another room
    with pytest.raises(rm.RoomError):
        run(pool, rm.create_room(pool, ALICE, "bad name/with slash"))
    with pytest.raises(rm.RoomError) as taken:
        run(pool, rm.create_room(pool, CAROL, "#x", a["id"]))                      # someone else's id
    assert taken.value.status == 409


def test_appends_are_idempotent_ordered_and_pageable(pool):
    r = room(pool)
    first, second = ev("one"), ev("two")
    out = send(pool, ALICE, r["id"], first, second)
    assert [a["seq"] for a in out["accepted"]] == [1, 2] and out["rejected"] == []
    retry = send(pool, ALICE, r["id"], first, ev("three"))                         # a retried send plus a new one
    assert retry["accepted"][0] == {"id": first["id"], "seq": 1, "duplicate": True} and retry["accepted"][1]["seq"] == 3
    page = run(pool, rm.list_events(pool, ALICE, r["id"], after=1, limit=1))
    assert [e["body"] for e in page["events"]] == ["two"] and page["more"] is True and page["next"] == 2
    rest = run(pool, rm.list_events(pool, ALICE, r["id"], after=page["next"]))
    assert [e["body"] for e in rest["events"]] == ["three"] and rest["more"] is False and rest["last_seq"] == 3
    assert [e["sender"]["me"] for e in rest["events"]] == [True]


def test_concurrent_senders_get_distinct_dense_seqs(pool):
    r = room(pool)
    join(pool, r, BOB)

    async def burst():
        calls = [rm.append_events(pool, who, r["id"], [ev(f"{who}-{i}")]) for i in range(15) for who in (ALICE, BOB)]
        return await asyncio.gather(*calls)

    outs = run(pool, burst())
    seqs = sorted(o["accepted"][0]["seq"] for o in outs)
    assert seqs == list(range(1, 31))
    assert [e["seq"] for e in run(pool, rm.list_events(pool, ALICE, r["id"], limit=500))["events"]] == seqs


def test_a_non_member_sees_nothing_and_every_route_says_no_such_room(pool):
    r = room(pool)
    send(pool, ALICE, r["id"], ev("secret plan"))
    for call in (rm.list_events(pool, BOB, r["id"]), rm.list_members(pool, BOB, r["id"]),
                 rm.append_events(pool, BOB, r["id"], [ev("hi")]), rm.mark_read(pool, BOB, r["id"], 1),
                 rm.rename_room(pool, BOB, r["id"], "#mine"), rm.delete_room(pool, BOB, r["id"]),
                 rm.create_invite(pool, BOB, r["id"])):
        with pytest.raises(rm.RoomError) as e:
            run(pool, call)
        assert e.value.status == 404
    with pytest.raises(rm.RoomError) as bad:
        run(pool, rm.list_events(pool, ALICE, "not-a-uuid"))
    assert bad.value.status == 400


def test_invites_join_the_room_and_cannot_be_probed(pool):
    r = room(pool)
    send(pool, ALICE, r["id"], ev("before bob"))
    joined, inv = join(pool, r, BOB)
    assert joined == {"room_id": r["id"], "title": "#general", "already_member": False}
    assert [e["body"] for e in run(pool, rm.list_events(pool, BOB, r["id"]))["events"]] == ["before bob"]   # history comes with the room
    assert run(pool, rm.redeem_invite(pool, BOB, inv["code"]))["already_member"] is True
    assert [i["uses"] for i in run(pool, rm.list_invites(pool, ALICE, r["id"]))] == [1]                   # a repeat did not use it up
    for bad in ("", "nonsense", inv["code"][:-4] + "0000", f"rm1_{'0' * 32}_{'0' * 24}"):
        with pytest.raises(rm.RoomError) as e:
            run(pool, rm.redeem_invite(pool, CAROL, bad))
        assert (e.value.status, str(e.value)) == (404, "this invite is not valid")
    run(pool, rm.revoke_invite(pool, ALICE, r["id"], inv["id"]))
    with pytest.raises(rm.RoomError):
        run(pool, rm.redeem_invite(pool, CAROL, inv["code"]))
    with pytest.raises(rm.RoomError) as owner_only:
        run(pool, rm.create_invite(pool, BOB, r["id"]))                                                  # members can't invite
    assert owner_only.value.status == 403


def test_invite_use_limit_and_removal(pool):
    r = room(pool)
    inv = run(pool, rm.create_invite(pool, ALICE, r["id"], days=1, max_uses=1))
    run(pool, rm.redeem_invite(pool, BOB, inv["code"]))
    with pytest.raises(rm.RoomError):
        run(pool, rm.redeem_invite(pool, CAROL, inv["code"]))                                            # used up
    assert sorted(m["sub"] for m in run(pool, rm.list_members(pool, ALICE, r["id"]))) == [ALICE, BOB]
    with pytest.raises(rm.RoomError) as e:
        run(pool, rm.remove_member(pool, BOB, r["id"], ALICE))
    assert e.value.status == 409                                                                         # the owner can't be removed
    run(pool, rm.remove_member(pool, ALICE, r["id"], BOB))
    with pytest.raises(rm.RoomError) as gone:
        run(pool, rm.list_events(pool, BOB, r["id"]))
    assert gone.value.status == 404
    inv2 = run(pool, rm.create_invite(pool, ALICE, r["id"]))
    assert run(pool, rm.redeem_invite(pool, BOB, inv2["code"]))["already_member"] is False              # can come back with a new invite
    run(pool, rm.remove_member(pool, BOB, r["id"], BOB))                                                 # or leave on their own
    with pytest.raises(rm.RoomError):
        run(pool, rm.remove_member(pool, BOB, r["id"], ALICE))


def test_unread_counts_follow_the_person_and_own_sends_count_as_read(pool):
    r = room(pool)
    join(pool, r, BOB)
    send(pool, ALICE, r["id"], ev("one"), ev("two"))
    bob = run(pool, rm.list_rooms(pool, BOB))[0]
    assert bob["unread"] == 2 and bob["last"]["preview"] == "two" and bob["members"] == 2
    assert run(pool, rm.list_rooms(pool, ALICE))[0]["unread"] == 0
    assert run(pool, rm.mark_read(pool, BOB, r["id"], 1))["last_read_seq"] == 1
    assert run(pool, rm.list_rooms(pool, BOB))[0]["unread"] == 1
    assert run(pool, rm.mark_read(pool, BOB, r["id"], 0))["last_read_seq"] == 1                          # never goes backwards
    assert run(pool, rm.mark_read(pool, BOB, r["id"], 999))["last_read_seq"] == 2                        # capped at what exists
    send(pool, BOB, r["id"], ev("bob replies"))
    assert run(pool, rm.list_rooms(pool, BOB))[0]["unread"] == 0


def test_edits_deletes_and_reactions_follow_the_rules(pool):
    r = room(pool)
    join(pool, r, BOB)
    mine, bobs = ev("alice says"), ev("bob says")
    send(pool, ALICE, r["id"], mine)
    send(pool, BOB, r["id"], bobs)
    edit_other = send(pool, BOB, r["id"], ev("hacked", kind="edit", target=mine["id"]))
    assert edit_other["accepted"] == [] and "your own" in edit_other["rejected"][0]["reason"]
    assert send(pool, BOB, r["id"], ev("fixed", kind="edit", target=bobs["id"]))["accepted"]
    assert send(pool, BOB, r["id"], ev("", kind="delete", target=mine["id"]))["accepted"] == []          # not his to delete
    assert send(pool, ALICE, r["id"], ev("", kind="delete", target=bobs["id"]))["accepted"]               # the owner may delete anyone's
    assert send(pool, BOB, r["id"], ev("👍", kind="react", target=mine["id"]))["accepted"]               # anyone may react
    ghost = send(pool, ALICE, r["id"], ev("x", kind="edit", target=str(uuid.uuid4())))
    assert "not in this room" in ghost["rejected"][0]["reason"]
    assert "needs a target" in send(pool, ALICE, r["id"], ev("x", kind="edit"))["rejected"][0]["reason"]


def test_event_rules_depth_redaction_size_and_rate(pool):
    r = room(pool)
    agent = {"sender_kind": "agent", "sender_handle": "@reviewer"}
    assert send(pool, ALICE, r["id"], ev("hop 4", depth=4, **agent))["accepted"]
    refused = send(pool, ALICE, r["id"], ev("hop 5", depth=5, **agent))
    assert "depth limit" in refused["rejected"][0]["reason"] and refused["rejected"][0]["retry"] is False
    person = send(pool, ALICE, r["id"], ev("a person's depth is always 0", depth=3))
    stored = run(pool, rm.list_events(pool, ALICE, r["id"]))["events"]
    assert [e["depth"] for e in stored] == [4, 0] and stored[0]["sender"]["handle"] == "@reviewer" and stored[1]["sender"]["handle"] == "@person"
    assert person["accepted"]
    assert "@name" in send(pool, ALICE, r["id"], ev("x", sender_kind="agent", sender_handle="not a handle"))["rejected"][0]["reason"]
    key = "sk-ant-api03-" + "a" * 40
    send(pool, ALICE, r["id"], ev(f"use {key} for it @reviewer"))
    last = run(pool, rm.list_events(pool, ALICE, r["id"]))["events"][-1]
    assert key not in last["body"] and "REDACTED" in last["body"] and last["mentions"] == ["reviewer"]
    assert key not in run(pool, pool.fetchval("SELECT string_agg(body, '') FROM room_events"))
    assert "empty" in send(pool, ALICE, r["id"], ev("   "))["rejected"][0]["reason"]
    with pytest.raises(rm.RoomError):
        send(pool, ALICE, r["id"], *[ev(str(i)) for i in range(rm.MAX_BATCH + 1)])
    rm.LIMITER = rm._Limiter(limit=2)
    limited = send(pool, ALICE, r["id"], ev("a"), ev("b"), ev("c"))
    assert len(limited["accepted"]) == 2 and limited["rejected"][0]["retry"] is True                      # retry later, not give up


def test_deleting_a_room_removes_it_for_everyone_and_frees_the_name(pool):
    r = room(pool)
    join(pool, r, BOB)
    send(pool, ALICE, r["id"], ev("one"), ev("two"))
    with pytest.raises(rm.RoomError) as e:
        run(pool, rm.delete_room(pool, BOB, r["id"]))
    assert e.value.status == 403
    assert run(pool, rm.delete_room(pool, ALICE, r["id"]))["events_removed"] == 2
    assert run(pool, rm.list_rooms(pool, BOB)) == [] and run(pool, rm.list_rooms(pool, ALICE)) == []
    assert run(pool, pool.fetchval("SELECT count(*) FROM room_events")) == 0
    fresh = room(pool)
    assert fresh["id"] != r["id"] and fresh["title"] == "#general"


def test_rename_is_owner_only_and_unique(pool):
    r = room(pool)
    room(pool, ALICE, "#other")
    join(pool, r, BOB)
    assert run(pool, rm.rename_room(pool, ALICE, r["id"], "Design Review"))["title"] == "#design-review"
    with pytest.raises(rm.RoomError) as e:
        run(pool, rm.rename_room(pool, BOB, r["id"], "#mine"))
    assert e.value.status == 403
    with pytest.raises(rm.RoomError) as dup:
        run(pool, rm.rename_room(pool, ALICE, r["id"], "#other"))
    assert dup.value.status == 409


# ---------------------------------------------------------------- the HTTP routes

class _PerCallPool:
    """The HTTP client runs the app on its own event loop, and an asyncpg pool belongs to the loop that made it. So the route
    tests open a short-lived connection to the same scratch database for each statement (real SQL, real database)."""

    def __init__(self, dsn):
        self._dsn = dsn

    async def _with(self, method, *a):
        con = await asyncpg.connect(self._dsn)
        try:
            return await getattr(con, method)(*a)
        finally:
            await con.close()

    async def fetch(self, *a): return await self._with("fetch", *a)
    async def fetchrow(self, *a): return await self._with("fetchrow", *a)
    async def fetchval(self, *a): return await self._with("fetchval", *a)
    async def execute(self, *a): return await self._with("execute", *a)


class _FakeServer:
    def __init__(self):
        self.routes = []

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.routes.append(Route(path, fn, methods=methods))
            return fn
        return deco


class _HeaderGate(dr.Gate):
    """Same refusals shape as the real gate, but the identity comes from a header so the routes can run without Supabase."""

    async def subject(self, request):
        sub = request.headers.get("x-test-sub")
        return (sub, None) if sub else (None, ("sign in first", 401))


def _client(pool):
    srv = _FakeServer()
    per_call = _PerCallPool(pool._dsn)
    rr.register(srv, _HeaderGate(object(), lambda: per_call, lambda r: False), lambda: per_call)
    return TestClient(Starlette(routes=srv.routes))


def test_routes_end_to_end_with_cors_membership_and_sync(pool):
    c = _client(pool)
    origin = {"Origin": "https://www.kell.org.in"}
    pre = c.options("/v1/rooms/x/read", headers={**origin, "Access-Control-Request-Method": "PUT"})
    assert pre.status_code == 204 and "PUT" in pre.headers["access-control-allow-methods"]
    assert "access-control-allow-origin" not in c.options("/v1/rooms", headers={"Origin": "https://evil.example"}).headers
    assert c.get("/v1/rooms").status_code == 401

    alice, bob = {"x-test-sub": ALICE, **origin}, {"x-test-sub": BOB, **origin}
    r = c.post("/v1/rooms", json={"title": "#general"}, headers=alice)
    assert r.status_code == 201 and "Access-Control-Allow-Credentials" not in r.headers
    rid = r.json()["id"]
    e1 = ev("from the laptop", device_id="laptop")
    assert c.post(f"/v1/rooms/{rid}/events", json={"events": [e1]}, headers=alice).json()["accepted"][0]["seq"] == 1
    assert c.post(f"/v1/rooms/{rid}/events", json={"events": [e1]}, headers=alice).json()["accepted"][0]["duplicate"] is True   # retry
    assert c.post(f"/v1/rooms/{rid}/events", content=b"nope", headers=alice).status_code == 400
    assert c.get(f"/v1/rooms/{rid}/events", headers=bob).status_code == 404                                   # not his room
    inv = c.post(f"/v1/rooms/{rid}/invites", json={"days": 2, "max_uses": 3}, headers=alice).json()
    assert c.post("/v1/rooms/join", json={"code": inv["code"]}, headers=bob).json()["title"] == "#general"
    phone = c.get(f"/v1/rooms/{rid}/events?after=0", headers=bob).json()                                     # a new device pulls everything
    assert [(e["seq"], e["body"], e["device_id"]) for e in phone["events"]] == [(1, "from the laptop", "laptop")]
    assert c.put(f"/v1/rooms/{rid}/read", json={"seq": 1}, headers=bob).json()["last_read_seq"] == 1
    assert c.get("/v1/rooms", headers=bob).json()["rooms"][0]["unread"] == 0
    assert c.get(f"/v1/rooms/{rid}/events?after=x", headers=alice).status_code == 400
    assert c.patch(f"/v1/rooms/{rid}", json={"title": "#team"}, headers=bob).status_code == 403
    assert c.patch(f"/v1/rooms/{rid}", json={"title": "#team"}, headers=alice).json()["title"] == "#team"
    assert c.delete(f"/v1/rooms/{rid}", headers=bob).status_code == 403
    assert c.delete(f"/v1/rooms/{rid}", headers=alice).json()["events_removed"] == 1
    assert c.get("/v1/rooms", headers=bob).json()["rooms"] == []


def test_stream_sends_what_is_new_then_ends_with_a_cursor(pool, monkeypatch):
    monkeypatch.setattr(rr, "STREAM_SECONDS", 1)
    monkeypatch.setattr(rr, "POLL_SECONDS", 0.2)
    c = _client(pool)
    alice = {"x-test-sub": ALICE}
    rid = c.post("/v1/rooms", json={"title": "#general"}, headers=alice).json()["id"]
    c.post(f"/v1/rooms/{rid}/events", json={"events": [ev("one"), ev("two")]}, headers=alice)

    def events_of(text):
        out, name = [], None
        for line in text.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name:
                out.append((name, json.loads(line[6:])))
        return out

    with c.stream("GET", f"/v1/rooms/{rid}/stream?after=0", headers=alice) as s:
        got = events_of("".join(s.iter_text()))
    assert [d["body"] for n, d in got if n == "event"] == ["one", "two"]
    assert got[-1] == ("end", {"cursor": 2})
    with c.stream("GET", f"/v1/rooms/{rid}/stream?after=2", headers=alice) as s:
        again = events_of("".join(s.iter_text()))
    assert [n for n, _ in again] == ["end"]                                                                 # nothing replayed after the cursor
    assert c.get(f"/v1/rooms/{rid}/stream", headers={"x-test-sub": BOB}).status_code == 404               # not a member: no stream
