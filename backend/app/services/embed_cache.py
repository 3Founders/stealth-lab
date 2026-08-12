"""
Throttled, disk-cached embedder that drops into every existing call site.

Subclasses app.services.embeddings.Embedder and overrides embed(), so
HybridRetriever / hierarchical_search / batch_hierarchical_search /
resolve_subtask_reuse all accept it unchanged -- they already take an
`embedder` argument and only ever call .embed() / .embed_one().

Three things the production Embedder does not do, each of which sinks a
long experiment run:

  - CACHE. The same skill document is embedded once per script, not once
    per run. Re-running an experiment after a crash costs nothing.
  - TOKEN-BUDGETED BATCHING. Voyage's free tier caps tokens/minute, and
    one 129-document call blows it in a single request.
  - BACKOFF. On a rate-limit error the production embedder raises, and
    every caller in this codebase catches that and silently degrades to
    lexical matching -- producing a plausible-looking run that measures
    the wrong mechanism entirely.

Same design as experiments/swebench_pro/knowledge.py's VoyageEmbedder,
which solved this once already; this version conforms to the Embedder
interface so it can be injected into production code paths.

HISTORY: this file lived at experiments/after/embed_cache.py until that
directory was deleted wholesale (commit ed6f426), which left five
importers -- graph_ingest, run_graph_experiment, run_graph_instance,
run_symbolic_instance, compare_embeddings -- dead at import. It is
restored here rather than there because it subclasses a backend service
and is injected into backend read paths; experiments/after/ was removed
on purpose and should stay removed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Sequence

from app.services.embeddings import Embedder, EmbeddingError, InputType

log = logging.getLogger(__name__)

# Resolved against the repo root, NOT against __file__'s own directory.
# The original default was `Path(__file__).parent / ".cache"`, which under
# the old location pointed inside experiments/after/ and under this one
# would point at backend/app/services/.cache/ -- neither is where the real
# cache lives. Getting this wrong is silent and expensive rather than
# loud: every lookup misses, every run re-embeds from scratch at
# MIN_REQUEST_INTERVAL seconds per request, and nothing errors.
# run_graph_experiment.py constructs this class with no cache_path at all,
# so this default is the only thing pointing it at the real 1173-entry
# cache.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_PATH = _REPO_ROOT / "experiments" / "swebench_pro" / ".cache_joint" / "embeddings.json"


class CachedEmbedder(Embedder):
    #  Voyage free tier: 3 requests/min, 10K tokens/min. Batches are sized
    #  by estimated tokens rather than count, and spaced to stay under the
    #  request ceiling. Both are overridable for a paid key.
    MAX_BATCH_TOKENS = 7000
    MIN_REQUEST_INTERVAL = 21.0

    def __init__(
        self,
        model: Optional[str] = None,
        dimension: Optional[int] = None,
        cache_path: Optional[Path] = None,
        min_interval: Optional[float] = None,
    ):
        super().__init__(model=model, dimension=dimension)
        self._cache_path = Path(cache_path or _CACHE_PATH)
        self._cache: dict[str, list[float]] = {}
        self._last_request = 0.0
        self._dirty = 0
        self.api_calls = 0
        self.cache_hits = 0
        if min_interval is not None:
            self.MIN_REQUEST_INTERVAL = min_interval
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
                log.info("embedding cache: %d entries loaded", len(self._cache))
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read embedding cache (%s); starting empty", exc)

    # -- cache plumbing ------------------------------------------------

    def _key(self, text: str, input_type: str) -> str:
        return hashlib.sha256(f"{self.model}::{input_type}::{text}".encode()).hexdigest()

    def _flush(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache), encoding="utf-8")
        os.replace(tmp, self._cache_path)  # atomic: a crash mid-write must
                                            # not corrupt an expensive cache
        self._dirty = 0

    @staticmethod
    def _est_tokens(text: str) -> int:
        return max(1, len(text) // 4)  # ~4 chars/token, same heuristic as
                                        # app/services/governance.py

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

    # -- the one overridden method -------------------------------------

    async def embed(
        self, texts: Sequence[str], input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []

        missing = list(dict.fromkeys(
            t for t in texts if self._key(t, input_type) not in self._cache
        ))
        self.cache_hits += len(texts) - len(missing)

        for batch in self._batches(missing):
            vectors = await self._call_with_backoff(batch, input_type)
            for text, vec in zip(batch, vectors):
                self._cache[self._key(text, input_type)] = vec
            self._dirty += len(batch)
            if self._dirty >= 16:
                self._flush()
        if self._dirty:
            self._flush()

        return [self._cache[self._key(t, input_type)] for t in texts]

    async def _call_with_backoff(
        self, batch: list[str], input_type: InputType
    ) -> list[list[float]]:
        for attempt in range(6):
            wait = self.MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                self._last_request = time.monotonic()
                self.api_calls += 1
                return await super().embed(batch, input_type=input_type)
            except EmbeddingError as exc:
                msg = str(exc).lower()
                retryable = any(s in msg for s in ("rate limit", "429", "timeout", "too many"))
                if not retryable or attempt == 5:
                    raise
                backoff = self.MIN_REQUEST_INTERVAL * (2 ** attempt)
                log.warning(
                    "embedding rate-limited (attempt %d), backing off %.0fs", attempt + 1, backoff
                )
                await asyncio.sleep(backoff)
        raise RuntimeError("unreachable")

    def stats(self) -> dict:
        return {
            "api_calls": self.api_calls,
            "cache_hits": self.cache_hits,
            "cache_size": len(self._cache),
        }
