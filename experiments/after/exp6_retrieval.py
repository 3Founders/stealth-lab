"""
Experiment 6 -- self-implemented SOTA retrieval methods, head to head.

Same 22-skill library and same 89 single-skill queries as Hypothesis A,
using the same cached voyage-3-large vectors, so every number here is
directly comparable to that experiment's A0 (0.674) and A1 (0.787).

Methods are implemented in retrieval_methods.py rather than imported from
a retrieval library, per the brief. My BM25 is checked against rank-bm25
before it is used for anything: an unvalidated from-scratch scorer that
happens to produce plausible numbers is worse than no baseline at all.

Retrieval runs in numpy, not through Postgres. These are ranking
algorithms; the storage layer is irrelevant to which one ranks better, and
keeping them in one process makes the comparison exact (identical
candidate sets, identical tie-breaking).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from openai import AsyncOpenAI
from scipy.stats import binomtest

from app.config import settings
from experiments.after.corpus import load_skills, load_tasks
from experiments.after.embed_cache import CachedEmbedder
from experiments.after.retrieval_methods import (
    BM25, CrossEncoder, cosine_scores, hyde_document, mmr, rrf, tokenize,
)


def validate_bm25(corpus: list[str], queries: list[str]) -> dict:
    """Rank correlation against rank-bm25 on the real corpus."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return {"validated": False, "reason": "rank-bm25 not installed"}

    mine = BM25(corpus)
    theirs = BM25Okapi([tokenize(c) for c in corpus], k1=1.5, b=0.75)
    agree_top1 = 0
    corrs = []
    for q in queries[:40]:
        a = mine.scores(q)
        b = np.asarray(theirs.get_scores(tokenize(q)), dtype=float)
        if a.std() > 0 and b.std() > 0:
            corrs.append(float(np.corrcoef(a, b)[0, 1]))
        agree_top1 += int(np.argmax(a) == np.argmax(b))
    return {
        "validated": True,
        "mean_pearson_vs_rank_bm25": round(float(np.mean(corrs)), 6) if corrs else None,
        "top1_agreement": f"{agree_top1}/{len(queries[:40])}",
    }


def metrics(ranked_names: list[list[str]], golds: list[str]) -> dict:
    n = len(golds)
    p1 = sum(r[:1] == [g] for r, g in zip(ranked_names, golds)) / n
    r3 = sum(g in r[:3] for r, g in zip(ranked_names, golds)) / n
    r5 = sum(g in r[:5] for r, g in zip(ranked_names, golds)) / n
    return {"p@1": round(p1, 4), "recall@3": round(r3, 4), "recall@5": round(r5, 4)}


def mcnemar(a_hits: list[bool], b_hits: list[bool]) -> dict:
    b = sum(1 for x, y in zip(a_hits, b_hits) if x and not y)
    c = sum(1 for x, y in zip(a_hits, b_hits) if y and not x)
    if b + c == 0:
        return {"discordant": 0, "p_value": 1.0}
    return {"a_wins": b, "b_wins": c, "discordant": b + c,
            "p_value": round(float(binomtest(b, b + c, 0.5).pvalue), 6)}


