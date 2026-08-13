"""
Does the memory have anything useful in it? Answered offline, before any
agent runs, because it decides whether the experiment has headroom at all.

For each eval instance, compare the files its gold patch actually touched
against the files named by the top-k retrieved prior issues. Three numbers
matter and they answer different questions:

  ceiling  -- fraction of the instance's real files touched by ANY earlier
              issue. This is the best retrieval could ever do. If it is low,
              the corpus simply does not contain the answer and no retrieval
              method fixes that.
  top-k    -- what this retriever actually surfaces.
  gap      -- ceiling minus top-k, i.e. how much is lost to ranking rather
              than to absence.

Changelog fragments are excluded from "real files". Every ansible PR adds
one under changelogs/fragments/, its name is unique to that PR, no prior
issue can ever have touched it, and no test checks it. Counting them would
depress every score by a constant that has nothing to do with retrieval.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from knowledge import VoyageEmbedder, build_store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def real_files(files: list[str]) -> set[str]:
    return {f for f in files if not f.startswith("changelogs/")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--top-k", type=int, nargs="+", default=[5, 10])
    args = ap.parse_args()

    with open(os.path.join(HERE, "subset.json"), encoding="utf-8") as f:
        subset = json.load(f)

    embedder = VoyageEmbedder(os.path.join(args.work_dir, "voyage_cache.json"))
    rows = []

    for sample in subset["eval"]:
        truth = real_files(sample["files_touched"])
        store = build_store(subset["corpus"], sample["commit_date"], embedder)
        store.build_embeddings()

        corpus_files = {f for n in store.nodes for f in real_files(n.files_touched)}
        ceiling = len(truth & corpus_files) / max(1, len(truth))

        query = f"{sample['title']}\n\n{sample['problem_statement'][:1500]}"
        row = {"instance_id": sample["instance_id"], "title": sample["title"],
               "n_truth": len(truth), "corpus": len(store.nodes),
               "ceiling": ceiling}
        for k in args.top_k:
            hits = store.retrieve(query, top_k=k)
            got = {f for h in hits for f in real_files(h.files_touched)}
            row[f"recall@{k}"] = len(truth & got) / max(1, len(truth))
            row[f"nfiles@{k}"] = len(got)
        rows.append(row)

        ks = "  ".join(f"r@{k}={row[f'recall@{k}']:.0%}({row[f'nfiles@{k}']}f)"
                       for k in args.top_k)
        print(f"{sample['title'][:52]:52s} truth={len(truth):2d} "
              f"ceil={ceiling:4.0%}  {ks}")

    n = len(rows)
    print("\n" + "=" * 92)
    print(f"instances                        : {n}")
    print(f"mean ceiling (any prior issue)   : {sum(r['ceiling'] for r in rows) / n:.1%}")
    print(f"instances with ceiling > 0       : {sum(1 for r in rows if r['ceiling'] > 0)}/{n}")
    for k in args.top_k:
        mean = sum(r[f"recall@{k}"] for r in rows) / n
        hit = sum(1 for r in rows if r[f"recall@{k}"] > 0)
        print(f"mean recall@{k:<2d} / any-hit         : {mean:.1%}  ({hit}/{n} instances)")

    out = os.path.join(HERE, "retrieval_check.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
