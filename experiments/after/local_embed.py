"""
Local 1024-dim embedder (Ollama / mxbai-embed-large), cached.

Why not Voyage for Experiments 5 and 6: the free tier delivers ~10K
tokens/min, and the scaled corpus is ~1.5M characters. That is roughly six
hours of pure rate-limit waiting, repeated whenever a corpus definition
changes. mxbai-embed-large is 1024-dim -- the exact width of the
VECTOR(1024) schema column -- so it drops in without touching the schema,
runs locally at no cost, and can be re-run freely.

The tradeoff is stated rather than hidden: mxbai is a weaker embedder than
voyage-3-large, so absolute numbers here are NOT comparable to Hypothesis
A's. Every arm within Experiments 5 and 6 uses this same embedder, so the
comparisons that matter -- flat vs hierarchical, method vs method, and the
shape of the curve as the corpus grows -- remain valid. Where a
cross-experiment comparison is wanted, the 22-node baseline is re-measured
here under this embedder rather than borrowed from Hypothesis A.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openai import AsyncOpenAI

from app.config import settings
from app.services.embeddings import Embedder, EmbeddingError, InputType

_CACHE = Path(__file__).parent / ".cache" / "local_embeddings.json"


class LocalEmbedder(Embedder):
    MODEL = "mxbai-embed-large"

    def __init__(self, model: Optional[str] = None, concurrency: int = 8,
                 cache_path: Optional[Path] = None):
        super().__init__(model=model or self.MODEL, dimension=1024)
        self._client = AsyncOpenAI(api_key="ollama", base_url=settings.local_base_url)
        self._sem = asyncio.Semaphore(concurrency)
        self._cache_path = Path(cache_path or _CACHE)
        self._cache: dict[str, list[float]] = {}
        self.api_calls = 0
        self.cache_hits = 0
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._cache = {}

    def _key(self, text: str) -> str:
        # No input_type in the key: Ollama has no document/query distinction,
        # so keying on one would just duplicate identical vectors.
        return hashlib.sha256(f"{self.model}::{text}".encode()).hexdigest()

    def _flush(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache), encoding="utf-8")
        os.replace(tmp, self._cache_path)

    async def _one(self, text: str) -> tuple[str, list[float]]:
        async with self._sem:
            for attempt in range(4):
                try:
                    self.api_calls += 1
                    r = await asyncio.wait_for(
                        self._client.embeddings.create(model=self.model, input=text),
                        timeout=180.0,
                    )
                    return text, r.data[0].embedding
                except Exception as exc:  # noqa: BLE001
                    if attempt == 3:
                        raise EmbeddingError(f"local embedding failed: {exc}") from exc
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    async def embed(
        self, texts: Sequence[str], input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []
        missing = list(dict.fromkeys(t for t in texts if self._key(t) not in self._cache))
        self.cache_hits += len(texts) - len(missing)

        done = 0
        for i in range(0, len(missing), 256):
            batch = missing[i: i + 256]
            for text, vec in await asyncio.gather(*(self._one(t) for t in batch)):
                if len(vec) != self.dimension:
                    raise EmbeddingError(
                        f"{self.model} returned dim {len(vec)}, schema expects {self.dimension}"
                    )
                self._cache[self._key(text)] = vec
            done += len(batch)
            self._flush()
            if len(missing) > 256:
                print(f"      embedded {done}/{len(missing)}")
        if missing:
            self._flush()
        return [self._cache[self._key(t)] for t in texts]

    def stats(self) -> dict:
        return {"api_calls": self.api_calls, "cache_hits": self.cache_hits,
                "cache_size": len(self._cache)}


if __name__ == "__main__":
    async def _t():
        e = LocalEmbedder()
        v = await e.embed(["hello world", "a second document"])
        print(f"model={e.model} n={len(v)} dim={len(v[0])} stats={e.stats()}")
    asyncio.run(_t())
