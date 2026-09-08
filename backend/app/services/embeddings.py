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

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from app.config import settings

log = logging.getLogger(__name__)

InputType = Literal["document", "query"]


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
        missing_idx: list[int] = []
        missing_texts: list[str] = []
        for i, text in enumerate(texts):
            key = _cache_key(model_id, self.dimension, task_type, text)
            with _EMBED_CACHE_LOCK:
                cached = _EMBED_CACHE.get(key)
            if cached is not None:
                results[i] = cached
            else:
                missing_idx.append(i)
                missing_texts.append(text)

        if missing_texts:
            vectors = await self._embed_configured_provider(missing_texts, input_type)
            for i, text, vec in zip(missing_idx, missing_texts, vectors):
                results[i] = vec
                _cache_put(
                    _cache_key(model_id, self.dimension, task_type, text),
                    vec,
                )
            if len(missing_texts) < len(texts):
                log.info("embedding cache: %d/%d served without a provider call",
                         len(texts) - len(missing_texts), len(texts))

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
        try:
            if provider == "gemini":
                vectors = await self._embed_gemini(texts, input_type)
            elif provider == "voyage":
                vectors = await self._embed_voyage(texts, input_type)
            else:
                raise EmbeddingError(f"unknown embedding provider {provider!r}")
        except EmbeddingError:
            # Do not fall through to a provider with another embedding
            # space. The caller records a retryable job failure instead.
            raise
        self._check_dimension(vectors, self.embedding_model_id())
        return vectors

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
        from google import genai
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
            client = genai.Client(api_key=key)
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
        import voyageai

        client = voyageai.AsyncClient(api_key=settings.require("voyage_api_key"))
        try:
            result = await client.embed(
                list(texts), model=self.model, input_type=input_type
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Voyage embedding failed: {exc}") from exc

        vectors = result.embeddings
        self._check_dimension(vectors, self.model)
        return vectors

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
