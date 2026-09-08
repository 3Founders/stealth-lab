"""
Retrieval-quality evaluation harness (plan Part 6 / Part 8).

Measures procedure-search precision against a labelled query set and
derives the relevance-gate cutoff FROM THAT MEASUREMENT -- it is never
guessed.

    # 1. produce candidate lists to label (writes *.candidates.jsonl)
    python scripts/eval_retrieval_quality.py --generate

    # 2. (a human, or the model acting as annotator against the rubric,
    #     fills the `label` 0-3 on each candidate in retrieval_eval_v1.jsonl)

    # 3. sweep the cutoff and write retrieval_eval_v1.report.json
    python scripts/eval_retrieval_quality.py --measure

The report names: dataset size, per-bucket counts, the precision / recall
/ F1 / P@3 / nDCG@10 curve over the similarity cutoff, the no-match
bucket's zero-result rate at each cutoff, and the SELECTED cutoff (max F1
s.t. no-match zero-result rate >= 0.9). app/services/relevance_gate.py is
then updated from that file by hand, in the same change.

Relevance rubric (label, applied to each retrieved procedure per query):
    0 = irrelevant                     (wrong task / wrong domain)
    1 = related but not useful         (same area, would not help)
    2 = useful / relevant              (a reasonable answer)
    3 = excellent / direct match       (exactly this task)
"relevant" for precision/recall := label >= 2.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
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
from app.services.access import AccessScope
from app.services.domain_search import search_global

_DATA_DIR = Path(__file__).resolve().parents[1] / "tests" / "data"
_EVAL_FILE = _DATA_DIR / "retrieval_eval_v1.jsonl"
_CANDIDATES_FILE = _DATA_DIR / "retrieval_eval_v1.candidates.jsonl"
_REPORT_FILE = _DATA_DIR / "retrieval_eval_v1.report.json"

_TOP_K = 15
_RELEVANT_LABEL = 2  # label >= this counts as relevant


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


async def _retrieve(pool, query: str) -> list[dict]:
    res = await search_global(
        pool, query, object_types=["procedure"],
        scope=AccessScope.unrestricted(), limit=_TOP_K,
    )
    return res["results"].get("procedure", [])


async def generate() -> None:
    """Run retrieval for every query, emit the ranked hits with empty
    labels for annotation. Preserves any labels already in _EVAL_FILE."""
    queries = _load_jsonl(_EVAL_FILE)
    if not queries:
        print(f"no queries in {_EVAL_FILE} -- create it first (query + bucket per line)")
        return
    prior = {q["query"]: {c["name"]: c.get("label") for c in q.get("candidates", [])}
             for q in queries}
    pool = await create_pool(min_size=1, max_size=2)
    try:
        out = []
        for q in queries:
            hits = await _retrieve(pool, q["query"])
            cands = []
            for h in hits:
                name = h.get("name")
                cands.append({
                    "name": name,
                    "procedure_id": h.get("procedure_id"),
                    "display_name": h.get("display_name"),
                    "similarity_score": h.get("similarity_score"),
                    "label": prior.get(q["query"], {}).get(name),
                })
            out.append({
                "query": q["query"], "bucket": q["bucket"],
                "note": q.get("note", ""), "candidates": cands,
            })
        _write_jsonl(_CANDIDATES_FILE, out)
        n_unlabelled = sum(1 for q in out for c in q["candidates"] if c["label"] is None)
        print(f"wrote {_CANDIDATES_FILE} -- {len(out)} queries, "
              f"{sum(len(q['candidates']) for q in out)} candidates, "
              f"{n_unlabelled} still need a label")
    finally:
        await pool.close()


def _dcg(labels: list[int]) -> float:
    return sum((2 ** l - 1) / math.log2(i + 2) for i, l in enumerate(labels))


def _ndcg(retrieved_labels: list[int], ideal_labels: list[int], k: int = 10) -> float:
    ideal = _dcg(sorted(ideal_labels, reverse=True)[:k])
    if ideal == 0:
        return 1.0 if not any(retrieved_labels[:k]) else 0.0
    return _dcg(retrieved_labels[:k]) / ideal


async def measure() -> None:
    queries = _load_jsonl(_EVAL_FILE)
    labelled = [q for q in queries if q.get("candidates")]
    n_cand = sum(len(q["candidates"]) for q in labelled)
    n_unlabelled = sum(1 for q in labelled for c in q["candidates"] if c.get("label") is None)
    if n_unlabelled:
        print(f"WARNING: {n_unlabelled}/{n_cand} candidates unlabelled -- "
              f"treating them as 0 (irrelevant) for this run")

    buckets: dict[str, int] = {}
    for q in labelled:
        buckets[q["bucket"]] = buckets.get(q["bucket"], 0) + 1

    # candidate similarity range
    sims = [c["similarity_score"] for q in labelled for c in q["candidates"]
            if c.get("similarity_score") is not None]
    if not sims:
        print("no similarity scores in the candidate lists -- run --generate first")
        return
    lo, hi = min(sims), max(sims)
    cutoffs = [round(lo + (hi - lo) * i / 40, 4) for i in range(41)]

    curve = []
    for tau in cutoffs:
        tp = fp = fn = 0
        p_at_3_num = p_at_3_den = 0
        ndcgs = []
        no_match_zero = no_match_total = 0
        for q in labelled:
            cands = sorted(q["candidates"],
                           key=lambda c: (c.get("similarity_score") or -1), reverse=True)
            kept = [c for c in cands if (c.get("similarity_score") or -1) >= tau]
            all_labels = [int(c.get("label") or 0) for c in cands]
            kept_labels = [int(c.get("label") or 0) for c in kept]

            tp += sum(1 for l in kept_labels if l >= _RELEVANT_LABEL)
            fp += sum(1 for l in kept_labels if l < _RELEVANT_LABEL)
            fn += sum(1 for l in all_labels if l >= _RELEVANT_LABEL) - \
                sum(1 for l in kept_labels if l >= _RELEVANT_LABEL)

            top3 = kept_labels[:3]
            p_at_3_num += sum(1 for l in top3 if l >= _RELEVANT_LABEL)
            p_at_3_den += max(len(top3), 0) or 0
            ndcgs.append(_ndcg([int(c.get("label") or 0) for c in kept], all_labels))

            if q["bucket"] == "no_match":
                no_match_total += 1
                if not kept:
                    no_match_zero += 1

        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        curve.append({
            "cutoff": tau,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "p_at_3": round(p_at_3_num / p_at_3_den, 4) if p_at_3_den else None,
            "ndcg_at_10": round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else None,
            "no_match_zero_result_rate": round(no_match_zero / no_match_total, 4)
            if no_match_total else None,
        })

    # SELECTION RULE (documented, deterministic): highest F1 among cutoffs
    # whose no-match bucket returns zero results in >= 90% of its queries;
    # tie -> the higher (more precise) cutoff.
    eligible = [c for c in curve
                if (c["no_match_zero_result_rate"] or 0) >= 0.9]
    pool_for_pick = eligible or curve
    best = max(pool_for_pick, key=lambda c: (c["f1"], c["cutoff"]))
    # strong-match band: the cutoff at which precision first reaches >= 0.9
    strong = next((c["cutoff"] for c in curve if c["precision"] >= 0.9
                   and c["cutoff"] >= best["cutoff"]), None)

    report = {
        "dataset": _EVAL_FILE.name,
        "queries": len(labelled),
        "candidates": n_cand,
        "unlabelled_treated_as_zero": n_unlabelled,
        "buckets": buckets,
        "similarity_range": [round(lo, 4), round(hi, 4)],
        "selection_rule": "max F1 s.t. no_match zero-result rate >= 0.9; tie -> higher cutoff",
        "selected_cutoff": best["cutoff"],
        "selected_metrics": best,
        "strong_label_cutoff": strong,
        "curve": curve,
    }
    _REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("queries", "candidates", "buckets", "similarity_range",
                       "selected_cutoff", "strong_label_cutoff")}, indent=2))
    print(f"\nselected metrics: {json.dumps(best)}")
    print(f"full curve + report -> {_REPORT_FILE}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--generate", action="store_true",
                   help="run retrieval, emit candidate lists to label")
    g.add_argument("--measure", action="store_true",
                   help="sweep the cutoff over labelled data, write the report")
    args = ap.parse_args()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set")
        return 1
    if args.generate:
        asyncio.run(generate())
    else:
        asyncio.run(measure())
    return 0


if __name__ == "__main__":
    sys.exit(main())
