"""Embedder's `openrouter` provider, offline: the request it sends, the space it claims, and its failure handling.
The live check (2026-10-09) found OpenRouter's gemini-embedding-2 vectors equal to the Gemini API's (cosine 1.0)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import settings
from app.services import embeddings as emb
from app.services.embeddings import Embedder, EmbeddingError


class _Resp:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(str(self.status_code), request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(self.status_code))


class _Client:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def client(monkeypatch):
    holder = {}

    async def fake_client():
        return holder["c"]

    async def no_sleep(_):
        return None

    monkeypatch.setattr(emb, "_vertex_http_client", fake_client)
    monkeypatch.setattr(emb.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_embedding_model", "gemini-embedding-2")
    monkeypatch.setattr(settings, "openrouter_embedding_model", "")
    return holder


def _ok(n):
    return _Resp(200, {"data": [{"index": i, "embedding": [0.5, 0.5, 0.5, 0.5]} for i in reversed(range(n))]})


def run(coro):
    return asyncio.run(coro)


def test_batched_request_in_the_gemini2_form_and_the_vertex_space_label(client):
    client["c"] = _Client([_ok(2), _ok(1)])
    e = Embedder(provider="openrouter", dimension=4)
    assert len(run(e._embed_openrouter(["a", "b"], "document"))) == 2
    sent = client["c"].calls[0]
    assert sent["url"].endswith("/embeddings")
    assert sent["json"] == {"model": "google/gemini-embedding-2", "dimensions": 4,
                            "input": ["title: none | text: a", "title: none | text: b"]}
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert e.embedding_model_id() == "vertex:gemini-embedding-2", "same vector space as the routing codebook"
    run(e._embed_openrouter(["q"], "query"))
    assert client["c"].calls[1]["json"]["input"] == ["task: search result | query: q"]


def test_retries_on_429_and_transport_errors_then_fails_cleanly(client, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_embed_max_retries", 2)
    client["c"] = _Client([_Resp(429, headers={"retry-after": "1"}), httpx.ConnectError("down"), _ok(1)])
    assert len(run(Embedder(provider="openrouter", dimension=4)._embed_openrouter(["a"], "document"))) == 1
    client["c"] = _Client([_Resp(500)] * 3)
    with pytest.raises(EmbeddingError):
        run(Embedder(provider="openrouter", dimension=4)._embed_openrouter(["a"], "document"))
    client["c"] = _Client([_Resp(200, {"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]})])
    with pytest.raises(EmbeddingError, match="1 embeddings for 2 texts"):
        run(Embedder(provider="openrouter", dimension=4)._embed_openrouter(["a", "b"], "document"))


def test_no_key_and_another_model_get_their_own_label(client, monkeypatch):
    monkeypatch.setattr(settings, "gemini_embedding_model", "gemini-embedding-001")
    assert Embedder(provider="openrouter").embedding_model_id() == "openrouter:google/gemini-embedding-001"
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    with pytest.raises(EmbeddingError, match="OPENROUTER_API_KEY"):
        run(Embedder(provider="openrouter", dimension=4)._embed_openrouter(["a"], "document"))
