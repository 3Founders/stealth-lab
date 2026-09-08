"""
Before/after retrieval precision for the canonical retrieval
representation (plan Part 24 #10). Read-only: it does NOT touch the live
corpus. It re-embeds, in memory, every procedure that appears in
retrieval_eval_v1's candidate lists under BOTH representations, re-scores
each eval query against each, and runs the same cutoff sweep for each.

  OLD text = " ".join([capability_statement or goal, "Workflow:", *step goals])
             (the formula skill_ingestion / backfill_procedure_embeddings
             used before this change)
  NEW text = build_procedure_retrieval_document(procedure)

Same embedding model for both (whatever the Embedder is configured to
use), so the delta is the representation, not the provider.

    python scripts/eval_representation_before_after.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.retrieval_document import build_procedure_retrieval_document

_DATA = Path(__file__).resolve().parents[1] / "tests" / "data"
_EVAL = _DATA / "retrieval_eval_v1.jsonl"
_OUT = _DATA / "retrieval_eval_v1.before_after.json"
_RELEVANT = 2


def _old_text(row) -> str:
    steps = row["steps"] or []
    if isinstance(steps, str):
        steps = json.loads(steps)
    step_text = [
        str(s.get("goal") or s.get("description") or "")
        for s in steps if isinstance(s, dict)
    ]
    return " ".join([row["capability_statement"] or row["goal"] or row["name"],
                     "Workflow:", *step_text])


def _cos(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _sweep(per_query):
    sims = [c["sim"] for q in per_query for c in q["cands"]]
    lo, hi = min(sims), max(sims)
    best = None
    for i in range(41):
        tau = lo + (hi - lo) * i / 40
        tp = fp = fn = 0
        nm_zero = nm_total = 0
        for q in per_query:
            kept = [c for c in q["cands"] if c["sim"] >= tau]
            tp += sum(1 for c in kept if c["label"] >= _RELEVANT)
            fp += sum(1 for c in kept if c["label"] < _RELEVANT)
            fn += sum(1 for c in q["cands"] if c["label"] >= _RELEVANT) - \
                sum(1 for c in kept if c["label"] >= _RELEVANT)
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
        if nmz >= 0.9 and (best is None or row["f1"] > best["f1"]):
            best = row
    return best


async def main():
    rows = [json.loads(l) for l in _EVAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    names = sorted({c["name"] for r in rows for c in r["candidates"]})
    pool = await create_pool(min_size=1, max_size=2)
    procs = {}
    for n in names:
        rec = await pool.fetchrow(
            "SELECT name, goal, capability_statement, steps, preconditions, invariants, "
            "postconditions, failure_conditions, domain, domain_payload, display_name "
            "FROM procedures WHERE name = $1 AND t_invalid IS NULL ORDER BY t_created DESC LIMIT 1",
            n,
        )
        if rec:
            procs[n] = dict(rec)
    await pool.close()
    print(f"{len(procs)}/{len(names)} eval procedures resolved")

    embedder = Embedder()
    old_texts = {n: _old_text(p) for n, p in procs.items()}
    new_texts = {n: build_procedure_retrieval_document(p) for n, p in procs.items()}
    uniq = list(procs)

    async def _embed_all(texts):
        out = []
        for i in range(0, len(texts), 24):
            out.extend(await embedder.embed(texts[i:i + 24], input_type="document"))
        return out

    old_vecs = dict(zip(uniq, await _embed_all([old_texts[n] for n in uniq])))
    new_vecs = dict(zip(uniq, await _embed_all([new_texts[n] for n in uniq])))

    def build(vecs):
        out = []
        for r in rows:
            qv = None
            cands = []
            for c in r["candidates"]:
                if c["name"] not in vecs:
                    continue
                if qv is None:
                    qv = None  # placeholder; filled below
                cands.append({"name": c["name"], "label": int(c.get("label") or 0),
                              "_v": vecs[c["name"]]})
            out.append({"bucket": r["bucket"], "query": r["query"], "cands": cands})
        return out

    old_pq = build(old_vecs)
    new_pq = build(new_vecs)
    # score against a fresh query embedding (query input_type)
    _qtexts = [r["query"] for r in rows]
    _qv = []
    for i in range(0, len(_qtexts), 24):
        _qv.extend(await embedder.embed(_qtexts[i:i + 24], input_type="query"))
    qvecs = dict(zip(_qtexts, _qv))
    for pq in (old_pq, new_pq):
        for q in pq:
            qv = qvecs[q["query"]]
            for c in q["cands"]:
                c["sim"] = _cos(qv, c["_v"])
                del c["_v"]

    old_best = _sweep(old_pq)
    new_best = _sweep(new_pq)
    report = {
        "eval_queries": len(rows),
        "eval_procedures_resolved": len(procs),
        "embedding_model": embedder.embedding_model_id(),
        "OLD_representation": {
            "text_formula": "capability_statement|goal + 'Workflow:' + step goals",
            "best_gate": old_best,
        },
        "NEW_representation": {
            "text_formula": "build_procedure_retrieval_document (procdoc_v1)",
            "best_gate": new_best,
        },
    }
    _OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\n-> {_OUT}")


if __name__ == "__main__":
    asyncio.run(main())
