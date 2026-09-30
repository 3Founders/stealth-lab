"""
Offline proving tests for the Embedder cache pass.

WHAT CHANGED, and why it needs pinning:

`Embedder.embed` used to hash every text twice (once to look the cache up,
once to store the result) and sent every cache MISS to the provider, even
when the same text appeared more than once in the same call. Ingest batches
repeat text constantly -- identical claim statements, identical boilerplate
across documents -- and the provider call is both paid and rate-limited, so
each repeat was a wasted round trip for a vector already in hand. The
experiment-path embedder in embed_cache.py has always deduplicated here;
this closes the gap between the two.

The behaviour that must NOT change is the cache key's meaning: it hashes
model + dimension + task_type + the whole text, so two different vector
spaces can never collide, and a changed task_type is a different entry.
These tests assert the key is still exactly that.

Fully offline. No provider, no network, no database, no clock. The provider
is a fake that records the exact input list it was handed, which is the only
thing that matters here -- the cache pass is what is under test, not the SDK.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import embeddings as em
from app.services.embeddings import Embedder, EmbeddingError


class RecordingProvider:
    """Stands in for _embed_configured_provider. Records every call and
    every input it was given, and returns one distinct vector per input so a
    dedup bug shows up as a value mismatch rather than silently passing."""

    def __init__(self):
        self.calls: list[list[str]] = []

    async def __call__(self, texts, input_type):
        self.calls.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


@pytest.fixture(autouse=True)
def _clean_cache():
    """The embed cache is process-global and shared. Isolate every test so
    ordering cannot decide whether a test sees a hit."""
    with em._EMBED_CACHE_LOCK:
        em._EMBED_CACHE.clear()
        em._EMBED_CACHE_ORDER.clear()
    yield
    with em._EMBED_CACHE_LOCK:
        em._EMBED_CACHE.clear()
        em._EMBED_CACHE_ORDER.clear()


def _embedder(provider) -> Embedder:
    e = Embedder(model="test-model", dimension=1024)
    e._embed_configured_provider = provider
    return e


# ------------------------------------------------------------ deduplication


def test_duplicate_texts_cost_one_provider_input_not_k():
    """THE assertion. k copies of the same text -> one provider input."""
    calls: list[list[str]] = []

    async def provider(texts, input_type):
        calls.append(list(texts))
        return [[1.0] for _ in texts]

    e = _embedder(provider)
    out = asyncio.run(e.embed(["same text"] * 5, input_type="document"))

    assert len(calls) == 1
    assert calls[0] == ["same text"]
    # And all five positions got the same vector, not five independent ones.
    assert out == [[1.0]] * 5
    assert all(v == out[0] for v in out)


def test_duplicates_and_distinct_texts_are_both_handled():
    """Mixed batch: repeated text collapses, distinct text survives."""
    calls: list[list[str]] = []
    lengths = {"a": 1.0, "b": 2.0, "c": 3.0}

    async def provider(texts, input_type):
        calls.append(list(texts))
        return [[lengths[t]] for t in texts]

    e = _embedder(provider)
    out = asyncio.run(e.embed(["a", "b", "a", "c", "a", "b"], input_type="document"))

    assert calls == [["a", "b", "c"]], "provider saw duplicates"
    assert out == [[1.0], [2.0], [1.0], [3.0], [1.0], [2.0]]


def test_repeat_across_calls_is_served_from_cache():
    """First call pays; the second identical call costs zero provider input.
    This is the property that makes the dedup above worth having at all."""
    calls: list[list[str]] = []

    async def provider(texts, input_type):
        calls.append(list(texts))
        return [[7.0] for _ in texts]

    e = _embedder(provider)
    asyncio.run(e.embed(["x", "y", "x"], input_type="document"))
    assert len(calls) == 1
    asyncio.run(e.embed(["x", "y", "x"], input_type="document"))
    assert len(calls) == 1, "second call hit the provider"
    assert calls[0] == ["x", "y"]


def test_a_deduplicated_batch_still_populates_the_cache_for_every_text():
    """After a dedup'd call, each unique text must be cached, or the next
    identical batch would go back to the provider."""
    calls: list[list[str]] = []

    async def provider(texts, input_type):
        calls.append(list(texts))
        return [[3.0] for _ in texts]

    e = _embedder(provider)
    asyncio.run(e.embed(["p", "p", "q"], input_type="document"))
    n_after_first = len(calls)
    asyncio.run(e.embed(["p", "p", "q"], input_type="document"))
    assert len(calls) == n_after_first, "cache was not populated by the dedup'd call"


# ------------------------------------------------------ key still means what


def test_cache_key_still_binds_model_dimension_and_task_type():
    """The dedup must not have loosened the key into something that could
    collide across vector spaces. Same text, different identity -> different
    key, so both get embedded rather than one stealing the other's vector."""
    base = em._cache_key("m", 1024, "RETRIEVAL_DOCUMENT", "same")
    assert base != em._cache_key("m2", 1024, "RETRIEVAL_DOCUMENT", "same")
    assert base != em._cache_key("m", 512, "RETRIEVAL_DOCUMENT", "same")
    assert base != em._cache_key("m", 1024, "RETRIEVAL_QUERY", "same")
    assert base == em._cache_key("m", 1024, "RETRIEVAL_DOCUMENT", "same")


