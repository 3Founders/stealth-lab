"""
The Delegator relay against a real Postgres: service rules, ownership isolation, the HTTP routes and the stream.

Needs a Postgres it may create a throwaway database in (DATABASE_URL_LOCAL, from backend/.env or the environment); skips
otherwise, like the other *_e2e.py files. The database is created fresh, only migration 155 is applied (it depends on no other
table), and it is dropped afterwards. No fakes: the SQL under test is the SQL that ships.
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
from app.services import delegate as dg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get("DATABASE_URL_LOCAL") or dotenv_values(ROOT / ".env").get("DATABASE_URL_LOCAL")
pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="DATABASE_URL_LOCAL not set")

ALICE, BOB = "sub-alice", "sub-bob"


def _swap_db(dsn: str, name: str) -> str:
    head, _, tail = dsn.rpartition("/")
    return f"{head}/{name}" + (("?" + tail.split("?", 1)[1]) if "?" in tail else "")


class _Db:
    """The pool plus the loop it lives on and its DSN (asyncpg's Pool has no spare attributes to hang them on)."""

    def __init__(self, real, loop, dsn):
        self._real, self._loop, self._dsn = real, loop, dsn

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture()
def pool():
    name = f"kell_delegate_{uuid.uuid4().hex[:10]}"
    loop = asyncio.new_event_loop()

    async def up():
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'CREATE DATABASE "{name}"')
        await admin.close()
        p = await asyncpg.create_pool(_swap_db(ADMIN_DSN, name), min_size=1, max_size=4)
        await p.execute((ROOT / "db" / "155_delegate_relay.sql").read_text(encoding="utf-8"))
        return p

    async def down(p):
        await p.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

    real = loop.run_until_complete(up())
    p = _Db(real, loop, _swap_db(ADMIN_DSN, name))
    yield p
    loop.run_until_complete(down(real))
    loop.close()


def run(pool, coro):
    return pool._loop.run_until_complete(coro)


def test_agents_names_are_validated_unique_and_per_owner(pool):
    a = run(pool, dg.create_agent(pool, ALICE, "Web-Chat", "chat"))
    assert a["name"] == "web-chat" and a["handle"] == "@web-chat" and a["online"] is False
    with pytest.raises(dg.DelegateError) as dup:
        run(pool, dg.create_agent(pool, ALICE, "web-chat"))
    assert dup.value.status == 409
    with pytest.raises(dg.DelegateError):
        run(pool, dg.create_agent(pool, ALICE, "bad name!"))
    assert run(pool, dg.create_agent(pool, BOB, "web-chat"))["id"] != a["id"]       # same name, different person
    assert [x["name"] for x in run(pool, dg.list_agents(pool, ALICE))] == ["web-chat"]


def test_message_creates_one_task_per_known_agent_and_reports_the_unknown(pool):
    run(pool, dg.create_agent(pool, ALICE, "web-chat"))
    run(pool, dg.create_agent(pool, ALICE, "opencode"))
    out = run(pool, dg.post_message(pool, ALICE, "@alice/web-chat and @opencode and @ghost: review src/auth.ts", "general"))
    assert out["room"] == "#general"
    assert sorted(t["status"] for t in out["tasks"]) == ["queued", "queued"]
    assert out["unknown"] == ["ghost"]
    none = run(pool, dg.post_message(pool, ALICE, "just thinking out loud"))
    assert none["tasks"] == [] and "no @agent" in none["note"]


def test_agent_pulls_in_order_posts_one_result_and_is_seen_online(pool):
    run(pool, dg.create_agent(pool, ALICE, "web-chat"))
    run(pool, dg.post_message(pool, ALICE, "@web-chat first"))
    run(pool, dg.post_message(pool, ALICE, "@web-chat second"))
    one = run(pool, dg.claim_next(pool, ALICE, "web-chat"))
    assert one["task"]["text"] == "@web-chat first" and one["task"]["status"] == "claimed" and one["waiting"] == 1
    assert "not an instruction from kell" in one["note"]
    done = run(pool, dg.submit_result(pool, ALICE, one["task"]["id"], "looked fine", True, "pytest: 3 passed"))
    assert done["status"] == "done" and done["result"] == "looked fine" and done["check"] == "pytest: 3 passed"
    with pytest.raises(dg.DelegateError):                       # a task is answered once
        run(pool, dg.submit_result(pool, ALICE, one["task"]["id"], "again"))
    two = run(pool, dg.claim_next(pool, ALICE, "web-chat"))
    assert two["task"]["text"] == "@web-chat second" and two["waiting"] == 0
    assert run(pool, dg.claim_next(pool, ALICE, "web-chat"))["task"] is None
    assert run(pool, dg.list_agents(pool, ALICE))[0]["online"] is True
    failed = run(pool, dg.submit_result(pool, ALICE, two["task"]["id"], "could not", ok=False))
    assert failed["status"] == "failed"


