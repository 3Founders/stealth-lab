"""
Experiment 7 -- can the golden patches of every OTHER task localize this one?

Leave-one-out over the whole corpus: for each of the 731 SWE-bench Pro
instances, the system may retrieve from the golden patches of all 730
others. The query is the current issue's problem_statement. Nothing is
executed and nothing is generated -- purely retrieval, which is what makes
it gradeable here (SWE-bench Pro ships patches, not a harness we can run).

WHAT COUNTS AS CORRECT

A retrieved patch is useful if it points at the FILES the current fix will
touch. That is the concrete form of the claim in
KNOWLEDGE_UPDATION_EXPERIMENT.md that the graph "indexes, doesn't
duplicate" -- it should not hand over a fix, it should hand over a
location.

  hit@k           any top-k patch touches >=1 file the gold patch touches
  file_recall@k   |files(top-k) ∩ gold| / |gold|  -- how much localization
  file_precision  |files(top-k) ∩ gold| / |files(top-k)| -- how much noise
  same_repo_rate  fraction of retrieved patches from the current repo

THE BASELINE THAT DECIDES THIS EXPERIMENT

Repositories have hot files. If many patches touch
`src/controllers/admin/users.js`, then "return the most frequently patched
files, ignoring the query" scores well while retrieving nothing. Two
query-blind controls run alongside every real method:

  random      k random other instances
  popularity  the k instances whose files are most commonly patched corpus-wide

Beating `random` proves nothing. The comparison that matters is against
`popularity`.

TWO SCOPES

  all   candidates are all 730 other instances. Cross-repo file paths
        essentially never collide, so this implicitly measures repo
        identification AND within-repo localization -- the realistic
        setting when one system holds everything.
  repo  candidates are other instances in the same repo only. An oracle on
        repo routing; the gap between the two is the cost of confusion
        across repositories.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from scipy.stats import binomtest

from experiments.after.local_embed import LocalEmbedder
from experiments.after.retrieval_methods import BM25, cosine_scores, rrf

FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)
MAX_CHARS = 2000  # matches scale_corpus.build_distractors so the embedding
                  # cache is shared rather than rebuilt

METHODS = ["random", "popularity", "bm25", "dense", "rrf_dense_bm25"]


def load_instances() -> list[dict]:
    import pandas as pd

    p = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/snapshots/*/data/*.parquet"))
    if not p:
        raise FileNotFoundError("SWE-bench Pro parquet not found in the HF cache")
    df = pd.read_parquet(p[0], columns=["repo", "instance_id", "patch", "problem_statement"])
    out = []
    for i, r in df.iterrows():
        files = sorted({m.group(2) for m in FILE_RE.finditer(str(r["patch"]))})
        if not files:
            continue
        out.append({
            "idx": int(i), "repo": str(r["repo"]), "instance_id": str(r["instance_id"]),
            "files": files, "text": str(r["problem_statement"])[:MAX_CHARS],
        })
    return out


def score_selection(selected: list[dict], gold: set[str], repo: str) -> dict:
    retrieved: set[str] = set()
    for s in selected:
        retrieved |= set(s["files"])
    inter = retrieved & gold
    return {
        "hit": bool(inter),
        "file_recall": len(inter) / len(gold) if gold else 0.0,
        "file_precision": len(inter) / len(retrieved) if retrieved else 0.0,
        "files_offered": len(retrieved),
        "same_repo": (sum(1 for s in selected if s["repo"] == repo) / len(selected)
                      if selected else 0.0),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, nargs="*", default=[1, 3, 5])
    ap.add_argument("--scope", choices=["all", "repo", "both"], default="both")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    inst = load_instances()
    N = len(inst)
    repos = sorted({i["repo"] for i in inst})
    print(f"{N} instances, {len(repos)} repos, "
          f"mean {statistics.mean(len(i['files']) for i in inst):.2f} gold files/instance")

    embedder = LocalEmbedder()
    print("\nembedding problem statements (local, cached) ...")
    vecs = np.vstack(await embedder.embed([i["text"] for i in inst], input_type="document"))
    print(f"  {embedder.stats()}")

    # Built once over the whole corpus, then the held-out document is masked
    # at scoring time. Rebuilding the index per query would change idf by one
    # document in 731 -- numerically irrelevant, and ~731x the work.
    print("building BM25 over the full corpus ...")
    bm = BM25([i["text"] for i in inst])
    repo_of = np.asarray([repos.index(i["repo"]) for i in inst])

    global_freq: Counter = Counter()
    for i in inst:
        global_freq.update(i["files"])
    pop_rank_all = np.asarray([sum(global_freq[f] for f in i["files"]) for i in inst], dtype=float)

    scopes = ["all", "repo"] if args.scope == "both" else [args.scope]
    results: dict[str, dict] = {}

    for scope in scopes:
        acc = {m: {k: [] for k in args.k} for m in METHODS}
        for qi, cur in enumerate(inst):
            gold = set(cur["files"])
            mask = np.ones(N, dtype=bool)
            mask[qi] = False                       # never retrieve itself
            if scope == "repo":
                mask &= (repo_of == repo_of[qi])
            cand = np.flatnonzero(mask)
            if len(cand) < max(args.k):
                continue

            bm_s = bm.scores(cur["text"])[cand]
            d_s = cosine_scores(vecs[qi], vecs[cand])
            bm_o = list(np.argsort(-bm_s))
            d_o = list(np.argsort(-d_s))
            f_o = list(np.argsort(-rrf([d_o, bm_o], len(cand))))
            pop_o = list(np.argsort(-pop_rank_all[cand]))
            rnd = rng.sample(range(len(cand)), min(max(args.k), len(cand)))

            for k in args.k:
                picks = {
                    "random": [inst[cand[i]] for i in rnd[:k]],
                    "popularity": [inst[cand[i]] for i in pop_o[:k]],
                    "bm25": [inst[cand[i]] for i in bm_o[:k]],
                    "dense": [inst[cand[i]] for i in d_o[:k]],
                    "rrf_dense_bm25": [inst[cand[i]] for i in f_o[:k]],
                }
                for m, sel in picks.items():
                    acc[m][k].append(score_selection(sel, gold, cur["repo"]))

        def agg(m: str, k: int) -> dict:
            rows = acc[m][k]
            return {
                "hit@k": round(sum(r["hit"] for r in rows) / len(rows), 4),
                "file_recall": round(statistics.mean(r["file_recall"] for r in rows), 4),
                "file_precision": round(statistics.mean(r["file_precision"] for r in rows), 4),
                "files_offered": round(statistics.mean(r["files_offered"] for r in rows), 1),
                "same_repo_rate": round(statistics.mean(r["same_repo"] for r in rows), 4),
            }

        def mcnemar(a: str, k: int, b: str = "popularity") -> dict:
            ra, rb = acc[a][k], acc[b][k]
            x = sum(1 for p, q in zip(ra, rb) if p["hit"] and not q["hit"])
            y = sum(1 for p, q in zip(ra, rb) if q["hit"] and not p["hit"])
            if x + y == 0:
                return {"discordant": 0, "p_value": 1.0}
            return {f"{a}_wins": x, f"{b}_wins": y, "discordant": x + y,
                    "p_value": round(float(binomtest(x, x + y, 0.5).pvalue), 8)}

        results[scope] = {
            "queries": len(acc["dense"][args.k[0]]),
            "methods": {m: {str(k): agg(m, k) for k in args.k} for m in METHODS},
            "vs_popularity": {m: {str(k): mcnemar(m, k) for k in args.k}
                              for m in METHODS if m != "popularity"},
        }

        print(f"\n=== scope={scope}, {results[scope]['queries']} queries ===")
        for k in args.k:
            print(f"\n-- k={k} --")
            print(f"{'method':<18}{'hit@k':>9}{'file_rec':>10}{'file_prec':>11}"
                  f"{'files':>8}{'same_repo':>11}{'p vs pop':>12}")
            for m in METHODS:
                a = agg(m, k)
                p = results[scope]["vs_popularity"].get(m, {}).get(str(k), {}).get("p_value")
                print(f"{m:<18}{a['hit@k']:>9.3f}{a['file_recall']:>10.3f}"
                      f"{a['file_precision']:>11.3f}{a['files_offered']:>8.1f}"
                      f"{a['same_repo_rate']:>11.3f}"
                      f"{(f'{p:.6f}' if p is not None else '-'):>12}")

    Path(__file__).parent.joinpath("results_exp7_swebench.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    print("\nwrote results_exp7_swebench.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
