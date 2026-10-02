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


def test_every_model_refusing_waits_then_raises_the_rate_limit(monkeypatch):
    slept = []
    monkeypatch.setattr(ij.time, "sleep", lambda s: slept.append(s))
    client = FakeClient(down={A, B})
    comp = _completions(client)
    comp._RATE_LIMIT_WAIT_S = 30.0
    with pytest.raises(RateLimitError):
        comp.create(messages=[])
    assert slept and sum(slept) <= 30.0 + 1e-6                    # waited (growing pauses), bounded, then gave up


def test_a_call_waiting_out_the_limit_succeeds_when_a_model_comes_back(monkeypatch):
    client = FakeClient(down={A, B})

    def recover(_s):
        client.down = set()                                          # capacity returns during the pause
    monkeypatch.setattr(ij.time, "sleep", recover)
    assert _completions(client).create(messages=[]).model == A


def test_without_fallbacks_a_refusal_still_reaches_the_caller_after_the_wait(monkeypatch):
    monkeypatch.setattr(ij.time, "sleep", lambda s: None)
    client = FakeClient(down={A})
    comp = _completions(client, fallbacks=())
    comp._RATE_LIMIT_WAIT_S = 0.0
    with pytest.raises(RateLimitError):
        comp.create(messages=[])
    assert client.calls == [(A, "default")]               # the client's own retries, as before


def test_refused_and_timed_out_judge_calls_are_not_charged_but_answered_ones_are():
    import asyncio

    from app.services.semantic.chain import _unbilled
    from app.services.semantic.errors import ErrorKind, ProviderError

    refused = ProviderError(ErrorKind.TRANSIENT, "vertex: transient: RateLimitError('Error code: 429 ...')")
    assert _unbilled(_429()) and _unbilled(refused) and _unbilled(asyncio.TimeoutError())
    unusable = ProviderError(ErrorKind.TRANSIENT, "invalid identity reply: relation missing")   # the model answered
    assert not _unbilled(unusable)


class LocatedClient:
    """Refuses by (model, base_url): models served only on global, and an older model with capacity per region."""

    def __init__(self, refuse, base_url="https://aiplatform.googleapis.com/global", calls=None):
        self.refuse, self.base_url = refuse, base_url
        self.calls = calls if calls is not None else []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **kw):
        return LocatedClient(self.refuse, kw.get("base_url", self.base_url), self.calls)

    def _create(self, **kw):
        self.calls.append((kw["model"], self.base_url))
        if self.refuse(kw["model"], self.base_url):
            raise _429()
        return SimpleNamespace(model="raw", choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


def test_an_entry_with_a_location_is_called_in_that_location_and_records_the_bare_model():
    C = "google/gemini-3.5-flash"
    client = LocatedClient(lambda m, url: "/global" in url or "europe-west2" in url)
    comp = ij._VertexOAuthCompletions(client, A, None, [f"{C}@europe-west2", f"{C}@europe-west3"], "proj")
    resp = comp.create(messages=[])
    assert resp.model == C                               # the item records the model, not model@location
    assert [m for m, _ in client.calls] == [A, C, C]
    assert client.calls[0][1].endswith("/global")        # the primary keeps the default location
    assert "europe-west2-aiplatform.googleapis.com/v1/projects/proj/locations/europe-west2/" in client.calls[1][1]
    assert "locations/europe-west3/" in client.calls[2][1]


def test_model_slot_parses_entries():
    from app.services.vertex_endpoints import model_slot

    assert model_slot(" google/gemini-3.6-flash ") == ("google/gemini-3.6-flash", "")
    assert model_slot("google/gemini-3.5-flash@europe-west3") == ("google/gemini-3.5-flash", "europe-west3")


def test_the_judge_can_have_its_own_vertex_chain(monkeypatch):
    """Extraction may fail over to gemini-2.5 (it extracts well); the judge must not (it judges identity worse)."""
    import google.auth
    from types import SimpleNamespace as NS

    from app.services.semantic import providers

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (NS(refresh=lambda req: None, valid=True, token="t"), "p"))
    settings = NS(vertex_project="proj", vertex_region="us-central1", vertex_llm_location="global",
                  vertex_model="google/gemini-3.8-flash")
    monkeypatch.setenv("VERTEX_MODEL_FALLBACKS", "google/gemini-3.6-flash,google/gemini-2.5-flash@europe-west3")
    monkeypatch.delenv("JUDGE_VERTEX_MODEL_FALLBACKS", raising=False)
    shared = providers.build_provider("vertex", settings, timeout_s=10)
    assert [c.chat.completions._model for c in shared._clients] == [
        "google/gemini-3.8-flash", "google/gemini-3.6-flash", "google/gemini-2.5-flash"]
    monkeypatch.setenv("JUDGE_VERTEX_MODEL_FALLBACKS", "google/gemini-3.6-flash")
    own = providers.build_provider("vertex", settings, timeout_s=10)
    assert [c.chat.completions._model for c in own._clients] == ["google/gemini-3.8-flash", "google/gemini-3.6-flash"]


def test_the_judge_can_lead_with_its_own_model(monkeypatch):
    """Extraction leads with gemini-2.5-flash; the judge must still lead with a model that passed its replay."""
    import google.auth
    from types import SimpleNamespace as NS

    from app.services.semantic import providers

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (NS(refresh=lambda req: None, valid=True, token="t"), "p"))
    settings = NS(vertex_project="proj", vertex_region="us-central1", vertex_llm_location="global",
                  vertex_model="google/gemini-2.5-flash")
    monkeypatch.setenv("JUDGE_VERTEX_MODEL", "google/gemini-3.6-flash")
    monkeypatch.setenv("JUDGE_VERTEX_MODEL_FALLBACKS", "google/gemini-3.7-flash,google/gemini-3.6-flash")
    p = providers.build_provider("vertex", settings, timeout_s=10)
    assert [c.chat.completions._model for c in p._clients] == ["google/gemini-3.6-flash", "google/gemini-3.7-flash"]
    assert p.model == "google/gemini-3.6-flash"
