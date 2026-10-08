"""`POST /routing/codes` (app/mcp_server/server.py): semantic codes for a repository's own Ways.

What these prove: a verified token is required; text is embedded and coded with the codebook's model and nothing
is stored; kept vectors are re-coded without any embedding call; a mismatched embedding model gives no codes;
a deployment without a codebook says so."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

import app.mcp_server.server as srv
from app.mcp_server import find_ways_triage as ft
from app.routing import semantic_codes as sc


def _post(body, *, token=True):
    async def go():
        transport = httpx.ASGITransport(app=srv.app, client=("127.0.0.1", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            return await client.post("/routing/codes", json=body, headers={"host": "127.0.0.1"})
    return asyncio.run(go())


@pytest.fixture
def codebook():
    rng = np.random.default_rng(0)
    x = np.concatenate([c + 0.05 * rng.normal(size=(40, 8)) for c in rng.normal(size=(3, 8))])
    return sc.build(x, k1=3, k2=2, min_group=10, version="cb-test", embedding_model_id="m-1"), x


@pytest.fixture(autouse=True)
def _env(monkeypatch, codebook):
    monkeypatch.setattr(ft, "WINDOW", ft.CallWindow(30, 60.0))
    monkeypatch.setattr(srv.settings, "deployment_mode", "single_user", raising=False)
    monkeypatch.setattr(sc, "load", lambda *a, **k: codebook[0])

    async def gate(request):
        return None
    monkeypatch.setattr(srv, "_route_gate", gate)


def _signed_in(monkeypatch, who="user-1"):
    async def tok(request):
        return SimpleNamespace(subject=who)
    monkeypatch.setattr(srv, "_route_token", tok)


class FakeEmbedder:
    calls: list = []

    def __init__(self, model="m-1", vectors=None):
        self.model, self.vectors = model, vectors

    def embedding_model_id(self):
        return self.model

    async def embed(self, texts, input_type="document"):
        FakeEmbedder.calls.append(list(texts))
        return [list(v) for v in self.vectors[: len(texts)]]


def test_a_token_is_required(monkeypatch):
    async def none(request):
        return None
    monkeypatch.setattr(srv, "_route_token", none)
    assert _post({"items": [{"id": "W-1", "text": "x"}]}).status_code == 401


def test_texts_are_embedded_coded_and_returned_with_their_vectors(monkeypatch, codebook):
    cb, x = codebook
    _signed_in(monkeypatch)
    FakeEmbedder.calls = []
    monkeypatch.setattr("app.services.embeddings.Embedder", lambda: FakeEmbedder(vectors=x[[0, 50]]))
    r = _post({"items": [{"id": "W-a", "text": "edit config"}, {"id": "W-b", "text": "write migration"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "cb-test" and body["codes"] == {"W-a": cb.assign(x[0]), "W-b": cb.assign(x[50])}
    assert set(body["vectors"]) == {"W-a", "W-b"} and len(body["vectors"]["W-a"]) == 8
    assert FakeEmbedder.calls == [["edit config", "write migration"]]


def test_kept_vectors_are_recoded_without_embedding(monkeypatch, codebook):
    cb, x = codebook
    _signed_in(monkeypatch)

    def boom():
        raise AssertionError("no embedding call for kept vectors")
    monkeypatch.setattr("app.services.embeddings.Embedder", boom)
    r = _post({"vectors": {"W-a": x[90].tolist(), "W-bad": [1.0]}})
    assert r.status_code == 200 and r.json()["codes"] == {"W-a": cb.assign(x[90])}


def test_a_different_embedding_model_gives_no_codes(monkeypatch, codebook):
    _signed_in(monkeypatch)
    monkeypatch.setattr("app.services.embeddings.Embedder", lambda: FakeEmbedder(model="other", vectors=codebook[1]))
    r = _post({"items": [{"id": "W-a", "text": "x"}]})
    assert r.status_code == 409 and r.json()["codes"] == {}


def test_no_codebook_and_bad_bodies(monkeypatch):
    _signed_in(monkeypatch)
    assert _post({"items": []}).status_code == 400
    monkeypatch.setattr(sc, "load", lambda *a, **k: None)
    assert _post({"items": [{"id": "W-a", "text": "x"}]}).status_code == 503
