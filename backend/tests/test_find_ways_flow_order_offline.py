"""
The order of `find_ways`, pinned: the JEV triage judgment comes FIRST, and nothing is embedded or searched
unless it says the request is worth a lookup.

    request -> [governor] -> triage (JEV first) -> embed -> search -> judge -> resolve

A prompt that is not a reusable task ("explain this function", "rename foo to bar", "thanks, continue") must cost
one cheap judgment and nothing else: no embedding call (the slowest non-judge step, a paid provider call), no
database search, no further judge calls. The hook's `POST /triage` is the same judgment without the MCP handshake
and must not embed either. These tests exist so a later "optimisation" cannot move the embedding ahead of the
judgment (for example to overlap them) without a test failing and someone deciding that on purpose.

The second half is concurrency: the triage step must not serialise concurrent requests, and the per-provider
concurrency cap is a setting (it was a hard-coded 4 shared by every request in the process).
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

import app.mcp_server.server as srv
from app.mcp_server import find_ways_triage as ft
from app.services.embeddings import Embedder
from app.services.semantic.chain import ChainResult
from app.services.semantic.providers import build_provider


class Ctx:
    request_context = SimpleNamespace(lifespan_context={"pool": None})


class TimedJudge:
    """Records when each judgment starts and ends, in one shared event log."""

    def __init__(self, events, kind, delay=0.05, confidence=0.95):
        self.events, self.kind, self.delay, self.confidence = events, kind, delay, confidence

    async def judge_triage(self, query):
        self.events.append("triage:start")
        await asyncio.sleep(self.delay)
        self.events.append("triage:end")
        return ChainResult(True, {"kind": self.kind, "confidence": self.confidence}, provider="jev", model="jev-latest")


@pytest.fixture
def flow(monkeypatch):
    """Triage on, governor off, a clean memo, an event log, and an Embedder that logs instead of calling out."""
    monkeypatch.setattr(srv.settings, "find_ways_triage", True)
    monkeypatch.setattr(srv.settings, "find_ways_governor", False)
    monkeypatch.setattr(ft, "CACHE", ft.TriageCache())
    monkeypatch.setattr(ft, "WINDOW", ft.CallWindow(1000, 60.0))
    events: list[str] = []

    async def record(ctx, query, reply, shards, ms, governor=None):
        events.append(f"recorded:{governor or 'search'}")

    async def embed_one(self, text, input_type="document"):
        events.append("embed")
        return [0.0] * 8

    monkeypatch.setattr(srv, "_record_find_ways", record)
    monkeypatch.setattr(Embedder, "embed_one", embed_one)

    async def search_standin(query, ctx, **kwargs):
        # what _find_ways_impl does first: embed the query, then search with it
        await Embedder().embed_one(query, input_type="query")
        events.append("search")
        return json.dumps({"outcome": "no_match"})

    monkeypatch.setattr(srv, "_find_ways_impl", search_standin)
    return events


def _judge(monkeypatch, judge):
    monkeypatch.setattr("app.services.retrieval_service.default_judge", lambda: judge)


def _run(coro):
    return asyncio.run(coro)


def test_a_reusable_task_is_judged_before_anything_is_embedded(monkeypatch, flow):
    _judge(monkeypatch, TimedJudge(flow, "reusable_task"))
    _run(srv.find_ways("add retry with backoff to the http client", Ctx()))
    assert flow[:4] == ["triage:start", "triage:end", "embed", "search"], flow


def test_a_request_that_needs_no_lookup_is_never_embedded_or_searched(monkeypatch, flow):
    _judge(monkeypatch, TimedJudge(flow, "knowledge_question"))
    body = json.loads(_run(srv.find_ways("what does the 429 status code mean", Ctx())))
    assert body["outcome"] == "not_needed"
    assert "embed" not in flow and "search" not in flow, flow
    assert flow == ["triage:start", "triage:end", "recorded:triage"]


def test_when_the_judge_cannot_answer_the_lookup_runs_after_the_attempt_not_instead_of_it(monkeypatch, flow):
    class Down:
        async def judge_triage(self, query):
            flow.append("triage:start")
            return ChainResult(False, reason="all semantic providers failed")

    _judge(monkeypatch, Down())
    _run(srv.find_ways("add retry with backoff to the http client", Ctx()))
    assert flow[:3] == ["triage:start", "embed", "search"], flow       # fails toward the lookup, but still judged first


def test_the_hooks_triage_route_never_embeds(monkeypatch, flow):
    _judge(monkeypatch, TimedJudge(flow, "conversation"))

    async def go():
        transport = httpx.ASGITransport(app=srv.app, client=("127.0.0.1", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            return await client.post("/triage", json={"query": "thanks, that looks right to me"})

    resp = _run(go())
    assert resp.status_code == 200 and resp.json()["needs_retrieval"] is False
    assert "embed" not in flow and "search" not in flow, flow


def test_the_hook_then_find_ways_judges_a_task_once_and_embeds_after(monkeypatch, flow):
    """The hook asks /triage, gets "look it up", then calls find_ways: ONE judgment in total, then the embedding."""
    _judge(monkeypatch, TimedJudge(flow, "reusable_task"))
    query = "add retry with backoff to the http client"

    async def go():
        transport = httpx.ASGITransport(app=srv.app, client=("127.0.0.1", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            first = await client.post("/triage", json={"query": query})
        await srv.find_ways(query, Ctx())
        return first

    first = _run(go())
    assert first.json()["needs_retrieval"] is True
    assert flow.count("triage:start") == 1, flow
    assert flow.index("triage:end") < flow.index("embed"), flow


# ---- concurrency -----------------------------------------------------------------------------------

def test_concurrent_requests_are_judged_in_parallel_not_one_after_another(monkeypatch, flow):
    """30 requests whose judgment takes 0.2 s finish in about one judgment's time, not thirty."""
    _judge(monkeypatch, TimedJudge(flow, "conversation", delay=0.2))

    async def go():
        t0 = time.monotonic()
        replies = await asyncio.gather(*[srv.find_ways(f"explain what helper number {i} does", Ctx()) for i in range(30)])
        return time.monotonic() - t0, replies

    elapsed, replies = _run(go())
    assert all(json.loads(r)["outcome"] == "not_needed" for r in replies)
    assert elapsed < 1.5, f"30 concurrent triage calls took {elapsed:.2f}s -- they are being serialised"
    assert "embed" not in flow


def _settings(**overrides):
    base = dict(
        general_compute_api_key="k", general_compute_api_keys=None, general_compute_base_url="http://127.0.0.1:1/v1",
        general_compute_fallback_model=None, local_model_name=None, local_judge_model=None, use_local_models=False,
        local_base_url="http://127.0.0.1:1/v1", semantic_gemini_model="m", semantic_gemini_base_url="http://127.0.0.1:1/v1",
        gemini_api_key="g", gemini_api_keys=None, semantic_provider_concurrency=16,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("name", ["gemma", "gemini"])
def test_a_fallback_providers_concurrency_comes_from_the_setting(name):
    provider = build_provider(name, _settings(semantic_provider_concurrency=24), timeout_s=5.0)
    assert provider is not None and provider._sem._value == 24


def test_the_default_provider_concurrency_is_no_longer_four():
    from app.config import Settings

    assert Settings.model_fields["semantic_provider_concurrency"].default == 16
    provider = build_provider("gemma", _settings(semantic_provider_concurrency=0), timeout_s=5.0)
    assert provider._sem._value == 16 or provider._sem._value == 1       # a nonsense value never disables the provider
