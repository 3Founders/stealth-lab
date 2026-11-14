"""
Desktop sessions in the rooms against a real Postgres (app/services/desktop_remote.py, db/208_desktop_remote.sql): a desktop
reports its sessions, grants decide what a teammate's ask may do, an offline desktop is refused, approvals and permission
replies, the desktop claims and finishes an ask and its answer is posted in the room, @kell's routing, and the HTTP routes.

Needs DATABASE_URL_LOCAL (a Postgres it may create a throwaway database in), like test_rooms_e2e.py; skips otherwise.
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
from starlette.testclient import TestClient  # noqa: E402

from app.services import desktop_remote as dr  # noqa: E402
from app.services import rooms as rm  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get("DATABASE_URL_LOCAL") or dotenv_values(ROOT / ".env").get("DATABASE_URL_LOCAL")
pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="DATABASE_URL_LOCAL not set")

ANUJ, CHAIT, EVE = "sub-anuj", "sub-chait", "sub-eve"
DEV = "anuj-laptop-01"


def _swap_db(dsn: str, name: str) -> str:
    head, _, tail = dsn.rpartition("/")
    return f"{head}/{name}" + (("?" + tail.split("?", 1)[1]) if "?" in tail else "")


class _Db:
    def __init__(self, real, loop):
        self._real, self._loop = real, loop

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture()
def pool():
    name = f"kell_desk_{uuid.uuid4().hex[:10]}"
    loop = asyncio.new_event_loop()

    async def up():
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'CREATE DATABASE "{name}"')
        await admin.close()
        p = await asyncpg.create_pool(_swap_db(ADMIN_DSN, name), min_size=1, max_size=8)
        for f in ("155_delegate_relay.sql", "157_rooms.sql", "159_room_admin_role.sql", "208_desktop_remote.sql", "210_desktop_history.sql"):
            await p.execute((ROOT / "db" / f).read_text(encoding="utf-8"))
        # just enough of the accounts tables for usernames (rooms.sub_of_username falls back to display_name)
        await p.execute("CREATE TABLE users (external_subject text PRIMARY KEY, display_name text)")
        await p.execute("INSERT INTO users VALUES ($1, 'anuj'), ($2, 'chaitanya'), ($3, 'eve')", ANUJ, CHAIT, EVE)
        return p

    async def down(p):
        await p.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

    rm.LIMITER = rm._Limiter()
    real = loop.run_until_complete(up())
    yield _Db(real, loop)
    loop.run_until_complete(down(real))
    loop.close()


def run(pool, coro):
    return pool._loop.run_until_complete(coro)


SESSIONS = [{"id": "ses_api", "title": "Fix the billing webhook", "project": "C:\\work\\payments", "summary": "added retries",
             "updated": 1_700_000_000_000},
            {"id": "ses_ui", "title": "Landing page copy", "project": "/home/a/site", "updated": 1_600_000_000_000}]


def shared_room(pool):
    r = run(pool, rm.create_room(pool, ANUJ, "#team"))
    inv = run(pool, rm.create_invite(pool, ANUJ, r["id"]))
    run(pool, rm.redeem_invite(pool, CHAIT, inv["code"]))
    return r


def test_heartbeat_lists_sessions_by_folder_name_and_a_device_belongs_to_one_account(pool):
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "Anuj's laptop", SESSIONS))
    mine = run(pool, dr.sessions_of(pool, [ANUJ]))
    assert [s["session_id"] for s in mine] == ["ses_api", "ses_ui"]
    assert mine[0]["project"] == "payments" and mine[1]["project"] == "site"        # never the full path
    assert all(s["online"] for s in mine)
    with pytest.raises(dr.DesktopError):
        run(pool, dr.heartbeat(pool, EVE, DEV, "not hers", []))
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "Anuj's laptop", SESSIONS[:1]))           # the list is replaced
    assert [s["session_id"] for s in run(pool, dr.sessions_of(pool, [ANUJ]))] == ["ses_api"]


def test_only_a_granted_teammate_may_ask_and_the_grant_decides_the_level(pool):
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "laptop", SESSIONS))
    with pytest.raises(dr.DesktopError) as no:
        run(pool, dr.create_ask(pool, CHAIT, DEV, "ses_api", "what changed?"))
    assert no.value.status == 403
    assert run(pool, dr.create_ask(pool, ANUJ, DEV, "ses_api", "status?"))["level"] == "owner"
    run(pool, dr.set_grant(pool, ANUJ, "chaitanya", "answer"))
    ask = run(pool, dr.create_ask(pool, CHAIT, DEV, "ses_api", "what changed?"))
    assert (ask["level"], ask["status"]) == ("answer", "queued")
    run(pool, dr.set_grant(pool, ANUJ, "@chaitanya", "approve"))
    ask = run(pool, dr.create_ask(pool, CHAIT, DEV, "ses_api", "run the tests"))
    assert (ask["level"], ask["status"]) == ("approve", "needs_approval")
    assert [p["id"] for p in run(pool, dr.pending_for_owner(pool, ANUJ))] == [ask["id"]]
    with pytest.raises(dr.DesktopError):
        run(pool, dr.approve(pool, CHAIT, ask["id"], True))                           # only the owner approves
    assert run(pool, dr.approve(pool, ANUJ, ask["id"], True))["status"] == "queued"
    g = run(pool, dr.grants(pool, ANUJ))
    assert [(x["username"], x["level"]) for x in g["given"]] == [("chaitanya", "approve")]
    assert run(pool, dr.grants(pool, CHAIT))["received"][0]["sub"] == ANUJ
    run(pool, dr.remove_grant(pool, ANUJ, CHAIT))                                       # queued asks are cancelled
    assert {a["status"] for a in run(pool, dr.recent_asks(pool, CHAIT))} == {"cancelled"}
    with pytest.raises(dr.DesktopError):
        run(pool, dr.create_ask(pool, CHAIT, DEV, "ses_api", "again?"))


def test_an_offline_desktop_is_refused(pool):
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "laptop", SESSIONS))
    run(pool, pool.execute("UPDATE desktop_devices SET last_seen = now() - interval '10 minutes'"))
    with pytest.raises(dr.DesktopOffline):
        run(pool, dr.create_ask(pool, ANUJ, DEV, "ses_api", "status?"))
    assert run(pool, dr.sessions_of(pool, [ANUJ]))[0]["online"] is False


def test_the_desktop_claims_runs_and_the_answer_is_posted_in_the_room(pool):
    r = shared_room(pool)
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "laptop", SESSIONS))
    run(pool, dr.set_grant(pool, ANUJ, "chaitanya", "full"))
    ask = run(pool, dr.create_ask(pool, CHAIT, DEV, "ses_api", "did the retries land?", r["id"]))
    assert run(pool, dr.next_ask(pool, EVE, DEV)) is None                               # someone else's device
    got = run(pool, dr.next_ask(pool, ANUJ, DEV))
    assert got["id"] == ask["id"] and got["level"] == "full" and got["asker_name"] == "chaitanya"
    assert run(pool, dr.next_ask(pool, ANUJ, DEV)) is None                              # claimed once
    run(pool, dr.update_ask(pool, ANUJ, ask["id"], {"answer": "Yes, partly", "run_session": "ses_fork"}))
    waiting = run(pool, dr.update_ask(pool, ANUJ, ask["id"], {"status": "waiting_permission",
                                                              "waiting": {"request_id": "per_1", "tool": "bash", "detail": "npm test"}}))
    assert waiting["waiting"]["tool"] == "bash"
    assert run(pool, dr.decide_permission(pool, ANUJ, ask["id"], "once"))["decision"] == "once"
    run(pool, dr.update_ask(pool, ANUJ, ask["id"], {"status": "running"}))
    done = run(pool, dr.update_ask(pool, ANUJ, ask["id"], {"status": "done", "answer": "Yes: retries are in and the tests pass."}))
    assert done["status"] == "done" and done["run_session"] == "ses_fork"
    events = run(pool, rm.list_events(pool, CHAIT, r["id"]))["events"]
    last = events[-1]
    assert last["sender"]["handle"] == "@anuj/desktop" and "retries are in" in last["body"]
    assert "Fix the billing webhook" in last["body"]
    with pytest.raises(dr.DesktopError):
        run(pool, dr.update_ask(pool, ANUJ, ask["id"], {"status": "done", "answer": "twice"}))   # finished asks stay finished


def test_route_weighs_only_reachable_sessions_and_needs_a_confident_verdict(pool):
    r = shared_room(pool)
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "laptop", SESSIONS))
    prompts = []

    def cheap(answer):
        async def call(prompt):
            prompts.append(prompt)
            return answer
        return call

    # Chaitanya has no grant: nothing of Anuj's is reachable, and no model is asked at all
    assert run(pool, dr.route(pool, CHAIT, r["id"], "did the webhook fix land?", [], cheap('{"session": 1}'))) is None
    assert prompts == []
    run(pool, dr.set_grant(pool, ANUJ, "chaitanya", "answer"))
    hit = run(pool, dr.route(pool, CHAIT, r["id"], "did the webhook fix land?", [],
                             cheap('Sure: {"session": 1, "confidence": 0.9, "why": "billing webhook"}')))
    assert hit["session_id"] == "ses_api" and hit["owner_name"] == "anuj"
    assert "anuj's desktop" in prompts[-1] and "Fix the billing webhook" in prompts[-1]
    assert run(pool, dr.route(pool, CHAIT, r["id"], "what is 2+2", [], cheap('{"session": null, "confidence": 0.9}'))) is None
    assert run(pool, dr.route(pool, CHAIT, r["id"], "hmm", [], cheap('{"session": 1, "confidence": 0.3}'))) is None
    assert run(pool, dr.route(pool, CHAIT, r["id"], "x", [], cheap("not json"))) is None
    # a teammate's desktop is reachable only in a room they are in
    other = run(pool, rm.create_room(pool, CHAIT, "#solo"))
    assert run(pool, dr.route(pool, CHAIT, other["id"], "did the webhook fix land?", [], cheap('{"session": 1, "confidence": 0.9}'))) is None


def test_http_routes(pool):
    from app.mcp_server import desktop_routes, room_routes

    class Gate:
        async def subject(self, request):
            who = request.headers.get("x-test-sub")
            return (who, None) if who else (None, ("sign in", 401))

    class Server:
        def __init__(self):
            self.routes = []

        def custom_route(self, path, methods, include_in_schema=False):
            def deco(fn):
                self.routes.append((path, fn, methods))
                return fn
            return deco

    s = Server()
    desktop_routes.register(s, Gate(), lambda: pool)
    desktop_routes.NEXT_WAIT_SECONDS = 0.0
    from starlette.routing import Route

    app = Starlette(routes=[Route(p, f, methods=m) for p, f, m in s.routes])

    # the routes' coroutines must run on the pool's loop: drive them through a client bound to it
    import httpx

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a = {"x-test-sub": ANUJ}
            assert (await c.post("/v1/desktop/heartbeat", json={"device_id": DEV, "name": "laptop", "sessions": SESSIONS}, headers=a)).status_code == 200
            assert (await c.get("/v1/desktop/sessions")).status_code == 401
            got = (await c.get("/v1/desktop/sessions", headers=a)).json()
            assert [x["session_id"] for x in got["mine"]] == ["ses_api", "ses_ui"] and got["devices"][0]["online"]
            ask = (await c.post("/v1/desktop/asks", json={"device_id": DEV, "session_id": "ses_ui", "text": "shorter headline"}, headers=a)).json()
            nxt = (await c.get(f"/v1/desktop/next?device_id={DEV}", headers=a)).json()
            assert nxt["ask"]["id"] == ask["id"]
            done = await c.post(f"/v1/desktop/asks/{ask['id']}/update", json={"status": "done", "answer": "Done."}, headers=a)
            assert done.json()["status"] == "done"
            assert (await c.get(f"/v1/desktop/asks/{ask['id']}", headers={"x-test-sub": EVE})).status_code == 404
            g = await c.post("/v1/desktop/grants", json={"username": "eve", "level": "bogus"}, headers=a)
            assert g.status_code == 400
            assert (await c.post("/v1/desktop/grants", json={"username": "eve", "level": "answer"}, headers=a)).status_code == 200
            assert (await c.get("/v1/desktop/grants", headers=a)).json()["given"][0]["level"] == "answer"
            assert (await c.delete(f"/v1/desktop/grants?grantee={EVE}", headers=a)).json()["removed"] is True

    run(pool, go())
    assert room_routes.register  # the desktop routes are registered from room_routes.register


def test_history_is_for_the_owner_only_and_only_handed_to_a_desktop_that_can_answer_it(pool):
    r = shared_room(pool)
    run(pool, dr.heartbeat(pool, ANUJ, DEV, "laptop", SESSIONS))
    run(pool, dr.set_grant(pool, ANUJ, "chaitanya", "full"))
    with pytest.raises(dr.DesktopError) as no:
        run(pool, dr.request_history(pool, CHAIT, DEV, "ses_api"))           # a grant lets them ask, not read the chat
    assert no.value.status == 404
    h = run(pool, dr.request_history(pool, ANUJ, DEV, "ses_api"))
    assert h["kind"] == "history" and h["status"] == "queued"
    assert run(pool, dr.next_ask(pool, ANUJ, DEV)) is None                  # an older desktop never gets it as a prompt
    got = run(pool, dr.next_ask(pool, ANUJ, DEV, ("ask", "history")))
    assert got["id"] == h["id"] and got["kind"] == "history"
    done = run(pool, dr.update_ask(pool, ANUJ, h["id"], {"status": "done", "answer": '[{"who": "user", "text": "hi"}]'}))
    assert done["status"] == "done"
    assert run(pool, rm.list_events(pool, ANUJ, r["id"]))["events"] == []    # nothing posted anywhere
    assert run(pool, dr.recent_asks(pool, ANUJ)) == []                       # and not listed as an ask