def test_nobody_can_touch_someone_elses_agents_or_tasks(pool):
    a = run(pool, dg.create_agent(pool, ALICE, "web-chat"))
    task = run(pool, dg.post_message(pool, ALICE, "@web-chat secret plan"))["tasks"][0]
    with pytest.raises(dg.DelegateError) as e1:
        run(pool, dg.claim_next(pool, BOB, "web-chat"))                          # Bob has no such agent
    assert e1.value.status == 404
    run(pool, dg.create_agent(pool, BOB, "web-chat"))
    assert run(pool, dg.claim_next(pool, BOB, "web-chat"))["task"] is None        # his own empty queue
    with pytest.raises(dg.DelegateError):
        run(pool, dg.submit_result(pool, BOB, task["id"], "x"))
    with pytest.raises(dg.DelegateError):
        run(pool, dg.decide_task(pool, BOB, task["id"], False))
    with pytest.raises(dg.DelegateError):
        run(pool, dg.revoke_agent(pool, BOB, a["id"]))
    assert run(pool, dg.list_tasks(pool, BOB)) == []
    assert len(run(pool, dg.list_tasks(pool, ALICE))) == 1


def test_approval_gate_cancel_and_revoke(pool):
    ag = run(pool, dg.create_agent(pool, ALICE, "web-chat"))
    t = run(pool, dg.post_message(pool, ALICE, "@web-chat please", needs_approval=True))["tasks"][0]
    assert t["status"] == "needs_approval"
    held = run(pool, dg.claim_next(pool, ALICE, "web-chat"))
    assert held["task"] is None and held["awaiting_owner_approval"] == 1
    assert run(pool, dg.decide_task(pool, ALICE, t["id"], True))["status"] == "queued"
    assert run(pool, dg.claim_next(pool, ALICE, "web-chat"))["task"]["id"] == t["id"]
    t2 = run(pool, dg.post_message(pool, ALICE, "@web-chat later"))["tasks"][0]
    assert run(pool, dg.decide_task(pool, ALICE, t2["id"], False))["status"] == "cancelled"
    t3 = run(pool, dg.post_message(pool, ALICE, "@web-chat and more"))["tasks"][0]
    gone = run(pool, dg.revoke_agent(pool, ALICE, ag["id"]))
    assert gone["cancelled"] >= 1
    statuses = {x["id"]: x["status"] for x in run(pool, dg.list_tasks(pool, ALICE))}
    assert statuses[t3["id"]] == "cancelled"
    with pytest.raises(dg.DelegateError):
        run(pool, dg.claim_next(pool, ALICE, "web-chat"))                        # a revoked agent can no longer pull
    assert run(pool, dg.create_agent(pool, ALICE, "web-chat"))["name"] == "web-chat"  # the name is free again


def test_secrets_in_task_text_and_results_are_redacted_before_storage(pool):
    run(pool, dg.create_agent(pool, ALICE, "web-chat"))
    key = "sk-ant-api03-" + "a" * 40
    task = run(pool, dg.post_message(pool, ALICE, f"@web-chat use {key} to call the API"))["tasks"][0]
    assert key not in task["text"] and "REDACTED" in task["text"]
    claimed = run(pool, dg.claim_next(pool, ALICE, "web-chat"))["task"]
    done = run(pool, dg.submit_result(pool, ALICE, claimed["id"], f"I found {key} in the file", check=f"ran with {key}"))
    assert key not in done["result"] and key not in done["check"]
    raw = run(pool, pool.fetchval("SELECT body || coalesce(result_text,'') || coalesce(result_check,'') FROM delegate_tasks"))
    assert key not in raw


