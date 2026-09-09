"""
Regression coverage for the T1-v3/B_default provider-crash root cause.

Observed: T1-v3-B_default-42d351a0 / -08e41225 both died in ~5s with
`ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`. Traced
to `app/services/embeddings.py::Embedder._embed_via_chain` ->
`_embed_voyage`, raised from `runner.py:378`
(`Embedder().embed_one(task_description, input_type="query")`) while inside
the MCP client session's own nested anyio TaskGroups (`_open_client_session`),
which is what turns a plain `EmbeddingError` into an `ExceptionGroup` at
session teardown.

Root cause: `voyageai.AsyncClient` ships a REAL tenacity retry/backoff
controller (`_make_retry_controller`, exponential-jitter wait, restricted to
`RateLimitError | ServiceUnavailableError | Timeout`) but it is inert at the
SDK's own `max_retries=0` default. `_embed_voyage` never passed
`max_retries`, so a single 429 (observed: Voyage's reduced "3 RPM / 10K TPM"
billing-tier throttle) failed on its first and only attempt.

Fix: `_embed_voyage` now passes `max_retries=settings.voyage_max_retries`
(default 5) to the real SDK client. This test proves that value actually
reaches the SDK's own retry loop -- it patches only the lowest-level network
call (`voyageai.Embedding.acreate`), so the tenacity `Retrying` controller
that runs in between is the REAL one shipped in site-packages, not a
reimplementation. No network, no real API key.

Run:  cd backend && python -m pytest tests/test_voyage_embedding_retry.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voyageai  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.embeddings import Embedder, EmbeddingError  # noqa: E402


def _fake_response(vectors: list[list[float]]):
    data = [SimpleNamespace(embedding=v) for v in vectors]
    usage = SimpleNamespace(total_tokens=sum(len(v) for v in vectors))
    return SimpleNamespace(data=data, usage=usage)


@pytest.fixture(autouse=True)
def _voyage_key(monkeypatch):
    # _embed_voyage does settings.require("voyage_api_key") before ever
    # touching the network -- give it a dummy key so that check passes and
    # the real retry controller is what gets exercised, not this guard.
    monkeypatch.setattr(settings, "voyage_api_key", "test-key-not-real")


class _FlakyThenOK:
    """Raises `exc_factory()` on the first `n_failures` calls, then returns
    a real, well-formed response -- the exact shape of a rate-limited
    provider that recovers once its window rolls over."""

    def __init__(self, n_failures: int, exc_factory, vectors: list[list[float]]):
        self.n_failures = n_failures
        self.exc_factory = exc_factory
        self.vectors = vectors
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.n_failures:
            raise self.exc_factory()
        return _fake_response(self.vectors)


@pytest.mark.asyncio
async def test_a_transient_rate_limit_recovers_via_the_real_sdk_retry_controller(monkeypatch):
    """The exact observed failure: Voyage returns RateLimitError (the SDK's
    class for the reduced-tier 429) twice, then succeeds on the third real
    attempt. With voyage_max_retries=5 this must succeed and must have
    actually invoked the network call more than once -- proving the SDK's
    own backoff, not a swallowed error, is what recovered it."""
    monkeypatch.setattr(settings, "voyage_max_retries", 5)
    fake = _FlakyThenOK(
        n_failures=2,
        exc_factory=lambda: voyageai.error.RateLimitError("reduced rate limit, 3 RPM"),
        vectors=[[0.1, 0.2, 0.3]],
    )
    monkeypatch.setattr(voyageai.Embedding, "acreate", fake, raising=True)
    monkeypatch.setattr(settings, "embedding_dimension", 3)

    embedder = Embedder(model="voyage-3-large", dimension=3)
    vectors = await embedder._embed_voyage(["find the largest function"], "query")

    assert vectors == [[0.1, 0.2, 0.3]]
    assert fake.calls == 3, "expected exactly 2 failed attempts + 1 successful retry"


@pytest.mark.asyncio
async def test_before_the_fix_max_retries_zero_fails_on_the_first_rate_limit(monkeypatch):
    """Regression guard for the OLD behavior: with voyage_max_retries=0 (the
    SDK's own default, what _embed_voyage passed implicitly before this fix)
    the exact same transient RateLimitError is fatal on attempt 1 -- proving
    the fix is the max_retries value itself, not some other change."""
    monkeypatch.setattr(settings, "voyage_max_retries", 0)
    fake = _FlakyThenOK(
        n_failures=2,
        exc_factory=lambda: voyageai.error.RateLimitError("reduced rate limit, 3 RPM"),
        vectors=[[0.1, 0.2, 0.3]],
    )
    monkeypatch.setattr(voyageai.Embedding, "acreate", fake, raising=True)
    monkeypatch.setattr(settings, "embedding_dimension", 3)

    embedder = Embedder(model="voyage-3-large", dimension=3)
    with pytest.raises(EmbeddingError, match="Voyage embedding failed"):
        await embedder._embed_voyage(["find the largest function"], "query")
    assert fake.calls == 1, "max_retries=0 must mean exactly one attempt, no backoff"


@pytest.mark.asyncio
async def test_exhausting_every_retry_still_raises_a_real_embeddingerror(monkeypatch):
    """The retry budget is BOUNDED, not infinite: a provider that never
    recovers within voyage_max_retries attempts must still fail loudly (as
    an EmbeddingError, so _embed_via_chain's fall-through-to-next-provider
    and eventual all-providers-failed raise both still work), never hang
    and never silently return a wrong/empty result."""
    monkeypatch.setattr(settings, "voyage_max_retries", 3)
    fake = _FlakyThenOK(
        n_failures=999,
        exc_factory=lambda: voyageai.error.RateLimitError("reduced rate limit, 3 RPM"),
        vectors=[[0.1, 0.2, 0.3]],
    )
    monkeypatch.setattr(voyageai.Embedding, "acreate", fake, raising=True)
    monkeypatch.setattr(settings, "embedding_dimension", 3)

    embedder = Embedder(model="voyage-3-large", dimension=3)
    with pytest.raises(EmbeddingError, match="Voyage embedding failed"):
        await embedder._embed_voyage(["find the largest function"], "query")
    assert fake.calls == 3, "must stop at exactly max_retries attempts, not retry forever"


@pytest.mark.asyncio
async def test_a_non_transient_failure_is_never_retried(monkeypatch):
    """No blind retrying: an auth/malformed-request failure is not
    rate-limit-shaped, so the SDK's own retry predicate
    (retry_if_exception_type(RateLimitError | ServiceUnavailableError |
    Timeout)) must NOT retry it -- it fails on the first attempt exactly as
    it did before this fix, even with a generous retry budget configured."""
    monkeypatch.setattr(settings, "voyage_max_retries", 5)
    fake = _FlakyThenOK(
        n_failures=999,
        exc_factory=lambda: voyageai.error.AuthenticationError("invalid API key"),
        vectors=[[0.1, 0.2, 0.3]],
    )
    monkeypatch.setattr(voyageai.Embedding, "acreate", fake, raising=True)
    monkeypatch.setattr(settings, "embedding_dimension", 3)

    embedder = Embedder(model="voyage-3-large", dimension=3)
    with pytest.raises(EmbeddingError, match="Voyage embedding failed"):
        await embedder._embed_voyage(["find the largest function"], "query")
    assert fake.calls == 1, "a non-transient error must never be retried"


@pytest.mark.asyncio
async def test_missing_gemini_key_still_falls_through_to_voyage_unaffected(monkeypatch):
    """The provider-chain fallback this fix must NOT touch: with no Gemini
    key configured (this experiment's real environment), gemini still fails
    fast with zero network calls and the chain still falls through to
    Voyage -- which then recovers via its own retry, unaffected by any
    change to Gemini's key-rotation path."""
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "gemini_api_keys", None)
    monkeypatch.setattr(settings, "voyage_max_retries", 5)
    monkeypatch.setattr(settings, "embedding_dimension", 3)
    monkeypatch.setattr(settings, "use_local_models", False)

    fake = _FlakyThenOK(
        n_failures=1,
        exc_factory=lambda: voyageai.error.RateLimitError("reduced rate limit, 3 RPM"),
        vectors=[[0.4, 0.5, 0.6]],
    )
    monkeypatch.setattr(voyageai.Embedding, "acreate", fake, raising=True)

    embedder = Embedder(model="voyage-3-large", dimension=3)
    vectors = await embedder._embed_via_chain(["find the largest function"], "query")

    assert vectors == [[0.4, 0.5, 0.6]]
    assert fake.calls == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
