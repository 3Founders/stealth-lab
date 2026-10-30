"""The last stage of tool retrieval: a cross-encoder reads the request with each candidate and scores them together.

Why: BM25 and embeddings score a request and a tool separately; a cross-encoder sees both at once, which is where most
of a modern retrieval pipeline's precision comes from. On "find why the API returns 500 in production" Voyage's
rerank-2.5 put the Sentry server at 0.62 and the Avalara *returns* API at 0.22 -- the first-stage confusion that
matched on the word "returns" (2026-10-30).

TOOL_RERANK_MODEL ("off" skips reranking); DeepInfra (DEEPINFRA_KEY) first, Voyage (VOYAGE_API_KEY,
TOOL_VOYAGE_RERANK_MODEL default rerank-2.5) without it. Any failure: None, and the first-stage
order stands.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional, Sequence

TIMEOUT_S = 8.0


def voyage_model() -> Optional[str]:
    m = os.environ.get("TOOL_VOYAGE_RERANK_MODEL", "rerank-2.5").strip()
    return None if not m or m.lower() == "off" else m


def _key() -> Optional[str]:
    k = os.environ.get("VOYAGE_API_KEY")
    if k:
        return k
    try:
        from app.config import settings
        return settings.voyage_api_key or None
    except Exception:  # noqa: BLE001
        return None


async def rerank(query: str, docs: Sequence[str], *, client=None) -> Optional[list[float]]:
    """-> one relevance score per doc (the input's order), or None (off, no key, an error, too slow). DeepInfra's
    Qwen3-Reranker when its key is set (app/tool_routing/providers.py); else Voyage, which this account caps at
    10K tokens a minute -- fine for a test, not for traffic."""
    if os.environ.get("TOOL_RERANK_MODEL", "").lower() == "off" or not docs:
        return None
    from app.tool_routing.providers import deepinfra_key, deepinfra_rerank
    if client is None and deepinfra_key():
        return await deepinfra_rerank(query, docs)
    model = voyage_model()
    if not model:
        return None
    try:
        if client is None:
            key = _key()
            if not key:
                return None
            import voyageai
            client = voyageai.AsyncClient(api_key=key, max_retries=1)
        r = await asyncio.wait_for(client.rerank(query, list(docs), model=model, top_k=len(docs), truncation=True), TIMEOUT_S)
        scores = [0.0] * len(docs)
        for x in r.results:
            scores[x.index] = float(x.relevance_score)
        return scores
    except Exception:  # noqa: BLE001 -- the first-stage order stands
        return None
