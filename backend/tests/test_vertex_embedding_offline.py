"""
Offline coverage for Embedder._embed_vertex (2026-09-23): Vertex AI's
native embedding :predict endpoint, authenticated via OAuth2/ADC instead
of a Gemini API key -- billed against real IAM-based project quota/credits
rather than the shared free-tier API-key pool _embed_gemini draws on.

No network, no real ADC: google.auth.default is monkeypatched to a fake
credentials object (same technique used for live testing this session),
and httpx.AsyncClient.post is monkeypatched to a fake responder so these
tests never touch the real Vertex endpoint or spend real credits.
"""
from __future__ import annotations

import json

import google.auth
import httpx
import pytest

from app.config import settings
from app.services.embeddings import Embedder, EmbeddingError


class _FakeCredentials:
    def __init__(self, token: str = "fake-adc-token"):
        self.token = token
        self.valid = True

    def refresh(self, request):
        pass


@pytest.fixture(autouse=True)
def _vertex_config(monkeypatch):
    monkeypatch.setattr(settings, "vertex_project", "test-project")
    monkeypatch.setattr(settings, "vertex_region", "us-central1")
    monkeypatch.setattr(settings, "gemini_embedding_model", "gemini-embedding-001")
    monkeypatch.setattr(settings, "embedding_dimension", 1024)
    monkeypatch.setattr(
        google.auth, "default", lambda *a, **kw: (_FakeCredentials(), "test-project")
    )


def _fake_predict_response(vectors: list[list[float]]) -> dict:
    return {
        "predictions": [
            {"embeddings": {"values": v, "statistics": {"truncated": False, "token_count": 7}}}
            for v in vectors
        ]
    }


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient as an async context manager -- only
    `.post()` is exercised by _embed_vertex."""

    def __init__(self, response_factory, *, status_code: int = 200, **kwargs):
        self._response_factory = response_factory
        self._status_code = status_code
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers, "json": json})
        body = self._response_factory()
        request = httpx.Request("POST", url)
        return httpx.Response(self._status_code, json=body, request=request)


@pytest.mark.asyncio
async def test_embed_vertex_returns_real_shaped_vectors(monkeypatch):
    fake_client_holder: dict = {}

    def make_client(*args, **kwargs):
        client = _FakeAsyncClient(lambda: _fake_predict_response([[0.1, 0.2, 0.3]]))
        fake_client_holder["client"] = client
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    monkeypatch.setattr(Embedder, "_check_dimension", lambda self, vectors, model_id: None)

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    vectors = await embedder._embed_vertex(["find the largest function"], "query")

    assert vectors == [[0.1, 0.2, 0.3]]
    req = fake_client_holder["client"].requests[0]
    assert req["url"].endswith(":predict")
    assert req["headers"]["Authorization"] == "Bearer fake-adc-token"
    assert req["json"]["instances"] == [{"content": "find the largest function", "task_type": "RETRIEVAL_QUERY"}]
    assert req["json"]["parameters"]["outputDimensionality"] == 1024


@pytest.mark.asyncio
async def test_embed_vertex_uses_retrieval_document_task_type_for_documents(monkeypatch):
    fake_client_holder: dict = {}

    def make_client(*args, **kwargs):
        client = _FakeAsyncClient(lambda: _fake_predict_response([[0.4, 0.5]]))
        fake_client_holder["client"] = client
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    monkeypatch.setattr(Embedder, "_check_dimension", lambda self, vectors, model_id: None)

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    await embedder._embed_vertex(["some document text"], "document")

    req = fake_client_holder["client"].requests[0]
    assert req["json"]["instances"][0]["task_type"] == "RETRIEVAL_DOCUMENT"


@pytest.mark.asyncio
async def test_embed_vertex_raises_when_no_project_configured(monkeypatch):
    monkeypatch.setattr(settings, "vertex_project", "")

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    with pytest.raises(EmbeddingError, match="no Vertex project configured"):
        await embedder._embed_vertex(["x"], "query")


@pytest.mark.asyncio
async def test_embed_vertex_raises_a_real_embeddingerror_on_a_non_200_response(monkeypatch):
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(
            lambda: {"error": {"code": 400, "message": "bad request"}}, status_code=400,
        )

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    with pytest.raises(EmbeddingError, match="Vertex embedding failed"):
        await embedder._embed_vertex(["x"], "query")


@pytest.mark.asyncio
async def test_embed_vertex_raises_on_a_prediction_count_mismatch(monkeypatch):
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(lambda: _fake_predict_response([[0.1, 0.2]]))  # 1 prediction

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    with pytest.raises(EmbeddingError, match=r"Vertex returned 1 embeddings for 2 texts"):
        await embedder._embed_vertex(["x", "y"], "query")  # 2 texts requested


@pytest.mark.asyncio
async def test_embed_vertex_raises_when_adc_is_unavailable(monkeypatch):
    def _boom(*a, **kw):
        raise Exception("no ADC found")

    monkeypatch.setattr(google.auth, "default", _boom)

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    with pytest.raises(EmbeddingError, match="Vertex ADC unavailable"):
        await embedder._embed_vertex(["x"], "query")


@pytest.mark.asyncio
async def test_embedding_model_id_reports_vertex_provider(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider_chain", "vertex")
    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    assert embedder.embedding_model_id() == "vertex:gemini-embedding-001"


@pytest.mark.asyncio
async def test_embed_configured_provider_dispatches_to_vertex(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider_chain", "vertex")

    async def fake_embed_vertex(self, texts, input_type):
        return [[0.9] * 1024 for _ in texts]

    monkeypatch.setattr(Embedder, "_embed_vertex", fake_embed_vertex)
    monkeypatch.setattr(Embedder, "_check_dimension", lambda self, vectors, model_id: None)

    embedder = Embedder(model="gemini-embedding-001", dimension=1024)
    vectors = await embedder._embed_configured_provider(["x"], "query")
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