def test_query_and_document_never_share_a_cache_entry():
    calls: list[list[str]] = []

    async def provider(texts, input_type):
        calls.append(list(texts))
        return [[1.0] for _ in texts]

    e = _embedder(provider)
    asyncio.run(e.embed(["t"], input_type="query"))
    asyncio.run(e.embed(["t"], input_type="document"))
    assert len(calls) == 2, "query and document shared a cache entry"


# --------------------------------------------------------- failure behaviour


def test_provider_returning_the_wrong_vector_count_raises_a_clear_error():
    """Previously a short provider reply surfaced as a bare KeyError from the
    return comprehension, which reads like a cache bug. It now says what
    actually happened."""
    async def provider(texts, input_type):
        return [[1.0]]  # one vector, three inputs

    e = _embedder(provider)
    with pytest.raises(EmbeddingError, match="returned 1 vectors for 3 inputs"):
        asyncio.run(e.embed(["a", "b", "c"], input_type="document"))


def test_empty_batch_never_calls_the_provider():
    calls: list[list[str]] = []

    async def provider(texts, input_type):
        calls.append(list(texts))
        return []

    e = _embedder(provider)
    assert asyncio.run(e.embed([], input_type="document")) == []
    assert calls == []


# ------------------------------------------------------- provider clients


@pytest.fixture
def fake_voyage_sdk(monkeypatch):
    """Replace the Voyage SDK class itself.

    Patching the constructor rather than calling the real client is
    deliberate: a test must never be one refactor away from spending real
    quota against a real key picked up from backend/.env.
    """
    import voyageai

    built: list[tuple[str, Any]] = []

    class FakeVoyage:
        def __init__(self, api_key, max_retries=None):
            self.api_key = api_key
            self.max_retries = max_retries
            built.append((api_key, max_retries))

    monkeypatch.setattr(voyageai, "AsyncClient", FakeVoyage)
    em._voyage_clients.clear()
    yield built
    em._voyage_clients.clear()


def test_voyage_client_is_reused_across_calls(fake_voyage_sdk):
    """One client per configuration for the process lifetime. A fresh client
    per call meant a fresh TCP+TLS handshake to Voyage on every embed()."""
    a = em._voyage_client("key-A", 3)
    b = em._voyage_client("key-A", 3)
    assert a is b, "same key + same max_retries must reuse its client"
    assert fake_voyage_sdk == [("key-A", 3)], "client was rebuilt instead of reused"


def test_changing_max_retries_does_not_serve_a_stale_client(fake_voyage_sdk):
    """The cache key covers every constructor argument, not just the key.

    VOYAGE_MAX_RETRIES is a setting. A client cached under an older value
    would keep retrying the old number of times after the setting changed,
    which is a live-behaviour bug that a test changing the setting would
    otherwise have caught only by accident.
    """
    a = em._voyage_client("key-A", 0)
    b = em._voyage_client("key-A", 3)
    assert a is not b, "a changed max_retries must not reuse the old client"
    assert a.max_retries == 0
    assert b.max_retries == 3


def test_different_keys_get_different_cached_clients(fake_voyage_sdk):
    """Key rotation only works if each key keeps its own client -- sharing one
    would send one key's traffic over another key's connection and defeat the
    per-key quota independence the rotation exists for."""
    a = em._voyage_client("key-A", 3)
    b = em._voyage_client("key-B", 3)
    assert a is not b
    assert sorted(fake_voyage_sdk) == [("key-A", 3), ("key-B", 3)]
