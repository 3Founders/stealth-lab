"""
Embedding generation (V1 item #3).

First code in this project that actually calls Voyage. The columns
(`VECTOR(1024)`) and HNSW indexes have existed since the first schema and
have been unused until now.

Two deliberate choices:

  - `input_type` matters. Voyage embeds documents and queries into
    slightly different spaces on purpose; using "document" for stored
    nodes and "query" for search text measurably improves retrieval over
    using one for both. Getting this backwards degrades results quietly,
    with no error.

  - Failure is not silent. If embedding fails during seeding, the node is
    still written with a NULL embedding rather than the whole onboarding
    transaction aborting -- but the caller is told. A graph that
    half-embedded without anyone noticing would produce silently
    degraded search forever.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from app import telemetry as _tel
from app.config import settings

log = logging.getLogger(__name__)

InputType = Literal["document", "query"]



# ADC credentials for the Vertex embedding endpoint, built once per process and refreshed only when
# expired. Both `google.auth.default()` and `credentials.refresh()` are synchronous network calls; doing
# them on every embed (as this module used to) blocked the event loop on every query.
_vertex_creds: Any = None
_vertex_creds_lock = threading.Lock()


def _vertex_credentials_sync() -> Any:
    global _vertex_creds
    import google.auth
    import google.auth.transport.requests

    with _vertex_creds_lock:
        if _vertex_creds is None:
            _vertex_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not _vertex_creds.valid:
            _vertex_creds.refresh(google.auth.transport.requests.Request())
        return _vertex_creds


async def _vertex_credentials() -> Any:
    return await asyncio.to_thread(_vertex_credentials_sync)


@dataclass(frozen=True)
class EmbeddingMetadata:
    """Identity of the vector space used for one persisted embedding.

    Similarity only has meaning inside one provider/model space.  This is
    returned with the vector so persistence callers never have to infer the
    provider from configuration after a fallback or deployment change.
    """

    provider: str
    model_id: str
    dimension: int
    input_type: InputType
    text_sha256: str

# ---------------------------------------------------------------------------
# Usage telemetry + cross-process TPM budget (see docs/usageapi.md).
# Every Gemini attempt appends one JSONL line so concurrent runs (tau2
# sweeps, backfills, ad-hoc probes) can be attributed and quota deaths can
# be explained after the fact instead of by archaeology. The bucket is a
# rolling-minute token budget shared across ALL processes via a small state
# file -- it converts would-be 429s into short waits.
# ---------------------------------------------------------------------------
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_USAGE_LOG = _LOG_DIR / "gemini_usage.jsonl"
_BUCKET_FILE = _LOG_DIR / "gemini_bucket.json"


def _caller_tag() -> str:
    return os.environ.get("CALLER_TAG", "untagged")


def _log_usage(key_idx: int, n_texts: int, est_tokens: int, ok: bool,
               latency_ms: int, err_class: str = "") -> None:
    if not settings.gemini_usage_log:
        return
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "key_idx": key_idx,
            "n_texts": n_texts,
            "est_tokens": est_tokens,
            "ok": ok,
            "latency_ms": latency_ms,
            "caller": _caller_tag(),
            "err_class": err_class,
        }
        with open(_USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 -- telemetry must never break embedding
        pass


def _bucket_read() -> dict:
    try:
        raw = _BUCKET_FILE.read_text(encoding="utf-8")
        state = json.loads(raw)
        if isinstance(state, dict) and "start" in state and "used" in state:
            return state
    except Exception:  # noqa: BLE001 -- absent/corrupt state resets the window
        pass
    return {"start": time.time(), "used": 0}


def _bucket_write(state: dict) -> None:
    _BUCKET_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BUCKET_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, _BUCKET_FILE)


async def _bucket_acquire(est_tokens: int) -> None:
    """Shared rolling-minute token budget across every process using this module.

    Read-modify-write via temp+replace is not perfectly atomic; collisions
    occasionally under-count, which only means an occasional real 429 --
    handled by key rotation and voyage fallback. Bounded total wait so this
    can never become its own hang."""
    budget = settings.embed_tpm_budget
    if budget <= 0 or est_tokens <= 0:
        return

    import asyncio

    deadline = time.time() + 120.0
    while True:
        state = _bucket_read()
        now = time.time()
        if now - float(state["start"]) >= 60.0:
            state = {"start": now, "used": 0}
        if int(state["used"]) + est_tokens <= budget:
            state["used"] = int(state["used"]) + est_tokens
            _bucket_write(state)
            return
        if time.time() > deadline:
            log.warning("embed TPM budget wait exceeded 120s -- proceeding unthrottled")
            return
        wait = max(0.5, min(60.0 - (now - float(state["start"])), 10.0))
        await asyncio.sleep(wait)

# Cross-call embedding cache, keyed by (model, dim, task_type, text-hash).
# tau2 runs simulations on ThreadPoolExecutor threads and each substrate
# call bridges through its own asyncio.run(), so this is touched from many
# threads -- every access goes through the lock. Concurrent agents search
# overlapping topics; without the cache each identical query re-spends
# quota from a single 30K-TPM free-tier key. Bounded FIFO eviction keeps
# memory at ~80MB worst case.
_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_ORDER: list[str] = []
_EMBED_CACHE_LOCK = threading.Lock()
_EMBED_CACHE_MAX = 20000


def _cache_key(model: str, dim: int, task_type: str, text: str) -> str:
    digest = hashlib.sha256(
        f"{model}|{dim}|{task_type}|".encode("utf-8") + text.encode("utf-8")
    ).hexdigest()
    return f"{model}:{dim}:{task_type}:{digest}"


def _cache_put(key: str, vector: list[float]) -> None:
    with _EMBED_CACHE_LOCK:
        if key not in _EMBED_CACHE:
            _EMBED_CACHE_ORDER.append(key)
            while len(_EMBED_CACHE_ORDER) > _EMBED_CACHE_MAX:
                _EMBED_CACHE.pop(_EMBED_CACHE_ORDER.pop(0), None)
        _EMBED_CACHE[key] = vector


class EmbeddingError(Exception):
    pass


# Provider clients, built once per configuration and reused for the process
# lifetime.
#
# Both of these used to be constructed inside the per-call loop, which meant
# a new underlying connection pool -- and therefore a fresh TCP + TLS
# handshake to Voyage or Google -- on every single embed() call, with no
# keep-alive reuse across calls.
#
# The cache key must cover EVERY constructor argument, not just the api key.
# That is not theoretical: VOYAGE_MAX_RETRIES is a setting, and a client
# cached under an older value keeps retrying the old number of times after
# the setting changes. The key is (api_key, max_retries) for exactly that
# reason, and a test that changes the setting gets a correctly-configured
# client rather than a stale one. The api key stays in the key because both
# loops rotate across keys on failure: sharing one client across keys would
# send one key's traffic on another key's connection and, for a rotation
# that exists to survive a per-key quota, defeat the point of rotating.
_voyage_clients: dict[tuple[str, int], Any] = {}
_gemini_clients: dict[str, Any] = {}
_provider_client_lock = threading.Lock()


def _voyage_client(api_key: str, max_retries: Optional[int] = None) -> Any:
    import voyageai

    if max_retries is None:
        max_retries = settings.voyage_max_retries
    key = (api_key, max_retries)
    with _provider_client_lock:
        client = _voyage_clients.get(key)
        if client is None:
            client = voyageai.AsyncClient(api_key=api_key, max_retries=max_retries)
            _voyage_clients[key] = client
        return client


def _gemini_client(api_key: str) -> Any:
    from google import genai

    with _provider_client_lock:
        client = _gemini_clients.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            _gemini_clients[api_key] = client
        return client


class _VertexBatcher:
    """One queue for every Vertex embedding request in this event loop.

    WHY: the quota is on REQUESTS per minute (5 published, ~17 sustained on a new project) and on input tokens per
    minute, not on texts. The ingestion path embeds one text per call (a novelty check and a goal per item, from
    several workers at once), so it spent a request per text and failed on 429s: 37 of 40 items in the first
    production SkillMD run. Here every text is queued; the flusher waits for the next request slot, and whatever has
    piled up meanwhile goes out as ONE request (up to `max_batch` texts and a token cap). Under load the wait is what
    creates the batch, so no timing window is needed. Texts are not reordered across `input_type`: a request carries
    one `task_type`.

    Limits, stated: pacing is per process (several worker processes share the project's quota and would each pace
    themselves), and the token count is an estimate (len/3, conservative for code), not the provider's tokenizer.
    """

    def __init__(self, send: Any, *, rpm: int, tpm: int, max_batch: int) -> None:
        self._send = send
        self._interval = 60.0 / max(1, rpm)
        self._tpm = max(1, tpm)
        self._max_request_tokens = max(1, self._tpm // 2)
        self._max_batch = max(1, max_batch)
        self._pending: list[tuple[str, str, int, "asyncio.Future[list[float]]"]] = []
        self._task: Optional["asyncio.Task[None]"] = None
        self._next_at = 0.0
        self._window: list[tuple[float, int]] = []          # (sent_at, estimated tokens) within the last 60 s
        self.requests_sent = 0
        self.texts_sent = 0

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 3)

    async def submit(self, text: str, input_type: str) -> list[float]:
        fut: "asyncio.Future[list[float]]" = asyncio.get_running_loop().create_future()
        self._pending.append((text, input_type, self._estimate_tokens(text), fut))
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())
        return await fut

    def _take_batch(self) -> list[tuple[str, str, int, "asyncio.Future[list[float]]"]]:
        input_type = self._pending[0][1]
        batch: list = []
        tokens = 0
        for item in self._pending:
            if item[1] != input_type or item[3].cancelled():
                continue
            if batch and (len(batch) >= self._max_batch or tokens + item[2] > self._max_request_tokens):
                break
            batch.append(item)
            tokens += item[2]
        for item in batch:
            self._pending.remove(item)
        return batch

    async def _wait_for_tokens(self, tokens: int) -> None:
        while True:
            now = time.monotonic()
            self._window = [(t, n) for t, n in self._window if now - t < 60.0]
            used = sum(n for _, n in self._window)
            if used + tokens <= self._tpm or not self._window:
                return
            await asyncio.sleep(max(0.05, 60.0 - (now - self._window[0][0])))

    async def _run(self) -> None:
        while self._pending:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)       # texts submitted while we sleep join the next request
            self._pending = [p for p in self._pending if not p[3].cancelled()]
            if not self._pending:
                return
            batch = self._take_batch()
            if not batch:
                continue
            tokens = sum(item[2] for item in batch)
            try:
                await self._wait_for_tokens(tokens)
                self._next_at = time.monotonic() + self._interval
                self._window.append((time.monotonic(), tokens))
                self.requests_sent += 1
                self.texts_sent += len(batch)
                vectors = await self._send([item[0] for item in batch], batch[0][1])
                if len(vectors) != len(batch):
                    raise EmbeddingError(f"Vertex returned {len(vectors)} embeddings for {len(batch)} texts")
            except asyncio.CancelledError:
                for item in batch:
                    if not item[3].done():
                        item[3].cancel()
                raise
            except Exception as exc:  # noqa: BLE001 -- every waiting caller gets the same failure
                for item in batch:
                    if not item[3].done():
                        item[3].set_exception(exc)
                continue
            for item, vector in zip(batch, vectors):
                if not item[3].done():
                    item[3].set_result(vector)


_VERTEX_BATCHERS: "weakref.WeakKeyDictionary[Any, dict[int, _VertexBatcher]]" = weakref.WeakKeyDictionary()


def _vertex_batcher(send: Any, dimension: int) -> _VertexBatcher:
    """The batcher for this event loop and vector dimension (a loop's futures cannot be shared with another loop)."""
    loop = asyncio.get_running_loop()
    per_loop = _VERTEX_BATCHERS.setdefault(loop, {})
    batcher = per_loop.get(dimension)
    if batcher is None:
        batcher = _VertexBatcher(
            send, rpm=settings.vertex_embed_rpm, tpm=settings.vertex_embed_tpm,
            max_batch=settings.vertex_embed_max_batch,
        )
        per_loop[dimension] = batcher
    return batcher


class Embedder:
    """Thin wrapper over Voyage. Batches, because per-node calls are wasteful."""

    def __init__(
        self,
        model: Optional[str] = None,
        dimension: Optional[int] = None,
        *,
        rate_limit_pool: Any = None,
        provider: Optional[str] = None,
        data_classification: Any = None,
        policy_pool: Any = None,
    ):
        self.model = model or settings.embedding_model
        self.dimension = dimension or settings.embedding_dimension
        # Ingestion workers pass their shared Postgres pool here. Ordinary
        # interactive retrieval keeps the lightweight local limiter.
        self._rate_limit_pool = rate_limit_pool
        # Phase 5 (LC-005 / INV-07): when the caller knows the
        # classification of what is being embedded and supplies a pool,
        # every EXTERNAL provider call is gated by ProviderPolicyService.
        # A private embedding never reaches a provider whose policy row
        # does not list its class. `local` (in-boundary) is never gated.
        self._data_classification = data_classification
        self._policy_pool = policy_pool or rate_limit_pool
        # Explicit one-off override of the configured provider chain, for a
        # bulk job that must pin a specific space (e.g. the canonical
        # re-embed backfill). Never a fallback -- exactly one provider.
        self._provider_override = provider.strip() if provider else None

    def _configured_provider(self) -> str:
        if self._provider_override:
            return self._provider_override
        if settings.use_local_models:
            return "local"
        providers = [
            provider.strip()
            for provider in settings.embedding_provider_chain.split(",")
            if provider.strip()
        ]
        if not providers:
            raise EmbeddingError("embedding_provider_chain is empty")
        # A fallback provider produces a different vector space. Choosing
        # one explicit provider is safer than silently mixing vectors that
        # pgvector can compare numerically but cannot compare semantically.
        return providers[0]

    def embedding_model_id(self) -> str:
        provider = self._configured_provider()
        if provider == "gemini":
            return f"gemini:{settings.gemini_embedding_model}"
        if provider == "vertex":
            return f"vertex:{settings.gemini_embedding_model}"
        if provider == "voyage":
            return f"voyage:{self.model}"
        if provider == "local":
            return f"local:{settings.local_embedding_model}"
        raise EmbeddingError(f"unknown embedding provider {provider!r}")

    @staticmethod
    def _text_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def embed_one_with_metadata(
        self, text: str, input_type: InputType = "document",
    ) -> tuple[list[float], EmbeddingMetadata]:
        vector = await self.embed_one(text, input_type=input_type)
        provider = self._configured_provider()
        return vector, EmbeddingMetadata(
            provider=provider,
            model_id=self.embedding_model_id(),
            dimension=self.dimension,
            input_type=input_type,
            text_sha256=self._text_sha256(text),
        )

    async def embed(
        self, texts: Sequence[str], input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []

        provider = self._configured_provider()
        model_id = self.embedding_model_id()

        if provider == "local":
            return await self._embed_local(texts)

        # Cache pass: serve whatever we already have, send only the misses
        # to the provider chain.
        task_type = "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
        results: dict[int, list[float]] = {}
        # Two fixes over the previous version of this loop, both of which
        # got worse as ingest volume grew:
        #
        # 1. The cache key hashes the WHOLE text, and it was computed twice
        #    per text -- once to look up, once to store. At the 8k-token
        #    chunk size that is 2x the SHA-256 work for nothing. Keyed once
        #    here and reused for the store.
        # 2. Misses were NOT deduplicated within a call, so a text repeated
        #    k times in one batch was sent to the provider k times. Ingest
        #    batches repeat text constantly (identical claim statements,
        #    identical boilerplate sections) and the batch API is
        #    rate-limited, so each repeat was a paid, rate-limited round
        #    trip for a vector already in hand. The experiment-path
        #    embedder in embed_cache.py has always deduped here; this
        #    closes the gap between the two.
        missing_pairs: list[tuple[int, str]] = []   # (index, text) -- first occurrence
        duplicate_pairs: list[tuple[int, str]] = [] # (index, text) -- later repeats
        missing_texts: list[str] = []               # unique, provider-bound order
        missing_keys: dict[str, str] = {}           # text -> cache key
        seen: set[str] = set()
        for i, text in enumerate(texts):
            key = _cache_key(model_id, self.dimension, task_type, text)
            with _EMBED_CACHE_LOCK:
                cached = _EMBED_CACHE.get(key)
            if cached is not None:
                results[i] = cached
            elif text in seen:
                duplicate_pairs.append((i, text))
            else:
                seen.add(text)
                missing_keys[text] = key
                missing_pairs.append((i, text))
                missing_texts.append(text)

        if missing_texts:
            vectors = await self._embed_configured_provider(missing_texts, input_type)
            if len(vectors) != len(missing_texts):
                # Previously this surfaced as a bare KeyError from the
                # return comprehension below, which reads like a cache bug.
                # A provider that answers with the wrong vector count is a
                # different problem and deserves to say so.
                raise EmbeddingError(
                    f"embedding provider returned {len(vectors)} vectors for "
                    f"{len(missing_texts)} inputs (model={model_id}, "
                    f"provider={self._configured_provider()})"
                )
            by_text = dict(zip(missing_texts, vectors))
            for text in missing_texts:
                _cache_put(missing_keys[text], by_text[text])
            for i, text in missing_pairs:
                results[i] = by_text[text]
            for i, text in duplicate_pairs:
                results[i] = by_text[text]
            served = len(texts) - len(missing_texts)
            if served:
                log.info("embedding cache: %d/%d served without a provider call",
                         served, len(texts))

        return [results[i] for i in range(len(texts))]

    async def _enforce_provider_policy(self, provider: str) -> None:
        """LC-005: gate an external embedding call on ProviderPolicyService.
        No-op when the caller supplied no classification/pool, or for the
        in-boundary `local` provider. Raises ProviderPolicyDenied otherwise."""
        if provider == "local" or self._data_classification is None or self._policy_pool is None:
            return
        from app.services.provider_policy import guard_send

        await guard_send(
            self._policy_pool,
            data_classification=self._data_classification,
            provider=provider,
            model=self.embedding_model_id(),
        )

    async def _embed_configured_provider(
        self, texts: Sequence[str], input_type: InputType
    ) -> list[list[float]]:
        provider = self._configured_provider()
        await self._enforce_provider_policy(provider)
        from app.services import ingest_budget
        await ingest_budget.guard("embedding")   # ingestion workers only; BudgetExceeded is not an EmbeddingError
        try:
            with _tel.span("embedding", kind="EMBEDDING", on_error=_tel.FailureCode.MODEL_ERROR,
                           embedding_model=self.embedding_model_id(), provider=provider,
                           input_count=len(texts)):
                if provider == "gemini":
                    vectors = await self._embed_gemini(texts, input_type)
                elif provider == "vertex":
                    vectors = await self._embed_vertex(texts, input_type)
                elif provider == "voyage":
                    vectors = await self._embed_voyage(texts, input_type)
                else:
                    raise EmbeddingError(f"unknown embedding provider {provider!r}")
        except EmbeddingError:
            # Do not fall through to a provider with another embedding
            # space. The caller records a retryable job failure instead.
            raise
        self._check_dimension(vectors, self.embedding_model_id())
        await ingest_budget.record_embedding(provider, self.embedding_model_id(), list(texts))
        return vectors

    async def _embed_vertex(
        self, texts: Sequence[str], input_type: InputType
    ) -> list[list[float]]:
        """
        Vertex AI's NATIVE embedding prediction endpoint, authenticated via
        OAuth2/ADC (no API key) -- the same auth mechanism already proven
        live for extraction and the semantic-judge chain (2026-09-22), and
        billed against real IAM-based project quota/credits rather than the
        shared free-tier API-key pool _embed_gemini draws on.

        Confirmed live 2026-09-23: the OpenAI-compatible surface Vertex
        exposes elsewhere in this codebase does NOT support
        gemini-embedding-001 ("OpenMaaS model ... not supported", 400) --
        this uses the native `:predict` endpoint instead, which does.
        `task_type`/`outputDimensionality` map straight onto this module's
        own input_type/dimension, same MRL truncation _embed_gemini already
        relies on.
        """
        if not settings.vertex_project:
            raise EmbeddingError("no Vertex project configured (VERTEX_PROJECT)")
        # Every text goes through the per-loop queue (`_VertexBatcher`): paced under the quota, coalesced into shared
        # requests, retried on 429/5xx. Callers see the same contract as before: one vector per text, in order.
        batcher = _vertex_batcher(self._vertex_predict, self.dimension)
        vectors = await asyncio.gather(*(batcher.submit(t, input_type) for t in texts))
        vectors = list(vectors)
        self._check_dimension(vectors, self.embedding_model_id())
        return vectors

    async def _vertex_predict(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        """ONE Vertex `:predict` request for `texts` (one `task_type`), retried on 429/5xx/transport errors with
        exponential backoff + jitter, honouring `Retry-After`. Raises EmbeddingError (its text keeps the status code,
        which `embed_cache` matches on) once the retries are spent."""
        import random

        import httpx

        task_type = "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
        url = (
            f"https://{settings.vertex_region}-aiplatform.googleapis.com/v1/projects/"
            f"{settings.vertex_project}/locations/{settings.vertex_region}/publishers/google/"
            f"models/{settings.gemini_embedding_model}:predict"
        )
        payload = {
            "instances": [{"content": t, "task_type": task_type} for t in texts],
            "parameters": {"outputDimensionality": self.dimension},
        }
        retries = max(0, settings.vertex_embed_max_retries)
        body: dict = {}
        for attempt in range(retries + 1):
            try:
                credentials = await _vertex_credentials()
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingError(f"Vertex ADC unavailable: {exc}") from exc
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(url, headers={"Authorization": f"Bearer {credentials.token}"}, json=payload)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    try:
                        wait = float(resp.headers.get("retry-after", ""))
                    except ValueError:
                        wait = min(60.0, 5.0 * (2 ** attempt))
                    wait *= 0.75 + 0.5 * random.random()
                    log.warning("Vertex embedding %s (attempt %d/%d, %d texts): retrying in %.0fs",
                                resp.status_code, attempt + 1, retries + 1, len(texts), wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                body = resp.json()
                break
            except httpx.TransportError as exc:
                if attempt < retries:
                    await asyncio.sleep(min(30.0, 2.0 * (2 ** attempt)))
                    continue
                raise EmbeddingError(f"Vertex embedding failed: {exc!r}") from exc
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingError(f"Vertex embedding failed: {exc}") from exc

        predictions = body.get("predictions") or []
        if len(predictions) != len(texts):
            raise EmbeddingError(f"Vertex returned {len(predictions)} embeddings for {len(texts)} texts")
        return [p["embeddings"]["values"] for p in predictions]

    async def _embed_gemini(
        self, texts: Sequence[str], input_type: InputType
    ) -> list[list[float]]:
        """
        Google gemini-embedding-001 via google-genai. Free tier is 100 RPM /
        30K TPM / 1K RPD per key, and task_type maps 1:1 onto this module's
        input_type distinction. MRL truncation to the schema's VECTOR(1024)
        is done server-side via output_dimensionality.

        Key rotation: GEMINI_API_KEYS (comma-separated) is tried in order
        whenever a call fails -- a per-key quota error on one key does not
        burn the next key's independent window. task_008 died permanently
        in phaseJ because four concurrent simulations drained one key's
        TPM; two keys double that ceiling.
        """
        from google.genai import types

        keys = [
            k.strip() for k in (settings.gemini_api_keys or "").split(",") if k.strip()
        ]
        if settings.gemini_api_key and settings.gemini_api_key not in keys:
            keys.append(settings.gemini_api_key)
        if not keys:
            raise EmbeddingError("no Gemini API key configured")

        task_type = "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
        est_tokens = max(1, sum(len(t) // 4 for t in texts) + 8)
        await self._acquire_gemini_budget(est_tokens)

        failures: list[str] = []
        for i, key in enumerate(keys):
            client = _gemini_client(key)
            t0 = time.perf_counter()
            try:
                result = await client.aio.models.embed_content(
                    model=settings.gemini_embedding_model,
                    contents=list(texts),
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.dimension,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.perf_counter() - t0) * 1000)
                err_class = ("429" if ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc))
                             else type(exc).__name__)
                failures.append(str(exc)[:150])
                _log_usage(i, len(texts), est_tokens, False, latency_ms, err_class)
                log.warning(
                    "gemini key %d/%d failed (%s) -- %s", i + 1, len(keys),
                    str(exc)[:120], "rotating" if i < len(keys) - 1 else "exhausted",
                )
                continue
            latency_ms = int((time.perf_counter() - t0) * 1000)
            embeddings = getattr(result, "embeddings", None) or []
            vectors = [list(e.values) for e in embeddings]
            if len(vectors) != len(texts):
                _log_usage(i, len(texts), est_tokens, False, latency_ms, "count_mismatch")
                raise EmbeddingError(
                    f"Gemini returned {len(vectors)} embeddings for {len(texts)} texts"
                )
            _log_usage(i, len(texts), est_tokens, True, latency_ms)
            return vectors

        raise EmbeddingError(
            f"Gemini embedding failed across {len(keys)} key(s): " + " | ".join(failures)
        )

    async def _acquire_gemini_budget(self, est_tokens: int) -> None:
        """Acquire a rolling-minute Gemini budget across distributed workers.

        The former file bucket works only for processes sharing one disk.
        When an ingestion worker supplies its Postgres pool, this uses a
        short row-locked transaction so every provider sees one aggregate
        budget. The lock is released before any model HTTP request.
        """
        if self._rate_limit_pool is None:
            await _bucket_acquire(est_tokens)
            return

        budget = settings.embed_tpm_budget
        if budget <= 0 or est_tokens <= 0:
            return

        import asyncio

        scope = self.embedding_model_id()
        while True:
            wait_seconds = 0.0
            async with self._rate_limit_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO embedding_rate_windows "
                        "(scope, window_started_at, tokens_used) "
                        "VALUES ($1, now(), 0) ON CONFLICT (scope) DO NOTHING",
                        scope,
                    )
                    row = await conn.fetchrow(
                        "SELECT window_started_at, tokens_used FROM embedding_rate_windows "
                        "WHERE scope=$1 FOR UPDATE",
                        scope,
                    )
                    elapsed = await conn.fetchval(
                        "SELECT EXTRACT(EPOCH FROM now() - $1::timestamptz)",
                        row["window_started_at"],
                    )
                    if elapsed >= 60:
                        await conn.execute(
                            "UPDATE embedding_rate_windows "
                            "SET window_started_at=now(), tokens_used=$2, updated_at=now() "
                            "WHERE scope=$1",
                            scope, est_tokens,
                        )
                        return
                    if int(row["tokens_used"]) + est_tokens <= budget:
                        await conn.execute(
                            "UPDATE embedding_rate_windows "
                            "SET tokens_used=tokens_used+$2, updated_at=now() WHERE scope=$1",
                            scope, est_tokens,
                        )
                        return
                    wait_seconds = max(0.5, min(60.0 - float(elapsed), 10.0))
            await asyncio.sleep(wait_seconds)

    async def _embed_local(self, texts: Sequence[str]) -> list[list[float]]:
        """
        Any OpenAI-compatible embedding endpoint (Ollama, LM Studio).

        No `input_type` distinction: that's a Voyage-specific feature, and
        local models generally embed queries and documents into one space.
        Slightly worse retrieval, but not a correctness problem.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key="not-needed-for-local", base_url=settings.local_base_url)
        try:
            result = await client.embeddings.create(
                model=settings.local_embedding_model, input=list(texts)
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"local embedding failed: {exc}") from exc

        vectors = [item.embedding for item in result.data]
        self._check_dimension(vectors, settings.local_embedding_model)
        return vectors

    async def _embed_voyage(
        self, texts: Sequence[str], input_type: InputType
    ) -> list[list[float]]:
        # ROOT CAUSE of the observed T1-v3/B_default provider crash: the SDK's
        # own AsyncClient ships a real tenacity retry/backoff controller
        # (exponential + jitter, retry_if_exception_type restricted to
        # RateLimitError | ServiceUnavailableError | Timeout) but it is inert
        # at the SDK's own max_retries=0 default -- a single 429 (observed:
        # the reduced-tier "3 RPM / 10K TPM" billing throttle) then raises on
        # its first and only attempt. Passing a bounded max_retries here is
        # the entire fix: it turns on backoff the SDK already implements and
        # already reviewed, for exactly the transient-only case the SDK's own
        # retry predicate selects -- an auth/malformed-request failure still
        # raises immediately, exactly as before.
        #
        # Key rotation (2026-09-23, same pattern as _embed_gemini): VOYAGE_API_KEYS
        # (comma-separated) is tried in order whenever a call fails -- a per-key
        # rate/quota error on one key does not burn the next key's independent
        # window. Single-key by default (VOYAGE_API_KEY alone) is unaffected;
        # this only activates once a second key is actually configured.
        keys = [
            k.strip() for k in (getattr(settings, "voyage_api_keys", None) or "").split(",") if k.strip()
        ]
        primary = settings.voyage_api_key
        if primary and primary not in keys:
            keys.insert(0, primary)
        if not keys:
            raise EmbeddingError("no Voyage API key configured")

        failures: list[str] = []
        for i, key in enumerate(keys):
            client = _voyage_client(key)
            try:
                result = await client.embed(
                    list(texts), model=self.model, input_type=input_type
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc)[:150])
                log.warning(
                    "voyage key %d/%d failed (%s) -- %s", i + 1, len(keys),
                    str(exc)[:120], "rotating" if i < len(keys) - 1 else "exhausted",
                )
                continue
            vectors = result.embeddings
            self._check_dimension(vectors, self.model)
            return vectors

        raise EmbeddingError(f"Voyage embedding failed across {len(keys)} key(s): " + " | ".join(failures))

    def _check_dimension(self, vectors: list[list[float]], model_name: str) -> None:
        """
        A dimension mismatch against the VECTOR(n) column fails at insert
        time with a far less obvious error, so catch it at the source.
        """
        if vectors and len(vectors[0]) != self.dimension:
            raise EmbeddingError(
                f"model {model_name} returned dimension {len(vectors[0])}, but the "
                f"schema expects {self.dimension}. Either pick a model with matching "
                f"dimension, or alter the VECTOR(n) column and re-embed the entire "
                f"corpus -- mixed dimensions in one column are not possible."
            )

    async def embed_one(self, text: str, input_type: InputType = "document") -> list[float]:
        vectors = await self.embed([text], input_type=input_type)
        if not vectors:
            raise EmbeddingError("no embedding returned")
        return vectors[0]

    async def embed_batched(
        self,
        texts: Sequence[str],
        input_type: InputType = "document",
        max_tokens_per_call: int = 8000,
        seconds_between_calls: float = 65.0,
        max_retries_per_call: int = 3,
    ) -> list[list[float]]:
        """
        For bulk jobs (backfills, evaluation runs) large enough to trip
        Voyage's free-tier TPM limit even as a SINGLE request -- one call
        with enough text in it can exceed 10K tokens/minute on its own,
        RPM isn't the binding constraint once individual documents get
        long (e.g. AFTER's task instructions). Chunks by an estimated
        token budget, then paces calls in real time rather than firing
        them back to back.

        Token estimate is `len(text) // 4` -- the standard rough
        approximation for English text, not an exact tokenizer count.
        `max_tokens_per_call` is set conservatively below the 10K/min
        limit for that reason (imprecise estimate + retry safety
        margin), not because 8000 is itself a meaningful number.

        `seconds_between_calls=65` is deliberately over 60: the TPM
        window is a rolling minute, not calls-since-start, so spacing
        by slightly more than a minute guarantees the previous call's
        tokens have fully aged out of the window before the next one
        counts against it -- not just "usually enough".

        Still retries with real backoff on an actual RateLimitError
        (not just the estimated pacing above) since the token estimate
        is approximate, not exact -- pacing reduces how often the limit
        gets hit, it doesn't guarantee it never does.
        """
        if not texts:
            return []

        def estimate_tokens(t: str) -> int:
            return max(1, len(t) // 4)

        batches: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0
        for text in texts:
            t = estimate_tokens(text)
            if current and current_tokens + t > max_tokens_per_call:
                batches.append(current)
                current, current_tokens = [], 0
            current.append(text)
            current_tokens += t
        if current:
            batches.append(current)

        log.info(
            "embed_batched: %d texts split into %d call(s), ~%ds apart",
            len(texts), len(batches), int(seconds_between_calls),
        )

        import asyncio

        all_vectors: list[list[float]] = []
        for i, batch in enumerate(batches):
            for attempt in range(max_retries_per_call):
                try:
                    vectors = await self.embed(batch, input_type=input_type)
                    all_vectors.extend(vectors)
                    break
                except EmbeddingError as exc:
                    if "RateLimitError" not in str(type(exc.__cause__).__name__) and "rate limit" not in str(exc).lower():
                        raise  # a real failure, not a rate limit -- don't retry blindly
                    if attempt == max_retries_per_call - 1:
                        raise
                    wait = seconds_between_calls * (attempt + 1)
                    log.warning("rate limited on batch %d/%d, retry %d/%d after %ds",
                                i + 1, len(batches), attempt + 1, max_retries_per_call, int(wait))
                    await asyncio.sleep(wait)

            if i < len(batches) - 1:
                await asyncio.sleep(seconds_between_calls)

        return all_vectors


def to_pgvector(vector: Sequence[float]) -> str:
    """
    pgvector's text input format. asyncpg has no native codec for the
    vector type, so it goes over the wire as a string and gets cast in SQL.
    """
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def node_text(name: str, description: Optional[str] = None) -> str:
    """
    What actually gets embedded for a node.

    Name plus description, because a bare name ("Extract fields") carries
    much less signal than the same name with its purpose attached.
    """
    return f"{name}\n{description}" if description else name
