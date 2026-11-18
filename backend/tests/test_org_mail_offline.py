"""The invitation email: text, configuration and the Resend call. No network."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services import org_mail


def run(coro):
    return asyncio.run(coro)


def test_the_message_names_the_organization_role_link_and_code():
    subject, body = org_mail.invite_text(org_name="Acme", code="sl1_abc_def", role="admin",
                                         expires_at="2026-12-01T00:00:00+00:00",
                                         link="https://kell.example/join?code=sl1_abc_def")
    assert "Acme" in subject and "admin" in body
    assert "https://kell.example/join?code=sl1_abc_def" in body
    assert "sl1_abc_def" in body and "2026-12-01" in body


def test_without_configuration_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", None, raising=False)
    monkeypatch.setattr(settings, "contact_notify_from", None, raising=False)
    assert run(org_mail.send_invite(to="ada@acme.co", org_name="Acme", code="c", role="member",
                                    expires_at="2026-12-01", link="https://x/join")) is False


def test_a_configured_message_is_plain_text_and_carries_the_code(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_test", raising=False)
    monkeypatch.setattr(settings, "contact_notify_from", "kell@example.org", raising=False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "email_1"})

    async def send():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await org_mail.send_invite(to="ada@acme.co", org_name="Acme", code="sl1_secret",
                                              role="member", expires_at="2026-12-01",
                                              link="https://kell.example/join?code=sl1_secret", client=client)

    assert run(send()) is True
    assert seen["from"] == "kell@example.org" and seen["to"] == ["ada@acme.co"]
    assert "sl1_secret" in seen["text"] and "https://kell.example/join?code=sl1_secret" in seen["text"]


def test_a_provider_refusal_is_false_not_an_exception(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_test", raising=False)
    monkeypatch.setattr(settings, "contact_notify_from", "kell@example.org", raising=False)

    async def send():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            return await org_mail.send_invite(to="ada@acme.co", org_name="Acme", code="c", role="member",
                                              expires_at="2026-12-01", link="https://x/join", client=client)

    assert run(send()) is False
