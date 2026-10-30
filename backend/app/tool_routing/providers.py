"""The tool router's own embedder and reranker (DeepInfra: Qwen3-Embedding-8B and Qwen3-Reranker-8B by default).

Why its own, and not the backend's Embedder: the router's vectors are its own (the registry cards it embeds itself, keyed
by model id), so it can use the strongest open retrieval models without touching find_ways' stored vectors, which are
all gemini-embedding-2 and must stay in that space. Voyage (the first reranker) is capped at 3 requests and 10K tokens
a minute on this account: one rerank of 40 tools is ~12K tokens.

Qwen3-Embedding is trained with an instruction on the query side ("Instruct: ...\\nQuery: ..."); documents go as they
are. Vectors are asked for at TOOL_EMBED_DIM dimensions (Matryoshka: the leading dimensions of the full vector), so the
registry's ~35k cards stay a manageable file.

TOOL_EMBED_MODEL, TOOL_EMBED_DIM (default 1024), TOOL_RERANK_MODEL ("off": none), DEEPINFRA_KEY.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional, Sequence

import httpx

BASE = "https://api.deepinfra.com/v1"
QUERY_INSTRUCTION = "Given a task a developer or coding agent wants done, retrieve the tools or MCP servers that can do it"


def deepinfra_key() -> Optional[str]:
    k = os.environ.get("DEEPINFRA_KEY")
    if k:
        return k.strip()
    try:
        from app.config import settings
        return (getattr(settings, "deepinfra_key", None) or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


class ToolEmbedder:
    """embed_one / embed / embedding_model_id, the interface app/tool_routing/retrieve.py uses."""

    def __init__(self, model: Optional[str] = None, dim: Optional[int] = None, key: Optional[str] = None,
                 client: Optional[httpx.AsyncClient] = None):
        self.model = model or os.environ.get("TOOL_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
        self.dim = int(dim or os.environ.get("TOOL_EMBED_DIM", "1024"))
        self.key = key or deepinfra_key()
        self._client = client

    def embedding_model_id(self) -> str:
        return f"deepinfra:{self.model}@{self.dim}"

    def available(self) -> bool:
        return bool(self.key)

    async def _post(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.key:
            raise RuntimeError("DEEPINFRA_KEY is not set")
        client = self._client or httpx.AsyncClient(timeout=60)
        try:
            # a dropped connection, a DNS hiccup, a 429 or a 5xx is retried with back-off: one getaddrinfo failure
            # ended a 25-minute embedding run with nothing saved (2026-10-30)
            for attempt in range(5):
                try:
                    r = await client.post(f"{BASE}/openai/embeddings", headers={"authorization": f"Bearer {self.key}"},
                                          json={"model": self.model, "input": list(texts), "encoding_format": "float",
                                                "dimensions": self.dim})
                    if r.status_code == 429 or r.status_code >= 500:
                        raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                    r.raise_for_status()
                    data = sorted(r.json()["data"], key=lambda d: d["index"])
                    return [d["embedding"][: self.dim] for d in data]
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if attempt == 4 or (status is not None and status < 500 and status != 429):
                        raise
                    await asyncio.sleep(2 * 2 ** attempt)
            raise RuntimeError("unreachable")
        finally:
            if self._client is None:
                await client.aclose()

    async def embed_one(self, text: str, input_type: str = "query") -> list[float]:
        t = f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}" if input_type == "query" else text
        return (await self._post([t]))[0]

    async def embed(self, texts: Sequence[str], input_type: str = "document") -> list[list[float]]:
        if input_type == "query":
            texts = [f"Instruct: {QUERY_INSTRUCTION}\nQuery: {t}" for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            out.extend(await self._post(texts[i:i + 256]))
        return out


async def deepinfra_rerank(query: str, docs: Sequence[str], *, model: Optional[str] = None, key: Optional[str] = None,
                           client: Optional[httpx.AsyncClient] = None, timeout_s: float = 10.0) -> Optional[list[float]]:
    """One relevance score per doc (input order), or None (no key, an error)."""
    key = key or deepinfra_key()
    model = model or os.environ.get("TOOL_RERANK_MODEL", "Qwen/Qwen3-Reranker-8B")
    if not key or not docs or model.lower() == "off":
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        r = await client.post(f"{BASE}/inference/{model}", headers={"authorization": f"Bearer {key}"},
                              json={"queries": [query], "documents": list(docs),
                                    "instruction": QUERY_INSTRUCTION})
        r.raise_for_status()
        scores = r.json().get("scores")
        return [float(s) for s in scores] if isinstance(scores, list) and len(scores) == len(docs) else None
    except Exception:  # noqa: BLE001 -- the first-stage order stands
        return None
    finally:
        if own:
            await client.aclose()
