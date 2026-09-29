"""Offline: Vertex embeddings are paced, batched and retried (first production SkillMD run: 37 of 40 items failed on
429s because every text was its own request against a quota of requests/min). No network: fake senders / fake httpx."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.services import embeddings as emb
from app.services.embeddings import EmbeddingError, _VertexBatcher


def _run(coro):
    return asyncio.run(coro)


def _vec(text: str) -> list[float]:
    return [float(len(text)), 1.0]


class _Sender:
    def __init__(self, fail_first: int = 0):
        self.calls: list[tuple[list[str], str]] = []
        self.fail_first = fail_first

    async def __call__(self, texts, input_type):
        self.calls.append((list(texts), input_type))
        if len(self.calls) <= self.fail_first:
            raise EmbeddingError("Vertex embedding failed: 429")
        return [_vec(t) for t in texts]


def test_texts_that_pile_up_while_the_flusher_waits_go_out_as_one_request():
    async def go():
        send = _Sender()
        b = _VertexBatcher(send, rpm=600, tpm=1_000_000, max_batch=64)   # 0.1 s between requests
        out = await asyncio.gather(*(b.submit(f"text-{i}", "document") for i in range(20)))
        return send, b, out

    send, b, out = _run(go())
    assert out == [_vec(f"text-{i}") for i in range(20)], "every caller gets ITS vector, in order"
    assert len(send.calls) <= 2 and sum(len(c[0]) for c in send.calls) == 20
    assert b.texts_sent == 20 and b.requests_sent == len(send.calls)


def test_a_request_never_exceeds_max_batch():
    async def go():
        send = _Sender()
        b = _VertexBatcher(send, rpm=6000, tpm=10_000_000, max_batch=8)
        await asyncio.gather(*(b.submit(f"t{i}", "document") for i in range(30)))
        return send

    send = _run(go())
    assert all(len(c[0]) <= 8 for c in send.calls) and sum(len(c[0]) for c in send.calls) == 30


def test_a_request_carries_one_task_type():
    async def go():
        send = _Sender()
        b = _VertexBatcher(send, rpm=6000, tpm=10_000_000, max_batch=64)
        await asyncio.gather(b.submit("a", "document"), b.submit("b", "query"), b.submit("c", "document"))
        return send

    send = _run(go())
    for texts, input_type in send.calls:
        assert input_type in ("document", "query")
    assert {t for c in send.calls if c[1] == "query" for t in c[0]} == {"b"}
    assert {t for c in send.calls if c[1] == "document" for t in c[0]} == {"a", "c"}


def test_requests_are_paced_at_the_configured_rate():
    async def go():
        send = _Sender()
        b = _VertexBatcher(send, rpm=300, tpm=10_000_000, max_batch=1)   # 0.2 s apart, one text per request
        t0 = time.monotonic()
        await asyncio.gather(*(b.submit(f"t{i}", "document") for i in range(4)))
        return time.monotonic() - t0, send

    elapsed, send = _run(go())
    assert len(send.calls) == 4
    assert elapsed >= 0.55, "4 requests at 5/s cannot finish faster than 3 gaps of 0.2 s"


def test_the_token_cap_splits_a_batch():
    async def go():
        send = _Sender()
        # 60_000 tokens/min -> 30_000-token requests; each text ~10_000 tokens (len//3)
        b = _VertexBatcher(send, rpm=6000, tpm=60_000, max_batch=64)
        await asyncio.gather(*(b.submit("x" * 30_000, "document") for _ in range(6)))
        return send

    send = _run(go())
    assert all(len(c[0]) <= 3 for c in send.calls)


def test_one_failed_request_fails_every_waiting_caller_and_the_queue_recovers():
    async def go():
        send = _Sender(fail_first=1)
        b = _VertexBatcher(send, rpm=6000, tpm=10_000_000, max_batch=64)
        first = await asyncio.gather(*(b.submit(f"a{i}", "document") for i in range(3)), return_exceptions=True)
        second = await b.submit("later", "document")
        return first, second

    first, second = _run(go())
    assert all(isinstance(r, EmbeddingError) and "429" in str(r) for r in first)
    assert second == _vec("later"), "the flusher restarts for the next caller"


def test_a_wrong_vector_count_is_an_error_not_a_silent_shift():
    async def go():
        async def short(texts, input_type):
            return [_vec(texts[0])]

        b = _VertexBatcher(short, rpm=6000, tpm=10_000_000, max_batch=64)
        return await asyncio.gather(b.submit("a", "document"), b.submit("b", "document"), return_exceptions=True)

    out = _run(go())
    assert all(isinstance(r, EmbeddingError) for r in out)


def test_a_cancelled_caller_does_not_break_the_others():
    async def go():
        send = _Sender()
        b = _VertexBatcher(send, rpm=600, tpm=10_000_000, max_batch=64)
        keep = asyncio.ensure_future(b.submit("keep", "document"))
        drop = asyncio.ensure_future(b.submit("drop", "document"))
        await asyncio.sleep(0)
        drop.cancel()
        return await keep

    assert _run(go()) == _vec("keep")


# ----------------------------------------------------------------------------- the Embedder path and the retry

class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"Client error '{self.status_code} Too Many Requests'")


class _FakeClient:
    script: list = []
    posts: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).posts.append(json)
        resp = type(self).script.pop(0)
        if resp.status_code == 200 and resp._body is None:          # "OK": one vector per instance actually sent
            dim = json["parameters"]["outputDimensionality"]
            resp._body = {"predictions": [{"embeddings": {"values": [0.1] * dim}} for _ in json["instances"]]}
        return resp


@pytest.fixture()
def vertex(monkeypatch):
    import httpx

    monkeypatch.setattr(emb.settings, "vertex_project", "p", raising=False)
    monkeypatch.setattr(emb.settings, "embedding_provider_chain", "vertex", raising=False)
    monkeypatch.setattr(emb.settings, "vertex_embed_rpm", 6000, raising=False)
    monkeypatch.setattr(emb.settings, "vertex_embed_max_retries", 3, raising=False)
    monkeypatch.setattr(emb.settings, "use_local_models", False, raising=False)

    async def creds():
        return type("C", (), {"token": "t"})()

    monkeypatch.setattr(emb, "_vertex_credentials", creds)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.script, _FakeClient.posts = [], []
    emb._VERTEX_BATCHERS.clear()
    emb._VERTEX_HTTP_CLIENTS.clear()
    return _FakeClient


def _ok(*_ignored):
    return _Resp(200)                       # sized by the request when it arrives


def test_a_429_is_retried_and_then_succeeds(vertex):
    e = emb.Embedder()
    tag = uuid.uuid4().hex
    vertex.script = [_Resp(429, headers={"retry-after": "0"}), _ok()]
    out = _run(e.embed([f"alpha {tag}", f"beta {tag}"], input_type="document"))
    assert len(out) == 2 and len(vertex.posts) == 2, "one retry, same two texts in ONE request each time"
    assert len(vertex.posts[1]["instances"]) == 2


def test_retries_are_bounded_and_the_error_keeps_the_status_code(vertex):
    e = emb.Embedder()
    vertex.script = [_Resp(429, headers={"retry-after": "0"}) for _ in range(4)]
    with pytest.raises(EmbeddingError) as exc:
        _run(e.embed([f"alpha {uuid.uuid4().hex}"], input_type="document"))
    assert "429" in str(exc.value) and len(vertex.posts) == 4        # 1 try + 3 retries; embed_cache matches on "429"


def test_concurrent_single_text_calls_share_requests(vertex):
    """The ingestion shape: several workers each embedding ONE text at once."""
    e = emb.Embedder()

    async def go():
        vertex.script = [_ok() for _ in range(6)]
        tag = uuid.uuid4().hex
        return await asyncio.gather(*(e.embed([f"goal {i} {tag}"], input_type="document") for i in range(6)))

    out = _run(go())
    assert [len(o) for o in out] == [1] * 6
    assert len(vertex.posts) < 6, "6 single-text calls must not cost 6 requests"


def test_the_parent_is_embedded_before_its_step_procedures_are_written():
    """A failed embedding (quota 429, outage) must leave nothing behind. Found in production: `_persist_independent_steps`
    ran BEFORE the parent's embedding, so a 429 stranded `active` step procedures with no vector and no provenance.
    Structural on purpose: the function is ~3,000 lines and needs a live database to run end to end."""
    import pathlib

    src = pathlib.Path(emb.__file__).with_name("skill_ingestion.py").read_text(encoding="utf-8")
    embed_at = src.index("goal_vec, embedding_metadata = await embedder.embed_one_with_metadata(\n                retrieval_doc")
    steps_at = src.index("independent_step_procedure_ids.extend(await _persist_independent_steps(")
    capture_at = src.index("result = await capture_procedure(", steps_at)
    assert embed_at < steps_at < capture_at


# ------------------------------------------------------------------------------------------------ gemini-embedding-2

class _Gen2Client(_FakeClient):
    urls: list = []

    async def post(self, url, headers=None, json=None):
        type(self).urls.append(url)
        type(self).posts.append(json)
        dim = json["outputDimensionality"]
        return _Resp(200, {"embedding": {"values": [0.5] * dim}})


@pytest.fixture()
def gen2(monkeypatch, vertex):
    import httpx

    monkeypatch.setattr(emb.settings, "gemini_embedding_model", "gemini-embedding-2", raising=False)
    monkeypatch.setattr(emb.settings, "vertex_embedding_location", "", raising=False)
    monkeypatch.setattr(emb.settings, "vertex_embed_rpm_gen2", 60_000, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _Gen2Client)
    _Gen2Client.urls, _Gen2Client.posts = [], []
    emb._VERTEX_BATCHERS.clear()
    emb._VERTEX_HTTP_CLIENTS.clear()
    return _Gen2Client


def test_gen2_uses_the_global_embedcontent_endpoint_and_the_configured_dimension(gen2):
    e = emb.Embedder()
    out = _run(e.embed([f"goal {uuid.uuid4().hex}"], input_type="document"))
    assert len(out) == 1 and len(out[0]) == e.dimension
    assert gen2.urls[0].startswith("https://aiplatform.googleapis.com/v1/projects/p/locations/global/publishers/google/")
    assert gen2.urls[0].endswith("/models/gemini-embedding-2:embedContent"), "not :predict -- that 404s for this model"
    assert gen2.posts[0]["outputDimensionality"] == e.dimension


def test_gen2_states_the_task_in_the_text_because_it_has_no_task_type(gen2):
    e = emb.Embedder()
    tag = uuid.uuid4().hex
    _run(e.embed([f"deploy {tag}"], input_type="document"))
    _run(e.embed([f"deploy {tag}"], input_type="query"))
    texts = [p["content"]["parts"][0]["text"] for p in gen2.posts]
    assert texts == [f"title: none | text: deploy {tag}", f"task: search result | query: deploy {tag}"]
    assert all("task_type" not in p for p in gen2.posts)


def test_gen2_sends_one_request_per_text_and_keeps_order(gen2):
    e = emb.Embedder()
    tag = uuid.uuid4().hex
    out = _run(e.embed([f"a{i} {tag}" for i in range(5)], input_type="document"))
    assert len(out) == 5 and len(gen2.posts) == 5


def test_the_embedding_model_id_changes_with_the_model_so_vector_spaces_are_never_mixed_silently(gen2):
    assert emb.Embedder().embedding_model_id() == "vertex:gemini-embedding-2"


def test_one_http_client_serves_every_request_in_a_loop(gen2):
    """Building a client costs ~1 s of blocking CA-bundle loading on Windows; a client per request made 60 concurrent
    embeds take 63 s against a provider that answered 60 raw requests in 1.4 s."""
    built = []
    real = gen2.__init__

    def counting_init(self, *a, **kw):
        built.append(1)
        real(self, *a, **kw)

    gen2.__init__ = counting_init
    try:
        e = emb.Embedder()
        tag = uuid.uuid4().hex
        _run(e.embed([f"x{i} {tag}" for i in range(20)], input_type="document"))
    finally:
        gen2.__init__ = real
    assert len(gen2.posts) == 20 and len(built) == 1
