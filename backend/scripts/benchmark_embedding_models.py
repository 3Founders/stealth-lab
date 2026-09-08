"""
Controlled embedding-model benchmark for the retrieval release closure.

HELD CONSTANT: the procedure corpus, the canonical retrieval representation
(procdoc_v1), the lexical retrieval leg (the exact `_PROC_LEXICAL_SQL`
from applicability.py), RRF fusion (retrieval.fuse_rrf), the browse-mode
applicability filter (`_CANDIDATE_BASE_WHERE`), access filtering
(unrestricted for the eval — no private rows), the ranking logic, the 57
evaluation queries, the 855 labels, and the scoring methodology.

VARIED: only the embedding model — one of the three the codebase actually
supports (local:mxbai-embed-large, voyage:voyage-3-large,
gemini:gemini-embedding-001). No unsupported providers.

For each model:
  * embed every live procedure's `retrieval_document` and every eval query
    with that model (local reuses the live `embedding` column);
  * per query, fuse the model's cosine-similarity ranking with the
    model-independent lexical ranking via the real fuse_rrf;
  * derive the optimal relevance threshold INDEPENDENTLY for this model
    using the documented rule (max F1 s.t. the no-match bucket returns
    zero results in >= 90 % of its queries; tie -> higher cutoff);
  * report Recall@{1,3,5,10}, Precision@{1,3,5,10}, MRR, nDCG@10 (all on
    the raw ranking — ranker quality, threshold-independent), plus, at the
    model's own optimal threshold: optimal F1, no-match false-positive
    rate, zero-result rate; plus embedding latency / failures / errors.

Vectors are cached to .scratch/retrieval-release-closure/vectors/ so S6/S7
can reuse them without re-embedding.

    python scripts/benchmark_embedding_models.py [--models local,voyage,gemini] [--topn 50]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from app.db.session import create_pool
from app.services.applicability import _PROC_LEXICAL_SQL, _CANDIDATE_BASE_WHERE
from app.services.embeddings import Embedder
from app.services.retrieval import fuse_rrf

_CLOSURE = Path(__file__).resolve().parents[2] / ".scratch" / "retrieval-release-closure"
_VEC_DIR = _CLOSURE / "vectors"
_EVAL = Path(__file__).resolve().parents[1] / "tests" / "data" / "retrieval_eval_v1.jsonl"
_OUT = _CLOSURE / "embedding-model-benchmark.json"

_RELEVANT = 2      # label >= this is "relevant"
_TOP_N = 50        # candidate depth pulled from each leg before fusion (matches search top_k*expansion scale)
_KS = (1, 3, 5, 10)
INDEX = "eval"


def _pgvec(v):
    """asyncpg has no vector codec -> a VECTOR column comes back as the
    text form '[0.1,0.2,...]'. Normalize to list[float]."""
    if v is None:
        return None
    if isinstance(v, str):
        return [float(x) for x in v.strip().lstrip("[").rstrip("]").split(",") if x]
    return [float(x) for x in v]


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _ndcg(labels, k=10):
    def dcg(ls):
        return sum((2 ** l - 1) / math.log2(i + 2) for i, l in enumerate(ls[:k]))
    ideal = dcg(sorted(labels, reverse=True))
    return dcg(labels) / ideal if ideal else (1.0 if not any(labels[:k]) else 0.0)


async def _embed_all(embedder, texts, batch, tag, pace_s=0.0, input_type="document", cache_path=None):
    """Chunked embed with latency + failure accounting + inter-request
    pacing (Voyage free tier is 3 RPM / 10K TPM). Incrementally writes
    `cache_path` so a crash does not lose progress; resumes from it."""
    vecs, fails, t0 = [], 0, time.perf_counter()
    if cache_path and cache_path.exists():
        vecs = json.loads(cache_path.read_text())
        if len(vecs) >= len(texts):
            return vecs[:len(texts)], sum(1 for v in vecs[:len(texts)] if v is None), 0.0
        print(f"  [{tag}] resuming from {len(vecs)}/{len(texts)}")
    start = len(vecs)
    for i in range(start, len(texts), batch):
        chunk = texts[i:i + batch]
        for attempt in range(4):
            try:
                vs = await embedder.embed(chunk, input_type=input_type)
                if len(vs) != len(chunk):
                    raise RuntimeError(f"count {len(vs)} != {len(chunk)}")
                vecs.extend(vs)
                break
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if attempt == 3:
                    print(f"  [{tag}] batch {i} FAILED: {msg[:140]}")
                    vecs.extend([None] * len(chunk))
                    fails += len(chunk)
                else:
                    wait = 25 if ("rate limit" in msg.lower() or "429" in msg or "RPM" in msg) else 6 * (attempt + 1)
                    await asyncio.sleep(wait)
        if cache_path:
            cache_path.write_text(json.dumps(vecs))
        if (i // batch) % 5 == 0:
            print(f"  [{tag}] {i + len(chunk)}/{len(texts)}  ({time.perf_counter()-t0:.0f}s)", flush=True)
        if pace_s and i + batch < len(texts):
            await asyncio.sleep(pace_s)
    return vecs, fails, time.perf_counter() - t0


# per-model request shape: (doc_batch, pace_seconds_between_requests)
# Voyage free tier w/o payment method: 3 RPM, 10K TPM -> tiny batches, 21s apart.
_SHAPE = {"local": (64, 0.0), "gemini": (48, 0.0), "voyage": (16, 21.0)}


def _sweep_threshold(per_query):
    """Documented rule: max F1 among cutoffs whose no-match bucket returns
    zero results in >= 90 % of its no-match queries; tie -> higher cutoff."""
    sims = [c["sim"] for q in per_query for c in q["ranked"]]
    lo, hi = min(sims), max(sims)
    best = None
    curve = []
    for i in range(41):
        tau = lo + (hi - lo) * i / 40
        tp = fp = fn = 0
        nm_zero = nm_total = 0
        for q in per_query:
            kept = [c for c in q["ranked"] if c["sim"] >= tau]
            rel_total = sum(1 for c in q["ranked"] if c["label"] >= _RELEVANT)
            tp += sum(1 for c in kept if c["label"] >= _RELEVANT)
            fp += sum(1 for c in kept if c["label"] < _RELEVANT)
            fn += rel_total - sum(1 for c in kept if c["label"] >= _RELEVANT)
            if q["bucket"] == "no_match":
                nm_total += 1
                nm_zero += (not kept)
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        nmz = nm_zero / nm_total if nm_total else 1.0
        row = {"cutoff": round(tau, 4), "precision": round(prec, 4),
               "recall": round(rec, 4), "f1": round(f1, 4),
               "no_match_zero_rate": round(nmz, 4)}
        curve.append(row)
        if nmz >= 0.9 and (best is None or f1 > best["f1"] or
                           (f1 == best["f1"] and tau > best["cutoff"])):
            best = row
    return best or curve[-1], curve


def _rank_metrics(per_query):
    """Recall@K / Precision@K / MRR / nDCG@10 on the RAW ranking (no gate)."""
    R = {k: [] for k in _KS}
    P = {k: [] for k in _KS}
    rr, ndcgs = [], []
    for q in per_query:
        labels = [c["label"] for c in q["ranked"]]
        rel_total = sum(1 for l in labels if l >= _RELEVANT)
        ndcgs.append(_ndcg(labels, 10))
        first = next((i for i, l in enumerate(labels) if l >= _RELEVANT), None)
        rr.append(1.0 / (first + 1) if first is not None else 0.0)
        for k in _KS:
            topk = labels[:k]
            hits = sum(1 for l in topk if l >= _RELEVANT)
            P[k].append(hits / k)
            if rel_total:
                R[k].append(hits / rel_total)
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    return {
        **{f"recall@{k}": mean(R[k]) for k in _KS},
        **{f"precision@{k}": mean(P[k]) for k in _KS},
        "mrr": mean(rr),
        "ndcg@10": mean(ndcgs),
    }


async def run(models, topn):
    _VEC_DIR.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in _EVAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    queries = [r["query"] for r in rows]
    label_by_qn = {(r["query"], c["name"]): int(c.get("label") or 0)
                   for r in rows for c in r["candidates"]}

    eval_names = sorted({c["name"] for r in rows for c in r["candidates"]})
    pool = await create_pool(min_size=1, max_size=4)
    if INDEX == "eval":
        # Bounded, fair comparison: the exact set of procedures the labelled
        # candidates name (same set for every model). Chosen because Voyage's
        # free tier without a payment method is 3 RPM, making a full-corpus
        # re-embed per model impractical (~1 h). Documented as a limitation.
        corpus = await pool.fetch(
            f"SELECT id, name, retrieval_document, embedding, embedding_model_id "
            f"FROM procedures WHERE {_CANDIDATE_BASE_WHERE} AND name = ANY($1::text[]) ORDER BY id",
            eval_names,
        )
    else:
        corpus = await pool.fetch(
            f"SELECT id, name, retrieval_document, embedding, embedding_model_id "
            f"FROM procedures WHERE {_CANDIDATE_BASE_WHERE} ORDER BY id"
        )
    ids = [str(r["id"]) for r in corpus]
    names = {str(r["id"]): r["name"] for r in corpus}
    docs = [r["retrieval_document"] or r["name"] for r in corpus]
    # model-independent lexical ranking per query (real _PROC_LEXICAL_SQL)
    lex = {}
    for q in queries:
        lr = await pool.fetch(_PROC_LEXICAL_SQL, q, topn)
        lex[q] = [(str(r["id"]), "procedures", i) for i, r in enumerate(lr)]
    live_vec = {str(r["id"]): _pgvec(r["embedding"])
                for r in corpus} if any(m == "local" for m in models) else {}
    await pool.close()
    print(f"corpus: {len(ids)} live procedures; {len(queries)} queries")

    results = {}
    for model in models:
        print(f"\n=== {model} ===")
        embedder = Embedder(provider=model)
        model_id = embedder.embedding_model_id()
        cache = _VEC_DIR / f"{model}.doc.json"
        qcache = _VEC_DIR / f"{model}.query.json"
        fails, emb_latency = 0, 0.0

        dbatch, pace = _SHAPE.get(model, (48, 0.0))
        if model == "local":
            doc_vecs = [live_vec.get(i) for i in ids]
            fails = sum(1 for v in doc_vecs if v is None)
        else:
            doc_vecs, fails, emb_latency = await _embed_all(
                embedder, docs, dbatch, f"{model}.doc", pace_s=pace, cache_path=cache)
        by_id = dict(zip(ids, doc_vecs))

        q_vecs, _qf, qlat = await _embed_all(
            embedder, queries, dbatch, f"{model}.query",
            pace_s=pace, input_type="query", cache_path=qcache)
        emb_latency += qlat
        qv = dict(zip(queries, q_vecs))

        per_query = []
        for r in rows:
            q = r["query"]
            v = qv[q]
            # semantic leg: top-N by cosine over the whole live corpus
            scored = sorted(
                ((i, _cos(v, by_id[i])) for i in ids if by_id.get(i) is not None),
                key=lambda t: t[1], reverse=True,
            )[:topn]
            sem_hits = [(i, "procedures", rank) for rank, (i, _s) in enumerate(scored)]
            sim_by_id = {i: s for i, s in scored}
            fused, _ = fuse_rrf([(sem_hits, "semantic"), (lex[q], "lexical")])
            order = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
            ranked = []
            for (pid, _tbl), _score in order[:topn]:
                nm = names.get(pid)
                ranked.append({
                    "name": nm,
                    "label": label_by_qn.get((q, nm), 0),
                    # the gate signal find_applicable_procedures attaches is the
                    # raw cosine similarity of the row in the query's space
                    "sim": round(sim_by_id.get(pid, 0.0), 6),
                })
            per_query.append({"bucket": r["bucket"], "query": q, "ranked": ranked})

        best, curve = _sweep_threshold(per_query)
        rank = _rank_metrics(per_query)
        results[model] = {
            "model_id": model_id,
            "embedding_failures": fails,
            "embedding_latency_s_total": round(emb_latency, 1),
            "docs_embedded": len(ids) - fails,
            "optimal_threshold": best["cutoff"],
            "at_optimal_threshold": {
                "precision": best["precision"], "recall": best["recall"],
                "f1": best["f1"], "no_match_zero_result_rate": best["no_match_zero_rate"],
                "no_match_false_positive_rate": round(1 - best["no_match_zero_rate"], 4),
            },
            "raw_ranking": rank,
            "zero_result_rate_at_threshold": round(
                sum(1 for q in per_query
                    if not [c for c in q["ranked"] if c["sim"] >= best["cutoff"]]) / len(per_query), 4),
            "curve": curve,
        }
        print(json.dumps({k: results[model][k] for k in
                          ("model_id", "optimal_threshold", "at_optimal_threshold",
                           "raw_ranking", "embedding_failures")}, indent=2))

    _OUT.write_text(json.dumps({
        "held_constant": ["corpus", "procdoc_v1 representation", "_PROC_LEXICAL_SQL lexical leg",
                          "fuse_rrf", "_CANDIDATE_BASE_WHERE browse filter", "access=unrestricted",
                          "ranking", "57 queries", "855 labels", "scoring"],
        "varied": "embedding model only",
        "threshold_rule": "max F1 s.t. no-match bucket zero-result rate >= 0.9; tie -> higher cutoff; swept per model",
        "topn": topn,
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\n-> {_OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="local,voyage,gemini")
    ap.add_argument("--topn", type=int, default=_TOP_N)
    ap.add_argument("--index", choices=("eval", "corpus"), default="eval")
    a = ap.parse_args()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set"); return 1
    global INDEX
    INDEX = a.index
    asyncio.run(run([m.strip() for m in a.models.split(",") if m.strip()], a.topn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
