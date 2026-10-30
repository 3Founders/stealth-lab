"""Score the tool router's retrieval on ToolRet (Shi et al., ACL 2025 Findings; mangopy/ToolRet-*): each query is
answered from the whole pool of ~43k tools, and nDCG@10 / recall@10 are measured against its labelled tools.

    python scripts/eval_tool_retrieval.py --data ../.scratch/toolret [--per-subset 15] [--arms bm25,hybrid,rerank]

Arms, each adding one stage of app/tool_routing/retrieve.py:
  bm25      keywords alone
  dense     embeddings alone
  hybrid    BM25 + embeddings, reciprocal-rank fused
  rerank    hybrid, then the cross-encoder over the top 40
The tools' vectors are embedded once and kept in <data>/doc_vectors.npz (re-used across runs). The paper's reference
point: the best retriever it tested reached 33.83 nDCG@10.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.embeddings import Embedder  # noqa: E402
from app.tool_routing.providers import ToolEmbedder  # noqa: E402
from app.tool_routing.catalog import ToolCard  # noqa: E402
from app.tool_routing.rerank import rerank  # noqa: E402
from app.tool_routing.retrieve import rank  # noqa: E402


def tool_cards(data: Path) -> list[ToolCard]:
    cards = []
    for f in sorted((data / "tools").glob("*.parquet")):
        for tid, doc in pd.read_parquet(f).itertuples(index=False):
            try:
                d = json.loads(doc)
            except ValueError:
                d = {"description": str(doc)}
            params = d.get("doc_arguments") or d.get("parameters") or d.get("required_parameters") or {}
            pnames = list((params.get("properties") or {}).keys()) if isinstance(params, dict) else \
                [p.get("name", "") for p in params if isinstance(p, dict)] if isinstance(params, list) else []
            name = str(d.get("name") or d.get("api_name") or d.get("tool_name") or tid)
            desc = " ".join(str(x) for x in (d.get("description"), d.get("category_name"), d.get("tool_name")) if x)
            cards.append(ToolCard(id=str(tid), source="toolret", name=name, title="",
                                  description=(desc + (" Parameters: " + ", ".join(pnames) if pnames else ""))[:1500]))
    return cards


def queries(data: Path, per: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for f in sorted((data / "queries").glob("*.parquet")):
        q = pd.read_parquet(f)
        rows = q.to_dict("records")
        rng.shuffle(rows)
        for r in rows[:per]:
            labels = json.loads(r["labels"]) if isinstance(r["labels"], str) else r["labels"]
            rel = {str(x["id"]): float(x.get("relevance", 1) or 1) for x in labels if isinstance(x, dict) and x.get("id")}
            if rel:
                out.append({"subset": f.stem, "query": r["query"], "instruction": r.get("instruction") or "", "rel": rel})
    return out


def ndcg(ranked: list[str], rel: dict[str, float], k: int = 10) -> float:
    dcg = sum(rel.get(t, 0.0) / math.log2(i + 2) for i, t in enumerate(ranked[:k]))
    ideal = sum(r / math.log2(i + 2) for i, r in enumerate(sorted(rel.values(), reverse=True)[:k]))
    return dcg / ideal if ideal else 0.0


def recall(ranked: list[str], rel: dict[str, float], k: int = 10) -> float:
    return len(set(ranked[:k]) & set(rel)) / len(rel)


async def doc_vectors(cards: list[ToolCard], embedder, path: Path) -> tuple[dict[str, int], np.ndarray, str]:
    model = embedder.embedding_model_id()
    if path.exists():
        z = np.load(path, allow_pickle=False)
        if str(z["model"]) == model and len(z["ids"]) == len(cards):
            m = z["vectors"].astype(np.float32)
            m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
            return {str(c): i for i, c in enumerate(z["ids"])}, m, model
    vecs, t0 = [], time.monotonic()
    step = int(os.environ.get("EVAL_EMBED_BATCH", "96"))
    for i in range(0, len(cards), step):
        vecs.extend(await embedder.embed([c.text[:2000] for c in cards[i:i + step]], input_type="document"))
        if (i // step) % 5 == 0:
            print(f"  embedded {len(vecs)}/{len(cards)} ({time.monotonic() - t0:.0f} s)", flush=True)
    np.savez_compressed(path, ids=np.array([c.id for c in cards]), vectors=np.array(vecs, dtype=np.float16), model=np.array(model))
    m = np.array(vecs, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return {c.id: i for i, c in enumerate(cards)}, m, model


async def main(a) -> int:
    data = Path(a.data)
    cards = tool_cards(data)
    qs = queries(data, a.per_subset, a.seed)
    arms = a.arms.split(",")
    print(f"{len(cards)} tools, {len(qs)} queries from {len({q['subset'] for q in qs})} subsets; arms {arms}")
    # the router's own embedder (DeepInfra) when its key is set, else the backend's
    if any(x in arms for x in ("dense", "hybrid", "rerank")):
        te = ToolEmbedder()
        embedder = te if te.available() and not a.backend_embedder else Embedder()
        print(f"embedder: {embedder.embedding_model_id()}")
    else:
        embedder = None
    store = await doc_vectors(cards, embedder, data / "doc_vectors.npz") if embedder else None
    res: dict[str, dict[str, list[float]]] = {arm: defaultdict(list) for arm in arms}
    t0 = time.monotonic()
    for n, q in enumerate(qs, 1):
        text = f"{q['instruction']} {q['query']}".strip() if a.instruction else q["query"]
        for arm in arms:
            if arm == "dense":
                rows, mat, _m = store
                qv = np.array(await embedder.embed_one(text, input_type="query"), dtype=np.float32)
                qv /= np.linalg.norm(qv) + 1e-9
                top = np.argsort(-(mat @ qv))[:10]
                ids = [cards[int(i)].id for i in top]
            else:
                hits, _info = await rank(text, cards, goal=None, k=10, embedder=embedder if arm != "bm25" else None,
                                         vector_store=store, reranker=rerank if arm == "rerank" else None)
                ids = [h.card.id for h in hits]
            res[arm]["ndcg"].append(ndcg(ids, q["rel"]))
            res[arm]["recall"].append(recall(ids, q["rel"]))
            res[arm][f"ndcg:{q['subset']}"].append(ndcg(ids, q["rel"]))
        if n % 50 == 0:
            print(f"  {n}/{len(qs)} queries ({time.monotonic() - t0:.0f} s): " +
                  ", ".join(f"{arm} {100 * np.mean(res[arm]['ndcg']):.1f}" for arm in arms), flush=True)
    print("\narm      nDCG@10  recall@10   (macro nDCG@10 over subsets)")
    report = {}
    for arm in arms:
        subs = [np.mean(v) for k, v in res[arm].items() if k.startswith("ndcg:")]
        report[arm] = {"ndcg10": float(np.mean(res[arm]["ndcg"])), "recall10": float(np.mean(res[arm]["recall"])),
                       "macro_ndcg10": float(np.mean(subs))}
        print(f"{arm:<8} {100 * report[arm]['ndcg10']:7.2f}  {100 * report[arm]['recall10']:8.2f}   ({100 * report[arm]['macro_ndcg10']:.2f})")
    out = data / f"eval_{'inst' if a.instruction else 'query'}_{a.per_subset}.json"
    out.write_text(json.dumps({"queries": len(qs), "tools": len(cards), "arms": report}, indent=1))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../.scratch/toolret")
    ap.add_argument("--per-subset", type=int, default=15)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--arms", default="bm25,dense,hybrid,rerank")
    ap.add_argument("--instruction", action="store_true", help="prepend ToolRet's task instruction to the query")
    ap.add_argument("--backend-embedder", action="store_true", help="use the backend's Embedder, not the router's")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
