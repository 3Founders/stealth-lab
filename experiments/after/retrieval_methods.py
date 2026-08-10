"""
Retrieval methods, implemented here rather than imported (Experiment 6).

Each is a published technique that costs nothing to run: no paid reranker
API, no hosted retrieval service. Written out so the experiment measures
the method rather than a vendor's implementation of it, and so the
parameters are visible and auditable.

  Okapi BM25        Robertson & Walker. The codebase's current "lexical"
                    path is Jaccard set overlap (reuse_detection.py:80),
                    which has no term saturation and no length
                    normalisation -- it is not BM25 and should not be read
                    as a lexical baseline.
  RRF               Cormack et al. Rank fusion, already the fusion rule in
                    retrieval.py; reused here with a real BM25 leg.
  MMR               Carbonell & Goldstein. Relevance/diversity trade-off.
  HyDE              Gao et al. Embed a hypothetical ANSWER rather than the
                    question, closing the query/document asymmetry.
  Cross-encoder     Joint query-document scoring; the standard reranking
                    step, run locally on a small MS MARCO model.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = {
    "the", "a", "an", "is", "are", "be", "to", "of", "and", "or", "in", "on",
    "for", "with", "that", "this", "it", "as", "at", "by", "from", "not",
    "should", "when", "if", "but", "was", "were", "has", "have", "had", "we",
    "can", "will", "would", "there", "which", "these", "those", "its", "you",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class BM25:
    """
    Okapi BM25.

        score(q,d) = sum_t idf(t) * f(t,d)*(k1+1) / (f(t,d) + k1*(1-b+b*|d|/avgdl))

    k1 controls term-frequency saturation, b the strength of length
    normalisation. Defaults k1=1.5, b=0.75 are the standard operating
    point. idf uses the Robertson-Sparck-Jones form with the +0.5
    smoothing, floored at zero so a term appearing in more than half the
    corpus cannot contribute negative score -- without that floor, common
    terms actively penalise documents that contain them.
    """

    def __init__(self, corpus: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [tokenize(d) for d in corpus]
        self.N = len(self.docs)
        self.dl = np.asarray([len(d) for d in self.docs], dtype=float)
        self.avgdl = float(self.dl.mean()) if self.N else 0.0

        # Inverted index: term -> (doc ids, term frequencies). Scoring then
        # touches only the documents that actually contain a query term,
        # instead of every document for every term. At 731 documents with
        # ~200-term queries the dense form is ~10^8 Python operations per
        # sweep; this is ~10^5.
        postings: dict[str, list[tuple[int, int]]] = {}
        for i, d in enumerate(self.docs):
            for t, f in Counter(d).items():
                postings.setdefault(t, []).append((i, f))
        self.postings = {
            t: (np.fromiter((p[0] for p in v), dtype=np.int64, count=len(v)),
                np.fromiter((p[1] for p in v), dtype=np.float64, count=len(v)))
            for t, v in postings.items()
        }
        self.idf = {
            t: max(0.0, math.log((self.N - len(v) + 0.5) / (len(v) + 0.5) + 1.0))
            for t, v in postings.items()
        }
        # Precomputed per-document half of the denominator.
        self._norm = self.k1 * (1 - self.b + self.b * self.dl / max(self.avgdl, 1e-12))

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(self.N, dtype=float)
        for t in tokenize(query):
            entry = self.postings.get(t)
            if entry is None:
                continue
            idf = self.idf[t]
            if not idf:
                continue
            ids, tf = entry
            out[ids] += idf * (tf * (self.k1 + 1)) / (tf + self._norm[ids])
        return out


def cosine_scores(query_vec: Sequence[float], doc_matrix: np.ndarray) -> np.ndarray:
    q = np.asarray(query_vec, dtype=float)
    qn = np.linalg.norm(q)
    if qn == 0:
        return np.zeros(doc_matrix.shape[0])
    dn = np.linalg.norm(doc_matrix, axis=1)
    dn[dn == 0] = 1e-12
    return (doc_matrix @ q) / (dn * qn)


def rrf(rank_lists: Iterable[Sequence[int]], n_docs: int, k: int = 60) -> np.ndarray:
    """
    Reciprocal Rank Fusion: sum 1/(k + rank). Operates on RANKS, not
    scores, which is the point -- cosine similarity and BM25 live on
    incomparable scales, so any weighted score sum silently encodes an
    arbitrary normalisation that drifts as either distribution moves.
    """
    fused = np.zeros(n_docs, dtype=float)
    for ranking in rank_lists:
        for rank, idx in enumerate(ranking):
            fused[idx] += 1.0 / (k + rank + 1)
    return fused


def mmr(
    query_vec: Sequence[float], doc_matrix: np.ndarray, candidates: Sequence[int],
    lambda_: float = 0.7, top_k: int = 5,
) -> list[int]:
    """
    Maximal Marginal Relevance: iteratively pick the candidate maximising
        lambda*sim(q,d) - (1-lambda)*max_{s in selected} sim(d,s)
    Trades a little relevance for coverage. Relevant here because chunks of
    one document are near-duplicates of each other, so a pure top-k can
    return five slices of the same skill and miss the second gold skill.
    """
    if not len(candidates):
        return []
    rel = {i: float(v) for i, v in zip(candidates, cosine_scores(query_vec, doc_matrix[list(candidates)]))}
    norm = doc_matrix / np.clip(np.linalg.norm(doc_matrix, axis=1, keepdims=True), 1e-12, None)
    selected: list[int] = []
    pool = list(candidates)
    while pool and len(selected) < top_k:
        best, best_score = None, -1e18
        for i in pool:
            penalty = max((float(norm[i] @ norm[j]) for j in selected), default=0.0)
            score = lambda_ * rel[i] - (1 - lambda_) * penalty
            if score > best_score:
                best, best_score = i, score
        selected.append(best)
        pool.remove(best)
    return selected


HYDE_SYSTEM = (
    "You are writing a short reference document. Given a task description, write the "
    "SKILL DOCUMENT that would help an engineer complete it: the techniques, tools, "
    "file formats and library names involved. Write the document itself, 120 words "
    "maximum. Do not restate the task and do not address the reader."
)


async def hyde_document(client, model: str, instruction: str, timeout: float = 120.0) -> str:
    """
    HyDE: retrieve with a hypothetical ANSWER instead of the question.

    A task instruction and a skill document are different genres -- one
    asks, one explains -- and embedding models place them in different
    neighbourhoods. Generating a fake document first moves the query into
    the documents' own genre before the nearest-neighbour lookup.
    """
    import asyncio

    r = await asyncio.wait_for(client.chat.completions.create(
        model=model, temperature=0, max_tokens=300,
        messages=[{"role": "system", "content": HYDE_SYSTEM},
                  {"role": "user", "content": instruction[:4000]}],
    ), timeout=timeout)
    return (r.choices[0].message.content or "").strip()


class CrossEncoder:
    """
    Local cross-encoder reranker. Unlike a bi-encoder, query and document
    are scored jointly, so the model can condition on their interaction --
    consistently the strongest cheap reranking step in the literature. Runs
    on a small MS MARCO checkpoint; CPU is fine at this corpus size, CUDA
    is used when present.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model.to(self.device).eval()

    def score(self, query: str, docs: Sequence[str], max_length: int = 512,
              batch_size: int = 16) -> np.ndarray:
        out: list[float] = []
        for i in range(0, len(docs), batch_size):
            batch = docs[i: i + batch_size]
            enc = self.tok([query] * len(batch), list(batch), padding=True,
                           truncation=True, max_length=max_length, return_tensors="pt")
            enc = {k: v.to(self.device) for k, v in enc.items()}
            with self.torch.no_grad():
                logits = self.model(**enc).logits
            out.extend(logits.squeeze(-1).float().cpu().tolist()
                       if logits.shape[-1] == 1 else logits[:, -1].float().cpu().tolist())
        return np.asarray(out, dtype=float)
