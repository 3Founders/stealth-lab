"""Offline: Vertex model failover (app/services/ingestion_jobs._VertexOAuthCompletions)."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError

from app.services import ingestion_jobs as ij

A, B = "google/gemini-3.8-flash", "google/gemini-3.7-flash"


def _429():
    req = httpx.Request("POST", "https://example.test")
    return RateLimitError("Resource exhausted", response=httpx.Response(429, request=req), body=None)


class FakeClient:
    def __init__(self, down: set[str]):
        self.down = down
        self.calls: list[tuple[str, object]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._retries = "default"

    def with_options(self, **kw):
        clone = FakeClient(self.down)
        clone.calls = self.calls
        clone._retries = kw.get("max_retries")
        return clone

    def _create(self, **kw):
        self.calls.append((kw["model"], self._retries))
        if kw["model"] in self.down:
            raise _429()
        return SimpleNamespace(model="raw", choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


def _completions(client, fallbacks=(B,)):
    return ij._VertexOAuthCompletions(client, A, None, list(fallbacks))


def test_a_refused_call_fails_over_to_the_next_model_and_records_which_answered():
    client = FakeClient(down={A})
    resp = _completions(client).create(messages=[], max_tokens=50)
    assert resp.model == B
    assert client.calls == [(A, 0), (B, "default")]      # no client retries on A: B is the retry


def test_the_primary_is_used_while_it_answers():
    client = FakeClient(down=set())
    assert _completions(client).create(messages=[]).model == A
    assert [m for m, _ in client.calls] == [A]


def test_a_model_refusing_repeatedly_is_skipped_then_tried_again_after_the_cooldown(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ij.time, "monotonic", lambda: clock[0])
    client = FakeClient(down={A})
    comp = _completions(client)
    for _ in range(comp._TRIP):
        comp.create(messages=[])
    client.calls.clear()
    comp.create(messages=[])
    assert [m for m, _ in client.calls] == [B]            # A is cooling down: not even tried
    client.down = set()
    clock[0] += comp._COOLDOWN_S + 1
    client.calls.clear()
    assert comp.create(messages=[]).model == A            # after the cooldown the primary is first again


def test_every_model_refusing_raises_the_rate_limit():
    client = FakeClient(down={A, B})
    with pytest.raises(RateLimitError):
        _completions(client).create(messages=[])


def test_without_fallbacks_behaviour_is_unchanged():
    client = FakeClient(down={A})
    with pytest.raises(RateLimitError):
        _completions(client, fallbacks=()).create(messages=[])
    assert client.calls == [(A, "default")]               # the client's own retries, as before
