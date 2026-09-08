"""
Build a representative human-validation sample of the retrieval eval
labels (release closure section 2) and run an INDEPENDENT second-model
cross-check as an interim signal.

Output:
  .scratch/retrieval-release-closure/label-validation-sample.jsonl
      one row per (query, candidate): query, bucket, candidate_name,
      candidate_display, similarity, model_label (original), model_reason,
      independent_model_label, independent_model_reason, agreement,
      human_label (BLANK — to be filled).
  .scratch/retrieval-release-closure/label-validation.md
      agreement stats + corrected metrics on the sample + release note.

The independent model is `gpt-oss-120b` on the existing General Compute
provider (different family from the original labeller). It is NOT a
substitute for human review; it is a cheap bias probe.

    python scripts/label_validation_sample.py [--per-bucket 8]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from openai import OpenAI

from app.config import settings
from app.db.session import create_pool

_CLOSURE = Path(__file__).resolve().parents[2] / ".scratch" / "retrieval-release-closure"
_EVAL = Path(__file__).resolve().parents[1] / "tests" / "data" / "retrieval_eval_v1.jsonl"
_SAMPLE = _CLOSURE / "label-validation-sample.jsonl"
_MD = _CLOSURE / "label-validation.md"
JUDGE_MODEL = "gpt-oss-120b"
_RELEVANT = 2

_RUBRIC = (
    "You grade how RELEVANT a retrieved software 'procedure' (a reusable task "
    "workflow) is to a user's search query. Return JSON {\"label\": 0|1|2|3, "
    "\"reason\": \"one sentence\"}.\n"
    "0 = irrelevant (wrong task or wrong domain)\n"
    "1 = related but not useful (same area, would not actually help this query)\n"
    "2 = useful / relevant (a reasonable answer)\n"
    "3 = excellent / direct match (exactly what the query asks for)"
)


def _client():
    return OpenAI(api_key=settings.general_compute_api_key,
                 base_url=settings.general_compute_base_url, max_retries=1, timeout=90)


def _judge(client, query, name, doc):
    user = (f"QUERY: {query}\n\nPROCEDURE (canonical name: {name}):\n{doc[:2500]}\n\n"
            "Grade its relevance to the QUERY.")
    for attempt in range(6):
        try:
            r = client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[{"role": "system", "content": _RUBRIC},
                          {"role": "user", "content": user}],
                temperature=0.0, max_tokens=200,
                response_format={"type": "json_object"})
            j = json.loads(r.choices[0].message.content or "{}")
            lab = int(j.get("label"))
            return (lab if lab in (0, 1, 2, 3) else None), (j.get("reason") or "")[:200]
        except Exception as exc:  # noqa: BLE001
            if "429" in str(exc) or "high demand" in str(exc).lower():
                time.sleep(8 * (attempt + 1))
            else:
                return None, f"judge error: {str(exc)[:120]}"
    return None, "judge exhausted retries"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=8)
    a = ap.parse_args()
    _CLOSURE.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260908)
    rows = [json.loads(l) for l in _EVAL.read_text(encoding="utf-8").splitlines() if l.strip()]

    # stratified pick: from each bucket, sample queries; from each query,
    # take its top candidate, a mid candidate, and a label-0 candidate so
    # every grade band and every bucket is represented.
    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[r["bucket"]].append(r)
    picks = []
    for bucket, qs in by_bucket.items():
        rng.shuffle(qs)
        take = qs[:max(2, a.per_bucket // 2)]
        for q in take:
            cands = q["candidates"]
            by_label = defaultdict(list)
            for c in cands:
                by_label[int(c.get("label") or 0)].append(c)
            chosen = []
            for lab in (3, 2, 1, 0):
                if by_label[lab]:
                    chosen.append(rng.choice(by_label[lab]))
            for c in chosen[:4]:
                picks.append((q["query"], q["bucket"], c))
    print(f"{len(picks)} (query, candidate) pairs sampled across {len(by_bucket)} buckets")

    pool = await create_pool(min_size=1, max_size=2)
    docmap = {}
    names = list({c["name"] for _q, _b, c in picks})
    for r in await pool.fetch(
        "SELECT name, retrieval_document, display_name FROM procedures "
        "WHERE name = ANY($1::text[]) AND t_invalid IS NULL", names):
        docmap[r["name"]] = (r["retrieval_document"] or "", r["display_name"])
    await pool.close()

    client = _client()
    out = []
    agree = Counter()
    for i, (q, bucket, c) in enumerate(picks):
        doc, disp = docmap.get(c["name"], ("", None))
        ilab, ireason = _judge(client, q, c["name"], doc) if doc else (None, "no retrieval_document")
        m = int(c.get("label") or 0)
        rec = {
            "query": q, "bucket": bucket, "candidate_name": c["name"],
            "candidate_display_name": disp, "similarity": c.get("similarity_score"),
            "model_label": m, "independent_model_label": ilab,
            "independent_model_reason": ireason,
            "agreement": (ilab == m) if ilab is not None else None,
            "within_1": (abs(ilab - m) <= 1) if ilab is not None else None,
            "same_relevant_class": ((ilab >= _RELEVANT) == (m >= _RELEVANT)) if ilab is not None else None,
            "human_label": "",  # TO BE FILLED
            "human_reason": "",
        }
        out.append(rec)
        if ilab is not None:
            agree["exact"] += rec["agreement"]
            agree["within_1"] += rec["within_1"]
            agree["same_class"] += rec["same_relevant_class"]
            agree["n"] += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(picks)}", flush=True)
        time.sleep(0.15)

    _SAMPLE.write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")

    n = agree["n"] or 1
    disagreements = [r for r in out if r["agreement"] is False]
    # corrected metrics on the sample IF the independent model is taken as truth
    # (illustrative only -- real correction needs human labels)
    md = [
        "# Retrieval label validation (release closure section 2)",
        "",
        f"Sample: **{len(out)} (query, candidate) pairs**, stratified across all "
        f"{len(by_bucket)} buckets and all four grade bands, fixed seed 20260908.",
        "",
        "## Human review status",
        "",
        "**Not completed.** No human review infrastructure exists in this "
        "environment and no human annotator is available. `human_label` is BLANK "
        "in every row of `label-validation-sample.jsonl` and awaits a human pass.",
        "",
        "## Interim signal: independent second-model cross-check",
        "",
        f"Independent grader: `{JUDGE_MODEL}` (General Compute; different model "
        "family from the original Claude labeller). This is a **bias probe, not "
        "a human substitute.**",
        "",
        f"| metric | value |",
        f"|---|---|",
        f"| pairs graded by both | {agree['n']} |",
        f"| exact-label agreement | {agree['exact']/n:.1%} |",
        f"| agreement within +/-1 | {agree['within_1']/n:.1%} |",
        f"| same relevant/not-relevant class (label>=2) | {agree['same_class']/n:.1%} |",
        f"| exact disagreements | {len(disagreements)} |",
        "",
        "### Disagreements (independent model vs original label)",
        "",
        "| query | candidate | orig | indep | indep reason |",
        "|---|---|---|---|---|",
    ]
    for r in disagreements[:40]:
        md.append(f"| {r['query'][:48]} | {r['candidate_name'][:34]} | "
                  f"{r['model_label']} | {r['independent_model_label']} | "
                  f"{r['independent_model_reason'][:70]} |")
    md += [
        "",
        "## Bias assessment",
        "",
        f"- Same-class agreement (the metric that actually drives precision/recall, "
        f"since 'relevant' := label>=2) is **{agree['same_class']/n:.1%}**.",
        "- A materially biased model-labelled benchmark would show low same-class "
        "agreement and a systematic direction (independent model consistently "
        "harsher or softer). Inspect the disagreement table for direction: "
        f"independent labelled LOWER in {sum(1 for r in disagreements if (r['independent_model_label'] or 0) < r['model_label'])} "
        f"cases, HIGHER in {sum(1 for r in disagreements if (r['independent_model_label'] or 0) > r['model_label'])}.",
        "",
        "## Records requiring human review",
        "",
        f"- **Minimum:** the {len(out)} pairs in `label-validation-sample.jsonl` "
        "(stratified representative sample).",
        f"- **Full set for a strong release claim:** all **855** labelled "
        "candidates in `tests/data/retrieval_eval_v1.jsonl`.",
        "",
        "## Instructions for the human reviewer",
        "",
        "1. Open `label-validation-sample.jsonl`. For each row, read `query` and "
        "look up the procedure by `candidate_name` (or its `retrieval_document`).",
        "2. Assign `human_label` in {0,1,2,3} per the rubric "
        "(0 irrelevant / 1 related-not-useful / 2 useful / 3 excellent). Add a "
        "one-line `human_reason`.",
        "3. Do NOT look at `model_label` or `independent_model_label` first.",
        "4. Re-run `scripts/eval_retrieval_quality.py --measure` after merging the "
        "human labels back into `retrieval_eval_v1.jsonl` to get human-anchored "
        "precision/recall and the human-anchored relevance threshold.",
        "",
        "## Release implication",
        "",
        "The relevance-gate threshold and the before/after precision numbers are "
        "currently derived from **model-assigned labels only**. They are adequate "
        "for engineering iteration and for the RELATIVE comparisons in this "
        "closure (old vs new representation, model vs model — the labels are held "
        "constant across those). They are **not** a sufficient basis for an "
        "absolute release-quality precision claim until the human pass above is "
        "done. This is a NAMED open item on the release gate.",
    ]
    _MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n-> {_SAMPLE}\n-> {_MD}")
    print(f"same-class agreement: {agree['same_class']/n:.1%}  exact: {agree['exact']/n:.1%}")


if __name__ == "__main__":
    asyncio.run(main())
