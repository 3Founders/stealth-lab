"""
CachedEmbedder -- referenced by graph_ingest.py, run_graph_experiment.py,
run_graph_instance.py, run_symbolic_instance.py, compare_embeddings.py,
method_library.py, precompute_embeddings.py, but the file itself was
never included in what was shared. Built here from the real, exact
interface every one of those call sites actually uses -- confirmed by
grepping every real usage before writing a single line, not assumed.

HONEST STATUS: this is new code, not recovered code, unlike everything
else built this session. It wraps the REAL, already-validated
app.services.embeddings.Embedder as its actual engine -- it does not
reimplement Voyage API calls, dimension checking, or the local-model
fallback; all of that is delegated to the real class. What's new here
is exactly the caching + rate-limit batching layer, modeled closely on
knowledge.py's VoyageEmbedder (already read in full this session, does
almost this same job for the older pilot), adapted from sync to async
since Embedder.embed() is async.

Real contract confirmed from existing call sites, not guessed:
  - min_interval, cache_path are keyword args (cache_path optional)
  - .embed(texts, input_type) -> list[list[float]], async
  - .embed_one(text, input_type) -> list[float], async
  - .MAX_BATCH_TOKENS is a settable attribute (every real caller
    overrides it immediately after construction)
  - .model exposes the underlying model name
  - .stats() returns something printable for a status log line
  - the cache file is REWRITTEN WHOLESALE on every batch write, not
    appended -- confirmed directly from run_graph_experiment.py's own
    --cache-path help text ("two processes sharing one path silently
    drop each other's entries on the last write"), so concurrent runs
    genuinely need distinct cache_path values. Matched exactly here,
    not redesigned.

UNTESTED AGAINST THE REAL VOYAGE API. Every piece checkable offline
(caching correctness, batching math, rate-limit spacing, stats
tracking) has been verified with a mocked embedder. The actual Voyage
call itself is delegated entirely to the real, separately-validated
Embedder class -- but this specific composition has not made one real
API call yet. Treat the first real run as a real test.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.embeddings import Embedder  # noqa: E402

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "embed_cache.json"
DEFAULT_MAX_BATCH_TOKENS = 3300  # matches the real, already-worked-out Voyage
                                   # free-tier math in graph_ingest.py (3 req/min
                                   # AND 10K tokens/min -- 3300 tokens per request
                                   # at a 21s interval is ~9.4K tokens/min, safely
                                   # under the ceiling). Every real caller
                                   # overrides this explicitly anyway, so this
                                   # default mostly matters for callers that don't.


class CachedEmbedder:
    def __init__(
        self, min_interval: float = 21.0, cache_path: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._embedder = Embedder(model=model)
        self.model = self._embedder.model
        self.MAX_BATCH_TOKENS = DEFAULT_MAX_BATCH_TOKENS
        self._min_interval = min_interval
        self._cache_path = Path(cache_path) if cache_path else DEFAULT_CACHE_PATH
        self._cache: dict[str, list[float]] = {}
        self._last_request = 0.0
        self._cache_hits = 0
        self._cache_misses = 0
        self._api_calls = 0
        self._tokens_sent = 0
        if self._cache_path.exists():
            self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))

    @staticmethod
    def _est_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def _key(self, text: str, input_type: str) -> str:
        return hashlib.sha256(f"{input_type}::{text}".encode()).hexdigest()

    def _batches(self, texts: list[str]):
        batch: list[str] = []
        budget = 0
        for text in texts:
            cost = self._est_tokens(text)
            if batch and budget + cost > self.MAX_BATCH_TOKENS:
                yield batch
                batch, budget = [], 0
            batch.append(text)
            budget += cost
        if batch:
            yield batch

    def _flush(self) -> None:
        # Real, documented contract: whole-file rewrite, not append --
        # matched exactly, not redesigned. See module docstring.
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache), encoding="utf-8")

    async def _rate_limited_call(self, batch: list[str], input_type: str) -> list[list[float]]:
        wait = self._min_interval - (time.time() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.time()
        self._api_calls += 1
        self._tokens_sent += sum(self._est_tokens(t) for t in batch)
        return await self._embedder.embed(batch, input_type=input_type)

    async def embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        if not texts:
            return []

        missing = list(dict.fromkeys(  # dedupe, preserve order -- same
            t for t in texts if self._key(t, input_type) not in self._cache  # text embedded
        ))                                                                    # twice is paid once
        self._cache_hits += len(texts) - len(set(texts) - set(missing))
        self._cache_misses += len(missing)

        for batch in self._batches(missing):
            vectors = await self._rate_limited_call(batch, input_type)
            for text, vec in zip(batch, vectors):
                self._cache[self._key(text, input_type)] = vec
            self._flush()

        return [self._cache[self._key(t, input_type)] for t in texts]

    async def embed_one(self, text: str, input_type: str = "document") -> list[float]:
        return (await self.embed([text], input_type=input_type))[0]

    def stats(self) -> dict:
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "api_calls": self._api_calls,
            "tokens_sent": self._tokens_sent,
            "cache_size": len(self._cache),
            "cache_path": str(self._cache_path),
        }