def test_limits_are_enforced(pool):
    for i in range(dg.MAX_AGENTS_PER_OWNER):
        run(pool, dg.create_agent(pool, ALICE, f"agent-{i}"))
    with pytest.raises(dg.DelegateError) as e:
        run(pool, dg.create_agent(pool, ALICE, "one-too-many"))
    assert e.value.status == 409
    with pytest.raises(dg.DelegateError):
        run(pool, dg.post_message(pool, ALICE, "@agent-0 " + "x" * 20, room="bad room!"))


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
    dr.register(srv, _HeaderGate(object(), lambda: per_call, lambda r: False), lambda: per_call)
    return TestClient(Starlette(routes=srv.routes))


def test_routes_end_to_end_with_cors_and_ownership(pool):
    c = _client(pool)
    origin = {"Origin": "https://www.kell.org.in"}
    pre = c.options("/v1/delegate/agents", headers={**origin, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == origin["Origin"]
    assert "access-control-allow-origin" not in c.options("/v1/delegate/agents", headers={"Origin": "https://evil.example"}).headers
    assert c.get("/v1/delegate/agents").status_code == 401                          # no identity, no access

    alice = {"x-test-sub": ALICE, **origin}
    r = c.post("/v1/delegate/agents", json={"name": "web-chat", "kind": "chat"}, headers=alice)
    assert r.status_code == 201 and r.json()["handle"] == "@web-chat"
    assert r.headers["access-control-allow-origin"] == origin["Origin"] and "Access-Control-Allow-Credentials" not in r.headers
    assert c.post("/v1/delegate/agents", json={"name": "web-chat"}, headers=alice).status_code == 409
    assert c.post("/v1/delegate/agents", content=b"not json", headers=alice).status_code == 400

    m = c.post("/v1/delegate/messages", json={"text": "@web-chat summarize auth.ts", "room": "#team"}, headers=alice)
    assert m.status_code == 201 and m.json()["tasks"][0]["status"] == "queued"
    tid = m.json()["tasks"][0]["id"]
    assert [t["id"] for t in c.get("/v1/delegate/tasks?room=%23team", headers=alice).json()["tasks"]] == [tid]
    assert c.get("/v1/delegate/tasks", headers={"x-test-sub": BOB}).json()["tasks"] == []
    assert c.post(f"/v1/delegate/tasks/{tid}/cancel", headers={"x-test-sub": BOB}).status_code == 404
    assert c.post(f"/v1/delegate/tasks/{tid}/cancel", headers=alice).json()["status"] == "cancelled"
    assert c.get("/v1/delegate/tasks?after=garbage", headers=alice).status_code == 400
    agent_id = c.get("/v1/delegate/agents", headers=alice).json()["agents"][0]["id"]
    assert c.delete(f"/v1/delegate/agents/{agent_id}", headers={"x-test-sub": BOB}).status_code == 404
    assert c.delete(f"/v1/delegate/agents/{agent_id}", headers=alice).json()["revoked"] == agent_id


def test_event_stream_sends_the_room_then_ends_and_resumes_from_a_cursor(pool, monkeypatch):
    monkeypatch.setattr(dr, "STREAM_SECONDS", 1)
    monkeypatch.setattr(dr, "POLL_SECONDS", 0.2)
    c = _client(pool)
    alice = {"x-test-sub": ALICE}
    c.post("/v1/delegate/agents", json={"name": "web-chat"}, headers=alice)
    c.post("/v1/delegate/messages", json={"text": "@web-chat one", "room": "#r"}, headers=alice)
    with c.stream("GET", "/v1/delegate/events?room=%23r", headers=alice) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    events = [blk for blk in body.split("\n\n") if blk.startswith("event:")]
    kinds = [e.split("\n", 1)[0] for e in events]
    assert "event: task" in kinds and "event: agents" in kinds and kinds[-1] == "event: end"
    task = json.loads(next(e for e in events if e.startswith("event: task")).split("data: ", 1)[1])
    assert task["text"] == "@web-chat one" and task["room"] == "#r"
    cursor = json.loads(events[-1].split("data: ", 1)[1])["cursor"]
    with c.stream("GET", f"/v1/delegate/events?room=%23r&after={cursor}", headers=alice) as again:
        body2 = "".join(again.iter_text())
    assert "event: task" not in body2                                             # nothing changed: no replay, no duplicate
    assert c.get("/v1/delegate/events?room=bad%20room", headers=alice).status_code == 400
