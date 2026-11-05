"""The hosted server serves the organization admin routes (mcp_server/org_admin_gate.py): off unless switched on, signs the person in with the hosted gate,
tells the routers who they are, and adds the CORS headers. No network, no database: a fake wrapped app, a fake sub app and a fake gate."""
from __future__ import annotations

import pytest

from app.mcp_server.org_admin_gate import OrgAdminGate
from app.services.authn import current_actor

ORIGIN = b"https://www.kell.org.in"


class Recorder:
    def __init__(self):
        self.sent: list[dict] = []

    async def __call__(self, message):
        self.sent.append(message)

    def status(self):
        return next(m["status"] for m in self.sent if m["type"] == "http.response.start")

    def headers(self):
        return {k.decode().lower(): v.decode() for k, v in next(m["headers"] for m in self.sent if m["type"] == "http.response.start")}


def _scope(path="/v1/orgs/mine", method="GET"):
    return {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"", "scheme": "https",
            "server": ("t", 443), "client": ("127.0.0.1", 1), "headers": [(b"origin", ORIGIN), (b"authorization", b"Bearer t")]}


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


class Wrapped:
    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"wrapped"})


class Sub:
    """Stands for the org-admin FastAPI app: records who the routers would see as the actor and what pool they were given."""

    def __init__(self):
        self.state = type("S", (), {})()
        self.seen = []

    async def __call__(self, scope, receive, send):
        actor = current_actor()
        self.issuers = getattr(self, "issuers", []) + [getattr(actor, "issuer", None)]
        self.seen.append((scope["path"], getattr(actor, "subject", None), getattr(self.state, "pool", None)))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"[]"})


class Gate:
    def __init__(self, subject="person-1", issuer="https://proj.supabase.co/auth/v1"):
        self.value = subject
        self._verifier = type("V", (), {"_oidc_config": type("C", (), {"issuer": issuer})()})()

    async def subject(self, request):
        return (self.value, None) if self.value else (None, ("sign in first", 401))


def _gate(subject="person-1", on=True, pool=object(), sub=None, wrapped=None):
    wrapped = wrapped or Wrapped()
    sub = sub or Sub()
    return OrgAdminGate(wrapped, Gate(subject), lambda: pool, sub_app=sub, is_on=lambda: on), wrapped, sub


@pytest.mark.asyncio
async def test_off_by_default_the_wrapped_app_answers_as_before():
    gate, wrapped, sub = _gate(on=False)
    out = Recorder()
    await gate(_scope(), _receive, out)
    assert wrapped.calls == ["/v1/orgs/mine"] and sub.seen == []


@pytest.mark.asyncio
async def test_other_paths_pass_through_even_when_on():
    gate, wrapped, sub = _gate()
    for path in ("/v1/rooms", "/healthz", "/v1/organizations-not-ours"):
        await gate(_scope(path), _receive, Recorder())
    assert wrapped.calls == ["/v1/rooms", "/healthz", "/v1/organizations-not-ours"] and sub.seen == []


@pytest.mark.asyncio
async def test_a_signed_in_person_reaches_the_routers_as_that_person_with_the_pool_and_cors():
    pool = object()
    gate, wrapped, sub = _gate(pool=pool)
    out = Recorder()
    await gate(_scope("/v1/orgs/mine"), _receive, out)
    assert sub.seen == [("/v1/orgs/mine", "person-1", pool)] and wrapped.calls == []
    assert sub.issuers == ["https://proj.supabase.co/auth/v1"]        # the issuer the person is stored under, not a blank one
    assert out.status() == 200 and "access-control-allow-origin" in out.headers()
    assert current_actor() is None                                   # the actor is not left set for the next request


@pytest.mark.asyncio
async def test_the_join_routes_are_served_too():
    gate, wrapped, sub = _gate()
    await gate(_scope("/v1/join", "POST"), _receive, Recorder())
    assert [p for p, _, _ in sub.seen] == ["/v1/join"]


@pytest.mark.asyncio
async def test_without_a_sign_in_the_gates_own_sentence_and_status_come_back_with_cors():
    gate, wrapped, sub = _gate(subject=None)
    out = Recorder()
    await gate(_scope(), _receive, out)
    assert out.status() == 401 and sub.seen == [] and "access-control-allow-origin" in out.headers()


@pytest.mark.asyncio
async def test_a_preflight_is_answered_without_a_sign_in():
    gate, wrapped, sub = _gate(subject=None)
    out = Recorder()
    await gate(_scope(method="OPTIONS"), _receive, out)
    assert out.status() == 204 and sub.seen == [] and "access-control-allow-origin" in out.headers()


@pytest.mark.asyncio
async def test_a_server_still_starting_answers_503():
    gate, wrapped, sub = _gate(pool=None)
    out = Recorder()
    await gate(_scope(), _receive, out)
    assert out.status() == 503 and sub.seen == []


def test_the_wrapped_apps_attributes_stay_reachable():
    class App:
        state = "kept"

    gate = OrgAdminGate(App(), Gate(), lambda: None, sub_app=Sub(), is_on=lambda: True)
    assert gate.state == "kept"