def jaccard_scores(query: str, corpus: list[str]) -> np.ndarray:
    """The codebase's current lexical path (reuse_detection._lexical_overlap)."""
    q = set(tokenize(query))
    out = []
    for d in corpus:
        t = set(tokenize(d))
        out.append(len(q & t) / len(q | t) if (q and t) else 0.0)
    return np.asarray(out)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hyde-model", default="llama-3.1-8b-instant")
    ap.add_argument("--min-interval", type=float, default=21.0)
    ap.add_argument("--skip-hyde", action="store_true")
    ap.add_argument("--skip-cross-encoder", action="store_true")
    args = ap.parse_args()

    skills = load_skills()
    names = [s.name for s in skills]
    corpus = [s.text() for s in skills]
    tasks = [t for t in load_tasks() if len(t.gold_skills) == 1]
    golds = [t.gold_skills[0] for t in tasks]
    print(f"library={len(corpus)} skills  queries={len(tasks)} single-skill tasks")

    embedder = CachedEmbedder(min_interval=args.min_interval)
    doc_vecs = np.asarray(await embedder.embed(corpus, input_type="document"))
    q_vecs = np.asarray(await embedder.embed([t.instruction for t in tasks], input_type="query"))
    print(f"embeddings from cache: {embedder.stats()}")

    print("\nvalidating BM25 against rank-bm25 ...")
    v = validate_bm25(corpus, [t.instruction for t in tasks])
    print(json.dumps(v, indent=2))

    bm25 = BM25(corpus)
    results: dict[str, dict] = {}
    hits: dict[str, list[bool]] = {}
    ranked: dict[str, list[list[str]]] = {}

    def record(label: str, rankings: list[list[int]]) -> None:
        rn = [[names[i] for i in r] for r in rankings]
        ranked[label] = rn
        results[label] = metrics(rn, golds)
        hits[label] = [r[:1] == [g] for r, g in zip(rn, golds)]

    order = lambda s: list(np.argsort(-s))  # noqa: E731

    record("jaccard_baseline", [order(jaccard_scores(t.instruction, corpus)) for t in tasks])
    record("bm25", [order(bm25.scores(t.instruction)) for t in tasks])
    record("dense_voyage", [order(cosine_scores(q, doc_vecs)) for q in q_vecs])

    fused = []
    for t, q in zip(tasks, q_vecs):
        fused.append(order(rrf(
            [order(cosine_scores(q, doc_vecs)), order(bm25.scores(t.instruction))],
            len(corpus))))
    record("rrf_dense_bm25", fused)

    record("mmr_dense", [
        mmr(q, doc_vecs, order(cosine_scores(q, doc_vecs))[:10], lambda_=0.7, top_k=5)
        for q in q_vecs
    ])

    if not args.skip_hyde:
        print("\nHyDE: generating hypothetical documents ...")
        client = AsyncOpenAI(api_key=settings.require("groq_api_key"),
                             base_url=settings.groq_base_url)
        docs, t0 = [], time.time()
        for i, t in enumerate(tasks, 1):
            try:
                docs.append(await hyde_document(client, args.hyde_model, t.instruction))
            except Exception as exc:  # noqa: BLE001
                print(f"  {i}: FAIL {type(exc).__name__}: {str(exc)[:100]}")
                docs.append(t.instruction)  # degrade to the plain query
            if i % 20 == 0:
                print(f"  {i}/{len(tasks)}  ({time.time()-t0:.0f}s)")
        hyde_vecs = np.asarray(await embedder.embed(docs, input_type="query"))
        record("hyde_dense", [order(cosine_scores(q, doc_vecs)) for q in hyde_vecs])
        record("hyde_plus_dense_rrf", [
            order(rrf([order(cosine_scores(h, doc_vecs)), order(cosine_scores(q, doc_vecs))],
                      len(corpus)))
            for h, q in zip(hyde_vecs, q_vecs)
        ])

    if not args.skip_cross_encoder:
        print("\ncross-encoder reranking ...")
        try:
            ce = CrossEncoder()
            print(f"  device={ce.device}")
            reranked = []
            for t, q in zip(tasks, q_vecs):
                cand = order(cosine_scores(q, doc_vecs))[:10]
                s = ce.score(t.instruction[:2000], [corpus[i][:2000] for i in cand])
                reranked.append([cand[j] for j in np.argsort(-s)])
            record("cross_encoder_rerank", reranked)
        except Exception as exc:  # noqa: BLE001
            print(f"  cross-encoder unavailable: {type(exc).__name__}: {str(exc)[:200]}")

    baseline = "dense_voyage"
    tests = {k: mcnemar(hits[k], hits[baseline]) for k in hits if k != baseline}

    out = {"library": len(corpus), "queries": len(tasks),
           "bm25_validation": v, "methods": results,
           f"mcnemar_vs_{baseline}": tests}
    print("\n=== RESULTS ===")
    print(f"{'method':<24}{'p@1':>8}{'r@3':>8}{'r@5':>8}{'p vs dense':>12}")
    for k, m in sorted(results.items(), key=lambda kv: -kv[1]["p@1"]):
        p = tests.get(k, {}).get("p_value")
        print(f"{k:<24}{m['p@1']:>8.3f}{m['recall@3']:>8.3f}{m['recall@5']:>8.3f}"
              f"{(f'{p:.5f}' if p is not None else '-'):>12}")

    Path(__file__).parent.joinpath("results_exp6_retrieval.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("\nwrote results_exp6_retrieval.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
