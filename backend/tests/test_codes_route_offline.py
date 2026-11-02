"""The two public billing routes without a database or a network: codes/check (no session, per-address limited) and plans
(no session: the pricing page needs the numbers before anyone signs in). Real Starlette Requests against the handlers
billing_routes registers on a fake server, so the 503, the sentences and the limiter are pinned."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from starlette.requests import Request

from app.mcp_server import billing_routes as br


class FakeConn:
    def __init__(self, answers):
        self.answers = answers

    def _find(self, sql):
        for needle, value in self.answers.items():
            if needle in sql:
                return value() if callable(value) else value
        return None

    async def execute(self, sql, *a):
        return self._find(sql)

    async def fetch(self, sql, *a):
        return self._find(sql) or []

    async def fetchrow(self, sql, *a):
        return self._find(sql)

    async def fetchval(self, sql, *a):
        return self._find(sql)

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


class FakePool:
    def __init__(self, answers=None):
        self.conn = FakeConn(answers or {})

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class FakeServer:
    """Collects the custom_route handlers; register() needs nothing else from the MCP server."""

    def __init__(self):
        self.handlers = {}

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.handlers[path] = fn
            return fn
        return deco


def registered(pool):
    s = FakeServer()
    br.register(s, verifier=None, pool_getter=lambda: pool, is_local=lambda r: False)
    return s


def req(method="POST", body=None, client="198.51.100.7"):
    raw = json.dumps(body).encode() if body is not None else b""
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    scope = {"type": "http", "method": method, "path": "/v1/billing/codes/check", "headers": [],
             "client": (client, 50000), "query_string": b"", "server": ("test", 80), "scheme": "http"}
    return Request(scope, receive)


def message(response):
    return json.loads(response.body)["error"]["message"]


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("KELL_WALLETS", "on")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")
    monkeypatch.delenv("KELL_PERSONAL_ORGS", raising=False)


@pytest.mark.asyncio
async def test_a_known_code_is_valid_and_an_unknown_one_gets_the_sentence(billing_on):
    s = registered(FakePool({"FROM billing_referral_codes WHERE active": [{"code": "FRIEND"}]}))
    handler = s.handlers["/v1/billing/codes/check"]
    ok = await handler(req(body={"code": " friend "}))
    assert ok.status_code == 200 and json.loads(ok.body) == {"valid": True}
    bad = await handler(req(body={"code": "stranger"}))
    assert bad.status_code == 400 and "recognise" in message(bad)
    empty = await handler(req(body={"code": ""}))
    assert empty.status_code == 400 and "enter the code you were given" in message(empty)


@pytest.mark.asyncio
async def test_codes_not_open_yet_and_a_bad_body_are_refused(billing_on):
    s = registered(FakePool({}))
    handler = s.handlers["/v1/billing/codes/check"]
    closed = await handler(req(body={"code": "FRIEND"}))
    assert closed.status_code == 400 and "codes are not open yet" in message(closed)
    no_body = await handler(req())
    assert no_body.status_code == 400 and "must be JSON" in message(no_body)


@pytest.mark.asyncio
async def test_the_check_is_limited_per_address(billing_on, monkeypatch):
    monkeypatch.setattr(br, "CODE_CHECKS_PER_ADDRESS", br.Window(2, 60.0))
    s = registered(FakePool({"FROM billing_referral_codes WHERE active": [{"code": "FRIEND"}]}))
    handler = s.handlers["/v1/billing/codes/check"]
    assert (await handler(req(body={"code": "FRIEND"}))).status_code == 200
    assert (await handler(req(body={"code": "FRIEND"}))).status_code == 200
    assert (await handler(req(body={"code": "FRIEND"}))).status_code == 429
    other = await handler(req(body={"code": "FRIEND"}, client="203.0.113.9"))
    assert other.status_code == 200                                             # another address has its own window


@pytest.mark.asyncio
async def test_billing_off_is_503(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    s = registered(FakePool({}))
    assert (await s.handlers["/v1/billing/codes/check"](req(body={"code": "FRIEND"}))).status_code == 503
    assert (await s.handlers["/v1/billing/plans"](req(method="GET"))).status_code == 503


@pytest.mark.asyncio
async def test_the_plan_list_carries_the_active_rows_and_needs_no_session(billing_on):
    rows = [{"plan": "pro", "price_paise": 139_900, "period_days": 45, "included_usd": Decimal("6.5"), "active": True, "max_redemptions": None},
            {"plan": "plus", "price_paise": 200_000, "period_days": 30, "included_usd": Decimal("12"), "active": True, "max_redemptions": None},
            {"plan": "pilot", "price_paise": 64_900, "period_days": 30, "included_usd": Decimal("4"), "active": True, "max_redemptions": 100}]
    s = registered(FakePool({"FROM billing_plan_rows": rows, "AS n": {"n": 5}}))
    r = await s.handlers["/v1/billing/plans"](req(method="GET"))
    assert r.status_code == 200
    assert json.loads(r.body)["plans"] == [
        {"plan": "pilot", "inr": 649, "days": 30, "included_usd": "4", "max_redemptions": 100, "sold": 5},
        {"plan": "plus", "inr": 2000, "days": 30, "included_usd": "12", "max_redemptions": None, "sold": None},
        {"plan": "pro", "inr": 1399, "days": 45, "included_usd": "6.5", "max_redemptions": None, "sold": None},
    ]
