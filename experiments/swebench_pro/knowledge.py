"""
The memory under test: what this repo already learned from its own history.

A knowledge node here is one previously-fixed ansible issue reduced to what
survives as reusable experience -- the issue title, the problem statement,
the files that ended up changing, and the functions inside them. That is the
thing a person who has worked on a codebase for two years actually carries:
not the diffs, but where this kind of bug lives.

WHAT IS DELIBERATELY NOT STORED

No gold patch text, ever. If the store contained prior diffs, retrieval
could hand an agent a working fix for a near-duplicate issue and the
accuracy number would measure near-duplicate lookup rather than transfer.
Storing only localization keeps the agent doing the reasoning and the memory
doing the pointing.

RETRIEVAL is a port of app/services/retrieval.py's HybridRetriever: same
Reciprocal Rank Fusion, same RRF_K=60, same argument for using rank fusion
rather than a weighted score sum (cosine similarity and lexical rank are on
incomparable scales, so any weighted sum smuggles in an arbitrary
normalization that drifts as either distribution moves).

It is a port and not a call into that class, which matters and is stated
plainly: HybridRetriever needs a live Postgres with pgvector and the full
ontology schema, and this pilot runs standalone. The fusion arithmetic is
identical; the storage is not. A result here is evidence about the retrieval
idea, not a test of that deployment.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

RRF_K = 60  # same constant, same reason, as app/services/retrieval.py

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = {
    "the", "a", "an", "is", "are", "be", "to", "of", "and", "or", "in", "on",
    "for", "with", "that", "this", "it", "as", "at", "by", "from", "not",
    "should", "when", "if", "but", "was", "were", "has", "have", "had", "we",
    "can", "will", "would", "there", "which", "these", "those", "its",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]


@dataclass
class KnowledgeNode:
    instance_id: str
    title: str
    problem_statement: str
    files_touched: list[str]
    symbols_touched: list[str]
    commit_date: str
    embedding: Optional[list[float]] = None

    def text_for_embedding(self) -> str:
        return f"{self.title}\n\n{self.problem_statement[:1500]}"

    def render(self) -> str:
        """One retrieved precedent as the agent sees it. Files first: it is
        the part that actually saves work, and burying it under prose would
        waste the tokens this arm is supposed to save."""
        files = ", ".join(self.files_touched[:6]) or "(none recorded)"
        syms = ", ".join(self.symbols_touched[:8])
        line = (f'- [{self.commit_date[:10]}] "{self.title}"\n'
                f"    files changed: {files}")
        if syms:
            line += f"\n    functions/classes: {syms}"
        return line


class VoyageEmbedder:
    """
    Thin wrapper on the same model app/services/embeddings.py uses
    (voyage-3-large, 1024-dim), with an on-disk cache so re-running the
    experiment does not re-pay for embeddings that cannot have changed.

    Throttled because this account is on Voyage's free tier: 3 requests/min
    and 10K tokens/min. Embedding the 76-issue corpus in one batch trips
    that immediately. Batches are therefore sized by estimated tokens rather
    than by count, and spaced to stay under the request ceiling.

    The cache is what makes this a non-issue in practice: the corpus is
    embedded once, and every later instance pays only for its own query.
    """

    MAX_BATCH_TOKENS = 7000     # under the 10K/min ceiling with headroom
    MIN_REQUEST_INTERVAL = 21.0  # 3 RPM

    def __init__(self, cache_path: str, model: str = "voyage-3-large"):
        import voyageai

        self._client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
        self._model = model
        self._cache_path = cache_path
        self._cache: dict[str, list[float]] = {}
        self._last_request = 0.0
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                self._cache = json.load(f)

    @staticmethod
    def _est_tokens(text: str) -> int:
        return max(1, len(text) // 4)

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

    def _throttled_embed(self, batch: list[str], input_type: str):
        import time

        for attempt in range(6):
            wait = self.MIN_REQUEST_INTERVAL - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_request = time.time()
                return self._client.embed(batch, model=self._model, input_type=input_type)
            except Exception as exc:  # noqa: BLE001
                if "rate limit" not in str(exc).lower() or attempt == 5:
                    raise
                # Exponential backoff on top of the fixed spacing: a 429
                # means the estimate was optimistic, not that the request
                # was malformed.
                time.sleep(self.MIN_REQUEST_INTERVAL * (2 ** attempt))
        raise RuntimeError("unreachable")

    def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        # dict.fromkeys dedupes while preserving order: the same text can
        # legitimately appear twice and should be paid for once.
        missing = list(dict.fromkeys(
            t for t in texts if self._key(t, input_type) not in self._cache))
        for batch in self._batches(missing):
            resp = self._throttled_embed(batch, input_type)
            for text, vec in zip(batch, resp.embeddings):
                self._cache[self._key(text, input_type)] = vec
            self._flush()
        return [self._cache[self._key(t, input_type)] for t in texts]

    def _key(self, text: str, input_type: str) -> str:
        import hashlib

        return hashlib.sha256(f"{input_type}::{text}".encode()).hexdigest()

    def _flush(self) -> None:
        os.makedirs(os.path.dirname(self._cache_path) or ".", exist_ok=True)
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class KnowledgeStore:
    def __init__(self, nodes: list[KnowledgeNode], embedder: VoyageEmbedder):
        self._nodes = nodes
        self._embedder = embedder
        self._df = Counter()
        for n in nodes:
            for tok in set(tokenize(n.text_for_embedding())):
                self._df[tok] += 1
        self._n = max(1, len(nodes))

    @property
    def nodes(self) -> list[KnowledgeNode]:
        return self._nodes

    def build_embeddings(self) -> None:
        texts = [n.text_for_embedding() for n in self._nodes]
        vecs = self._embedder.embed(texts, input_type="document")
        for node, vec in zip(self._nodes, vecs):
            node.embedding = vec

    def _lexical_rank(self, query: str, limit: int) -> list[tuple[int, int]]:
        """BM25-style idf-weighted overlap. Present for the same reason
        retrieval.py keeps a lexical leg: embeddings match on meaning but
        miss exact identifiers, and an issue naming `GalaxyCLI` or a specific
        module by name is better served by the literal token."""
        q = set(tokenize(query))
        scored = []
        for i, node in enumerate(self._nodes):
            toks = set(tokenize(node.text_for_embedding()))
            shared = q & toks
            if not shared:
                continue
            score = sum(math.log(1 + self._n / (1 + self._df[t])) for t in shared)
            scored.append((score, i))
        scored.sort(reverse=True)
        return [(idx, rank) for rank, (_, idx) in enumerate(scored[:limit])]

    def _vector_rank(self, query: str, limit: int) -> list[tuple[int, int]]:
        qv = self._embedder.embed([query], input_type="query")[0]
        sims = [(cosine(qv, n.embedding), i)
                for i, n in enumerate(self._nodes) if n.embedding]
        sims.sort(reverse=True)
        return [(idx, rank) for rank, (_, idx) in enumerate(sims[:limit])]

    def retrieve(self, query: str, top_k: int = 5) -> list[KnowledgeNode]:
        fused: dict[int, float] = {}
        for hits in (self._vector_rank(query, top_k * 3),
                     self._lexical_rank(query, top_k * 3)):
            for idx, rank in hits:
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [self._nodes[i] for i, _ in ranked]


def build_store(corpus: list[dict], before_date: str, embedder: VoyageEmbedder) -> KnowledgeStore:
    """
    Only instances strictly earlier than `before_date`.

    This filter is the difference between memory and leakage, so it lives
    here rather than in the caller where it could be forgotten for one arm.
    """
    nodes = [
        KnowledgeNode(
            instance_id=r["instance_id"],
            title=r["title"],
            problem_statement=r["problem_statement"],
            files_touched=r["files_touched"],
            symbols_touched=r["symbols_touched"],
            commit_date=r["commit_date"],
        )
        for r in corpus
        if r["commit_date"] < before_date
    ]
    return KnowledgeStore(nodes, embedder)


def render_context(nodes: list[KnowledgeNode]) -> str:
    if not nodes:
        return ""
    body = "\n".join(n.render() for n in nodes)
    return (
        "PRIOR FIXES IN THIS REPOSITORY (retrieved from earlier issues; the "
        "diffs themselves are not available, only where the work landed). "
        "Treat these as hints about where to look, not as answers -- the "
        "current issue may live somewhere else entirely:\n\n"
        f"{body}\n"
    )
