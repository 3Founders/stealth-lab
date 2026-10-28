"""The website Contact form: fails closed, drops bots quietly, stores first and notifies second, and keeps visitor text out of
email headers. Offline: a hand-rolled fake pool and fake HTTP, no database or network."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services import contact


class FakeConn:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    async def execute(self, sql, *args):
        if self.fail:
            raise RuntimeError("db down")
        self.log.append((sql, args))


class FakePool:
    def __init__(self, fail=False):
        self.log, self.fail = [], fail

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self_inner):
                return FakeConn(pool.log, pool.fail)

            async def __aexit__(self_inner, *exc):
                return False

        return Ctx()


GOOD = dict(name="Ada Lovelace", email="ada@example.com", company="Analytical", topic="pilot",
            message="We would like a pilot.", source_path="/contact", honeypot="", turnstile_token="tok", remote_ip="1.2.3.4")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    contact.reset_limits()
    monkeypatch.setattr(settings, "contact_turnstile_secret", "secret")
    monkeypatch.setattr(settings, "contact_allow_unverified", False)
    monkeypatch.setattr(settings, "frontend_origin", "https://kell.example")
    yield
    contact.reset_limits()


async def _yes(token, ip):
    return True


async def _no(token, ip):
    return False


def run(coro):
    return asyncio.run(coro)


def test_stores_the_row_then_notifies_and_marks_it_notified():
    pool, sent = FakePool(), []

    async def notify(**kw):
        sent.append(kw)
        return True

    assert run(contact.submit(pool, **GOOD, notify=notify, verify=_yes)) == {"ok": True}
    assert "INSERT INTO contact_requests" in pool.log[0][0]
    assert "UPDATE contact_requests SET notified_at" in pool.log[1][0]
    assert sent and sent[0]["email"] == "ada@example.com"


def test_a_failed_notification_leaves_the_row_stored_and_unmarked():
    pool = FakePool()

    async def boom(**kw):
        raise RuntimeError("email down")

    assert run(contact.submit(pool, **GOOD, notify=boom, verify=_yes)) == {"ok": True}
    assert len(pool.log) == 1 and "INSERT" in pool.log[0][0]  # stored, and no notified_at update


def test_a_filled_honeypot_is_dropped_silently_with_nothing_stored_or_sent():
    pool = FakePool()
    out = run(contact.submit(pool, **{**GOOD, "honeypot": "http://spam"}, verify=_yes))
    assert out == {"ok": True} and pool.log == []


def test_turnstile_failure_refuses_and_stores_nothing():
    pool = FakePool()
    with pytest.raises(contact.ContactRefused) as e:
        run(contact.submit(pool, **GOOD, verify=_no))
    assert e.value.status == 403 and pool.log == []


def test_without_a_secret_the_form_refuses_unless_dev_mode_is_explicit(monkeypatch):
    monkeypatch.setattr(settings, "contact_turnstile_secret", None)
    with pytest.raises(contact.ContactRefused) as e:
        run(contact.submit(FakePool(), **GOOD))
    assert e.value.status == 503
    monkeypatch.setattr(settings, "contact_allow_unverified", True)
    assert run(contact.submit(FakePool(), **GOOD, notify=lambda **kw: asyncio.sleep(0, False))) == {"ok": True}


def test_storage_failure_is_a_refusal_not_a_silent_success():
    with pytest.raises(contact.ContactRefused) as e:
        run(contact.submit(FakePool(fail=True), **GOOD, verify=_yes))
    assert e.value.status == 503


@pytest.mark.parametrize("field,value", [("name", "   "), ("email", "not-an-email"), ("email", "a@b.c\r\nBcc: x@y.z"), ("message", ""),
                                         ("message", "x" * 4001)])
def test_bad_input_is_refused_with_422(field, value):
    with pytest.raises(contact.ContactRefused) as e:
        run(contact.submit(FakePool(), **{**GOOD, field: value}, verify=_yes))
    assert e.value.status == 422


def test_rate_limits_by_address_and_by_email():
    pool = FakePool()
    for i in range(3):
        run(contact.submit(pool, **{**GOOD, "email": "same@example.com", "remote_ip": f"9.9.9.{i}"}, verify=_yes,
                           notify=lambda **kw: asyncio.sleep(0, False)))
    with pytest.raises(contact.ContactRefused) as e:  # a 4th from the same email, new address
        run(contact.submit(pool, **{**GOOD, "email": "same@example.com", "remote_ip": "9.9.9.9"}, verify=_yes))
    assert e.value.status == 429
    contact.reset_limits()
    for i in range(5):
        run(contact.submit(pool, **{**GOOD, "email": f"p{i}@example.com", "remote_ip": "7.7.7.7"}, verify=_yes,
                           notify=lambda **kw: asyncio.sleep(0, False)))
    with pytest.raises(contact.ContactRefused) as e:  # a 6th from the same address
        run(contact.submit(pool, **{**GOOD, "email": "p9@example.com", "remote_ip": "7.7.7.7"}, verify=_yes))
    assert e.value.status == 429


def test_clean_line_removes_every_control_character():
    assert contact.clean_line("Ada\r\nBcc: x@y.z\tLovelace\x00", 120) == "Ada Bcc: x@y.z Lovelace"
    assert len(contact.clean_line("a" * 500, 120)) == 120


# --- Turnstile verification --------------------------------------------------

def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_turnstile_passes_only_on_success_from_our_own_hostname():
    async def go(body, status=200):
        async with _client(lambda req: httpx.Response(status, json=body)) as c:
            return await contact.verify_turnstile("tok", "1.2.3.4", client=c)

    assert run(go({"success": True, "hostname": "kell.example"})) is True
    assert run(go({"success": True, "hostname": "evil.example"})) is False  # a token minted on someone else's site
    assert run(go({"success": False, "error-codes": ["timeout-or-duplicate"]})) is False
    assert run(go({"success": True, "hostname": "kell.example"}, status=500)) is False


def test_turnstile_error_or_missing_token_fails_closed():
    async def boom(req):
        raise httpx.ConnectTimeout("slow")

    async def go():
        async with _client(boom) as c:
            return await contact.verify_turnstile("tok", None, client=c)

    assert run(go()) is False
    assert run(contact.verify_turnstile("", None)) is False


def test_turnstile_sends_the_secret_in_the_body_never_in_the_url():
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), req.content.decode()
        return httpx.Response(200, json={"success": True, "hostname": "kell.example"})

    async def go():
        async with _client(handler) as c:
            return await contact.verify_turnstile("tok", "1.2.3.4", client=c)

    assert run(go()) is True
    assert "secret" not in seen["url"] and "secret=secret" in seen["body"] and "remoteip=1.2.3.4" in seen["body"]


# --- the notification email ---------------------------------------------------

def test_notification_keeps_visitor_text_out_of_every_header(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_key")
    monkeypatch.setattr(settings, "contact_notify_to", "team@kell.example")
    monkeypatch.setattr(settings, "contact_notify_from", "forms@kell.example")
    seen = {}

    def handler(req):
        seen["json"], seen["auth"] = json.loads(req.content), req.headers["authorization"]
        return httpx.Response(200, json={"id": "1"})

    async def go():
        async with _client(handler) as c:
            return await contact.send_notification(lead_id="L1", name="Ada", email="ada@example.com", company=None,
                                                   topic="pilot", message="hello\r\nBcc: attacker@evil.example", client=c)

    assert run(go()) is True
    j = seen["json"]
    assert j["to"] == ["team@kell.example"] and j["from"] == "forms@kell.example"  # fixed, never the visitor's
    assert j["reply_to"] == "ada@example.com"
    assert j["subject"] == "New kell contact request"  # a constant
    assert "attacker" not in json.dumps({k: v for k, v in j.items() if k != "text"})
    assert seen["auth"] == "Bearer re_key"


def test_notification_is_skipped_not_failed_when_email_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", None)
    assert run(contact.send_notification(lead_id="L", name="A", email="a@b.co", company=None, topic="general", message="m")) is False


# --- the HTTP route -------------------------------------------------------------

def test_route_maps_refusals_to_status_codes_and_never_echoes_the_row(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import contact as contact_api

    monkeypatch.setattr(contact, "verify_turnstile", _no)  # never reach Cloudflare from a test
    app = FastAPI()
    app.include_router(contact_api.router)
    app.state.pool = FakePool()
    client = TestClient(app)

    body = {"name": "Ada", "email": "ada@example.com", "message": "hi", "turnstile_token": "t"}
    # The route has no verify override: a failed Turnstile check must come back as 403.
    r = client.post("/v1/contact", json=body)
    assert r.status_code == 403
    r = client.post("/v1/contact", json={**body, "website": "x"})  # honeypot
    assert r.status_code == 202 and r.json() == {"ok": True}
    r = client.post("/v1/contact", json={**body, "unknown_field": 1})
    assert r.status_code == 422
